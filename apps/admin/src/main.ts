import { readFile } from 'node:fs/promises';
import { createApp } from './app.js';
import { assertBind, tailscaleStatus, whois } from './identity.js';
import { loadCredentials } from './security.js';
import { Audit } from './audit.js';
import { Files } from './files.js';

async function start() {
  const env = (name: string) => { const v = process.env[name]; if (!v) throw new Error(`Missing ${name}`); return v; };
  const host = env('ADMIN_BIND'), port = Number(env('ADMIN_PORT')), origin = env('ADMIN_ORIGIN');
  const u = new URL(origin);
  if (u.protocol !== 'https:' || u.origin !== origin || u.username || u.password || !Number.isInteger(port) || port < 1024 || port > 65535 || Number(u.port || 443) !== port || !u.hostname.endsWith('.ts.net')) throw new Error('Invalid private HTTPS configuration');
  const status = await tailscaleStatus(); assertBind(host, status.addresses);
  const credentials = await loadCredentials(env('ADMIN_CREDENTIALS_FILE'));
  const audit = await Audit.create(env('ADMIN_AUDIT_FILE'));
  const files = process.env.ADMIN_FILE_ROOT ? await Files.create(process.env.ADMIN_FILE_ROOT) : undefined;
  const app = createApp({ origin, credentials, audit, files, identity: whois, tailscale: tailscaleStatus,
    tls: { key: await readFile(env('ADMIN_TLS_KEY')), cert: await readFile(env('ADMIN_TLS_CERT')) } });
  await app.listen({ host, port });
  const stop = async () => { await app.close(); await files?.close(); await audit.close(); process.exit(0); };
  process.on('SIGTERM', stop); process.on('SIGINT', stop);
  process.stdout.write('Private admin HTTPS listener started; privileged operations disabled.\n');
}
start().catch(() => { process.stderr.write('Admin startup refused. Verify private bind, Tailscale, TLS and protected configuration.\n'); process.exit(1); });
