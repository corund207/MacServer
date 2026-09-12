import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { HostMetrics, readStatus } from './metrics.mjs';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', 'public');
const assets = new Map([
  ['/', ['index.html', 'text/html; charset=utf-8']],
  ['/app.js', ['app.js', 'text/javascript; charset=utf-8']],
  ['/style.css', ['style.css', 'text/css; charset=utf-8']]
]);
const headers = {
  'cache-control': 'no-store',
  'content-security-policy': "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
  'cross-origin-resource-policy': 'same-origin',
  'permissions-policy': 'camera=(), microphone=(), geolocation=(), payment=(), usb=()',
  'referrer-policy': 'no-referrer',
  'x-content-type-options': 'nosniff',
  'x-frame-options': 'DENY'
};

export function assertLoopback(value) {
  if (!['127.0.0.1', '::1'].includes(value)) throw new Error('Dashboard must bind to loopback');
  return value;
}

export function createDashboard({ statusFile, metrics = new HostMetrics(), now = Date.now }) {
  return createServer(async (request, response) => {
    const url = new URL(request.url ?? '/', 'http://localhost');
    const finish = (code, type, body) => {
      response.writeHead(code, { ...headers, 'content-type': type, 'content-length': Buffer.byteLength(body) });
      response.end(body);
    };
    if (!['GET', 'HEAD'].includes(request.method ?? '')) return finish(405, 'application/json', '{"error":"Read-only endpoint"}\n');
    if (url.search || url.hash) return finish(400, 'application/json', '{"error":"Unexpected request"}\n');
    if (url.pathname === '/api/status') {
      const body = JSON.stringify({ generatedAt: new Date(now()).toISOString(), host: await metrics.read(now()),
        evidence: await readStatus(statusFile, now()) }) + '\n';
      return finish(200, 'application/json', request.method === 'HEAD' ? '' : body);
    }
    if (url.pathname === '/healthz') return finish(200, 'application/json', request.method === 'HEAD' ? '' : '{"status":"serving"}\n');
    const asset = assets.get(url.pathname);
    if (!asset) return finish(404, 'application/json', '{"error":"Not found"}\n');
    try {
      const body = await readFile(join(root, asset[0]));
      response.writeHead(200, { ...headers, 'content-type': asset[1], 'content-length': body.length });
      response.end(request.method === 'HEAD' ? undefined : body);
    } catch { finish(503, 'application/json', '{"error":"Display unavailable"}\n'); }
  });
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  try {
    const host = assertLoopback(process.env.MACSERVER_DASHBOARD_BIND ?? '127.0.0.1');
    const port = Number(process.env.MACSERVER_DASHBOARD_PORT ?? '7460');
    const statusFile = process.env.MACSERVER_STATUS_FILE ?? '/run/macserver/status.json';
    if (!Number.isInteger(port) || port < 1024 || port > 65535 || !statusFile.startsWith('/') || statusFile.length > 512) throw new Error('Invalid configuration');
    const server = createDashboard({ statusFile });
    server.listen(port, host, () => process.stdout.write(`Local dashboard listening on ${host}:${port}\n`));
    const stop = () => server.close(() => process.exit(0));
    process.on('SIGINT', stop); process.on('SIGTERM', stop);
  } catch {
    process.stderr.write('Dashboard startup refused; verify loopback bind, port, and absolute status path.\n');
    process.exitCode = 1;
  }
}
