import os from 'node:os';
import { readFile, statfs } from 'node:fs/promises';

export class Telemetry {
  private previous: { total: number; idle: number; rx: number; tx: number; time: number } | undefined;
  private history: object[] = [];
  private pending: Promise<object> | undefined;
  private last: object | undefined;
  private sampled = 0;
  read() {
    if (this.last && Date.now() - this.sampled < 5000) return Promise.resolve(this.last);
    if (this.pending) return this.pending;
    this.pending = this.sample().finally(() => { this.pending = undefined; }); return this.pending;
  }
  private async sample() {
    const cpus = os.cpus(), now = Date.now();
    const total = cpus.reduce((n, c) => n + Object.values(c.times).reduce((a, b) => a + b, 0), 0);
    const idle = cpus.reduce((n, c) => n + c.times.idle, 0);
    let rx = 0, tx = 0, networkAvailable = false;
    try {
      for (const line of (await readFile('/proc/net/dev', 'utf8')).split('\n').slice(2)) {
        const [name, raw] = line.split(':'); if (!raw || name.trim() === 'lo') continue;
        const fields = raw.trim().split(/\s+/).map(Number); rx += fields[0]; tx += fields[8];
      }
      networkAvailable = true;
    } catch { /* Missing Linux procfs is reported as unavailable. */ }
    const prev = this.previous, seconds = prev ? (now - prev.time) / 1000 : 0;
    const point = {
      at: new Date(now).toISOString(),
      cpu: prev && total > prev.total ? Math.max(0, Math.min(100, 100 * (1 - (idle - prev.idle) / (total - prev.total)))) : null,
      memory: Math.round(100 * (1 - os.freemem() / os.totalmem())),
      rx: networkAvailable && prev && rx >= prev.rx && seconds > 0 ? Math.round((rx - prev.rx) / seconds) : null,
      tx: networkAvailable && prev && tx >= prev.tx && seconds > 0 ? Math.round((tx - prev.tx) / seconds) : null,
    };
    let disk = null;
    try { const s = await statfs('/'); disk = { total: s.blocks * s.bsize, available: s.bavail * s.bsize }; } catch { /* Explicit null. */ }
    this.previous = { total, idle, rx, tx, time: now }; this.history.push(point); if (this.history.length > 120) this.history.shift();
    this.last = { ...point, history: [...this.history], uptime: Math.floor(os.uptime()), memoryTotal: os.totalmem(), disk, temperature: null, source: 'Admin process host; aggregate non-loopback network counters', staleAfterSeconds: 30 };
    this.sampled = now; return this.last;
  }
}
