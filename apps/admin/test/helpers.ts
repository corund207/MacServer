import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { promisify } from 'node:util';
import { execFile } from 'node:child_process';

// Only synthetic fixtures. Never load operator credentials or call tailscaled.
export async function tlsFixture() {
  const directory = await mkdtemp(join(tmpdir(), 'macserver-test-tls-'));
  try {
    await promisify(execFile)('/usr/bin/openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-noenc',
      '-keyout', join(directory, 'tls.key'), '-out', join(directory, 'tls.crt'), '-days', '1',
      '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1'], { timeout: 10_000 });
    return { directory, tls: { key: await readFile(join(directory, 'tls.key')), cert: await readFile(join(directory, 'tls.crt')) },
      close: () => rm(directory, { recursive: true, force: true }) };
  } catch (e) { await rm(directory, { recursive: true, force: true }); throw e; }
}
