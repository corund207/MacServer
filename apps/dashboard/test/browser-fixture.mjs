import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createDashboard } from '../src/server.mjs';

const directory = await mkdtemp(join(tmpdir(), 'macserver-dashboard-browser-'));
const statusFile = join(directory, 'status.json');
const observedAt = new Date().toISOString();
await writeFile(statusFile, JSON.stringify({ format: 1, observedAt, collector: 'Synthetic browser fixture',
  tailscale: { state: 'healthy', detail: 'Synthetic connected state' },
  backup: { state: 'degraded', detail: 'Synthetic restore drill overdue', lastSuccess: observedAt },
  requests: { state: 'healthy', perMinute: 18, errorsPerMinute: 1, detail: 'Synthetic 60-second window' },
  services: [
    { name: 'PostgreSQL', state: 'healthy', detail: 'Synthetic health check passed' },
    { name: 'Auth', state: 'healthy', detail: 'Synthetic health check passed' },
    { name: 'PostgREST', state: 'degraded', detail: 'Synthetic latency threshold exceeded' },
    { name: 'Storage', state: 'unavailable', detail: 'Optional profile not observed' }
  ], alerts: [{ severity: 'warning', title: 'Synthetic restore drill overdue', detail: 'Fixture data for visual verification only.', at: observedAt }] }), { mode: 0o600 });
let sample = 0;
const metrics = { read: async () => { sample++; return { at: new Date().toISOString(), cpuPercent: 22 + sample,
  memoryPercent: 48, receiveBytesPerSecond: 280000, sendBytesPerSecond: 90000, memoryTotalBytes: 8 * 1024 ** 3,
  disk: { totalBytes: 120 * 1024 ** 3, availableBytes: 72 * 1024 ** 3 }, uptimeSeconds: 234567,
  temperatureCelsius: null, source: 'Synthetic browser fixture', history: Array.from({ length: 18 }, (_, index) => ({
    at: new Date().toISOString(), cpuPercent: 18 + index * 1.5, memoryPercent: 42 + index / 3 })) }; } };
const server = createDashboard({ statusFile, metrics }); server.listen(7461, '127.0.0.1');
server.on('listening', () => process.stdout.write(`READY http://127.0.0.1:${server.address().port}\n`));
const stop = () => server.close(async () => { await rm(directory, { recursive: true }); process.exit(0); });
process.on('SIGTERM', stop); process.on('SIGINT', stop);
