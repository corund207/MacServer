import { createApp } from '../build/src/app.js';
import { credential, token } from '../build/src/security.js';
import { Audit } from '../build/src/audit.js';
import { Files } from '../build/src/files.js';
import { tlsFixture } from '../build/test/helpers.js';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

// Test-only listener. This module is not included in the production build or startup.
export async function browserFixture() {
  const fixture = await tlsFixture(), secret = token();
  await mkdir(join(fixture.directory, 'exports'));
  await mkdir(join(fixture.directory, 'exports/reports'));
  await writeFile(join(fixture.directory, 'exports/reports/status.txt'), 'Synthetic browser test export\n');
  const files = await Files.create(join(fixture.directory, 'exports'));
  const audit = await Audit.create(join(fixture.directory, 'audit.jsonl'));
  const options = { origin: 'https://127.0.0.1:0', tls: fixture.tls, credentials: [await credential('123', secret)],
    identity: async ip => { if (ip !== '127.0.0.1') throw new Error('Fixture is loopback-only'); return { userId: '123', nodeId: 'test-node' }; },
    files, audit, tailscale: async () => { throw new Error('No real tailnet in fixture'); } };
  const app = createApp(options);
  await app.listen({ host: '127.0.0.1', port: 0 });
  options.origin = `https://127.0.0.1:${app.server.address().port}`;
  return { url: options.origin, secret, app, close: async () => { await app.close(); await files.close(); await audit.close(); await fixture.close(); } };
}
