import { test } from 'node:test';
import assert from 'node:assert/strict';
import { normalizeCollector } from '../src/collector.js';

test('collector rejects stale, future and malformed observations', () => {
  const now = Date.now();
  for (const observedAt of ['bad', new Date(now - 61000).toISOString(), new Date(now + 6000).toISOString()]) {
    assert.throws(() => normalizeCollector({ format: 1, observedAt, services: [] }, now));
  }
});
test('collector returns only curated fields and never invents service health', () => {
  const now = Date.now();
  const value = normalizeCollector({ format: 1, observedAt: new Date(now).toISOString(), secret: 'excluded',
    services: [{ name: 'db', state: 'healthy', detail: 'observed', secret: 'excluded' }], backup: { state: 'healthy', lastSuccess: new Date(now - 1000).toISOString() } }, now);
  assert.equal(value.services[0].name, 'PostgreSQL'); assert.equal(value.services[0].state, 'healthy');
  assert.equal(value.services[1].state, 'unavailable'); assert.ok(!JSON.stringify(value).includes('excluded'));
});
