import os from 'node:os';
import { readFile, statfs } from 'node:fs/promises';

const MAX_STATUS_BYTES = 128 * 1024;
const ALLOWED_STATES = new Set(['healthy', 'degraded', 'failed', 'unavailable']);

export function sanitizeStatus(value, now = Date.now()) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || value.format !== 1 ||
      typeof value.observedAt !== 'string' || !Number.isFinite(Date.parse(value.observedAt))) {
    throw new Error('Invalid status snapshot');
  }
  const text = (candidate, limit = 160) => typeof candidate === 'string' && candidate.length <= limit ? candidate : '';
  const state = candidate => ALLOWED_STATES.has(candidate) ? candidate : 'unavailable';
  const services = Array.isArray(value.services) ? value.services.slice(0, 24).map(item => ({
    name: text(item?.name, 48) || 'Unnamed service', state: state(item?.state), detail: text(item?.detail)
  })) : [];
  const alerts = Array.isArray(value.alerts) ? value.alerts.slice(0, 20).map(item => ({
    severity: ['critical', 'warning', 'info'].includes(item?.severity) ? item.severity : 'warning',
    title: text(item?.title, 100) || 'Status requires review', detail: text(item?.detail, 240),
    at: typeof item?.at === 'string' && Number.isFinite(Date.parse(item.at)) ? item.at : value.observedAt
  })) : [];
  const number = candidate => typeof candidate === 'number' && Number.isFinite(candidate) && candidate >= 0 ? candidate : null;
  const observed = Date.parse(value.observedAt);
  return {
    observedAt: value.observedAt,
    ageSeconds: Math.max(0, Math.floor((now - observed) / 1000)),
    stale: observed - now > 30_000 || now - observed > 60_000,
    collector: text(value.collector, 80) || 'unspecified collector',
    tailscale: { state: state(value.tailscale?.state), detail: text(value.tailscale?.detail) },
    backup: { state: state(value.backup?.state), detail: text(value.backup?.detail),
      lastSuccess: typeof value.backup?.lastSuccess === 'string' && Number.isFinite(Date.parse(value.backup.lastSuccess)) ? value.backup.lastSuccess : null },
    requests: { state: state(value.requests?.state), perMinute: number(value.requests?.perMinute),
      errorsPerMinute: number(value.requests?.errorsPerMinute), detail: text(value.requests?.detail) },
    services, alerts
  };
}

export async function readStatus(path, now = Date.now()) {
  try {
    const raw = await readFile(path);
    if (raw.length > MAX_STATUS_BYTES) throw new Error('Snapshot too large');
    return sanitizeStatus(JSON.parse(raw.toString('utf8')), now);
  } catch {
    return {
      observedAt: null, ageSeconds: null, stale: true, collector: 'unavailable',
      tailscale: { state: 'unavailable', detail: 'No valid collector snapshot' },
      backup: { state: 'unavailable', detail: 'No valid collector snapshot', lastSuccess: null },
      requests: { state: 'unavailable', perMinute: null, errorsPerMinute: null, detail: 'No valid collector snapshot' },
      services: [], alerts: [{ severity: 'warning', title: 'Collector evidence unavailable',
        detail: 'Check the local collector service and its private status snapshot.', at: new Date(now).toISOString() }]
    };
  }
}

export class HostMetrics {
  #previous;
  #history = [];
  #cached;
  #sampledAt = 0;

  async read(now = Date.now()) {
    if (this.#cached && now - this.#sampledAt < 4_000) return this.#cached;
    const cpus = os.cpus();
    const total = cpus.reduce((sum, cpu) => sum + Object.values(cpu.times).reduce((a, b) => a + b, 0), 0);
    const idle = cpus.reduce((sum, cpu) => sum + cpu.times.idle, 0);
    let rx = 0, tx = 0, network = false;
    try {
      for (const line of (await readFile('/proc/net/dev', 'utf8')).split('\n').slice(2)) {
        const [name, raw] = line.split(':');
        if (!raw || name.trim() === 'lo') continue;
        const columns = raw.trim().split(/\s+/).map(Number);
        rx += columns[0]; tx += columns[8];
      }
      network = true;
    } catch { /* A non-Linux development host reports unavailable network rates. */ }
    const prior = this.#previous;
    const elapsed = prior ? (now - prior.at) / 1000 : 0;
    const point = {
      at: new Date(now).toISOString(),
      cpuPercent: prior && total > prior.total ? Math.max(0, Math.min(100, 100 * (1 - (idle - prior.idle) / (total - prior.total)))) : null,
      memoryPercent: Math.round(100 * (1 - os.freemem() / os.totalmem())),
      receiveBytesPerSecond: network && prior && rx >= prior.rx && elapsed > 0 ? Math.round((rx - prior.rx) / elapsed) : null,
      sendBytesPerSecond: network && prior && tx >= prior.tx && elapsed > 0 ? Math.round((tx - prior.tx) / elapsed) : null
    };
    let disk = null;
    try {
      const value = await statfs('/');
      disk = { totalBytes: value.blocks * value.bsize, availableBytes: value.bavail * value.bsize };
    } catch { /* Explicitly null. */ }
    this.#previous = { total, idle, rx, tx, at: now };
    this.#history.push(point);
    if (this.#history.length > 90) this.#history.shift();
    this.#cached = { ...point, memoryTotalBytes: os.totalmem(), disk, uptimeSeconds: Math.floor(os.uptime()),
      temperatureCelsius: null, source: 'Dashboard process host; aggregate non-loopback network counters',
      history: [...this.#history] };
    this.#sampledAt = now;
    return this.#cached;
  }
}
