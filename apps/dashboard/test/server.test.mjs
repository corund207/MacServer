import assert from 'node:assert/strict';
import { once } from 'node:events';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { assertLoopback, createDashboard } from '../src/server.mjs';
import { readStatus, sanitizeStatus } from '../src/metrics.mjs';

const observedAt = '2026-09-11T12:00:00.000Z';
const snapshot = { format: 1, observedAt, collector: 'test collector', tailscale: { state: 'healthy', detail: 'Connected' },
  backup: { state: 'degraded', detail: 'Restore evidence old', lastSuccess: observedAt },
  requests: { state: 'healthy', perMinute: 12, errorsPerMinute: 0, detail: 'Window 60s' },
  services: [{ name: 'PostgreSQL', state: 'healthy', detail: 'Health check passed' }],
  alerts: [{ severity: 'warning', title: 'Restore evidence old', detail: 'Run an isolated drill', at: observedAt }] };

test('bind assertion allows loopback only', () => {
  assert.equal(assertLoopback('127.0.0.1'), '127.0.0.1');
  assert.equal(assertLoopback('::1'), '::1');
  for (const value of ['0.0.0.0', '100.64.0.1', 'localhost', '::']) assert.throws(() => assertLoopback(value));
});

test('status snapshot is bounded, normalized, and stale-aware', async () => {
  const now = Date.parse(observedAt) + 61_000;
  const value = sanitizeStatus({ ...snapshot, services: [...snapshot.services, ...Array(30).fill({ name: 'x', state: 'invented' })], unknown: 'ignored' }, now);
  assert.equal(value.stale, true); assert.equal(value.services.length, 24); assert.equal(value.services[1].state, 'unavailable');
  assert.equal(value.requests.perMinute, 12); assert.equal(value.unknown, undefined);
  const missing = await readStatus('/definitely/missing/status.json', now);
  assert.equal(missing.stale, true); assert.equal(missing.alerts[0].title, 'Collector evidence unavailable');
  assert.equal(sanitizeStatus(snapshot, Date.parse(observedAt) - 31_000).stale, true);
});

test('server exposes only static reads and a non-secret status API', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'macserver-dashboard-')), statusFile = join(directory, 'status.json');
  await writeFile(statusFile, JSON.stringify(snapshot), { mode: 0o600 });
  const metrics = { read: async () => ({ at: observedAt, cpuPercent: 10, memoryPercent: 20, history: [] }) };
  const server = createDashboard({ statusFile, metrics, now: () => Date.parse(observedAt) });
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  t.after(async () => { server.close(); await once(server, 'close'); await rm(directory, { recursive: true }); });
  const address = server.address(), base = `http://127.0.0.1:${address.port}`;
  const page = await fetch(base + '/'); assert.equal(page.status, 200); assert.match(page.headers.get('content-security-policy'), /default-src 'none'/);
  const api = await fetch(base + '/api/status'); const value = await api.json();
  assert.equal(api.status, 200); assert.equal(value.evidence.services[0].name, 'PostgreSQL');
  const mutation = await fetch(base + '/api/status', { method: 'POST' }); assert.equal(mutation.status, 405);
  assert.equal((await fetch(base + '/missing')).status, 404);
  assert.equal((await fetch(base + '/?unexpected=1')).status, 400);
});
