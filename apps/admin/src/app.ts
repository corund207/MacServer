import Fastify from 'fastify';
import { readFile } from 'node:fs/promises';
import type { Identity } from './identity.js';
import { Limiter, Sessions, verify, type Credential } from './security.js';
import type { Audit } from './audit.js';
import type { Files } from './files.js';
import { Telemetry } from './telemetry.js';

type Options = {
  origin: string; credentials: Credential[]; identity: (ip: string) => Promise<Identity>;
  audit: Pick<Audit, 'record' | 'list'>; files?: Files; tls?: { key: Buffer; cert: Buffer };
  tailscale: () => Promise<{ state: string; addresses: string[] }>;
  now?: () => number;
};
const sessionCookie = '__Host-macserver';
function cookie(raw = '') { return raw.split(';').map(s => s.trim()).find(s => s.startsWith(sessionCookie + '='))?.slice(sessionCookie.length + 1) ?? ''; }
const fields = (body: unknown, keys: string[]) => !!body && typeof body === 'object' && !Array.isArray(body) && Object.keys(body).every(k => keys.includes(k));
export function createApp(options: Options) {
  const app = Fastify({ logger: false, trustProxy: false, bodyLimit: 4096, requestTimeout: 10_000, connectionTimeout: 10_000, keepAliveTimeout: 5000, ...(options.tls ? { https: options.tls } : {}) });
  const sessions = new Sessions(options.now), requests = new Limiter(180, 60_000, options.now), logins = new Limiter(5, 300_000, options.now), global = new Limiter(600, 60_000, options.now);
  const telemetry = new Telemetry(); let activeLogins = 0;
  const identities = new WeakMap<object, Identity>();
  app.addHook('onRequest', async (req, reply) => {
    reply.headers({ 'content-security-policy': "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'", 'x-content-type-options': 'nosniff', 'x-frame-options': 'DENY', 'referrer-policy': 'no-referrer', 'permissions-policy': 'camera=(), microphone=(), geolocation=()', 'strict-transport-security': 'max-age=31536000', 'cache-control': 'no-store', 'cross-origin-resource-policy': 'same-origin' });
    if (req.headers.host !== new URL(options.origin).host) return reply.code(421).send({ error: 'Unexpected host' });
    if (!global.allow('all') || !requests.allow(req.ip)) return reply.code(429).send({ error: 'Request limit reached' });
    if (Object.keys(req.headers).some(h => h.startsWith('x-forwarded-') || h.startsWith('tailscale-') || h === 'forwarded')) return reply.code(403).send({ error: 'Proxy context denied' });
    try {
      const identity = await options.identity(req.ip);
      if (!options.credentials.some(c => c.userId === identity.userId)) return reply.code(403).send({ error: 'Access denied' });
      identities.set(req, identity);
    } catch { return reply.code(403).send({ error: 'Verified Tailscale identity required' }); }
    if (!['GET', 'HEAD'].includes(req.method) && (req.headers.origin !== options.origin || req.headers['content-type'] !== 'application/json' || req.headers['x-admin-request'] !== '1')) return reply.code(403).send({ error: 'Request verification failed' });
    const path = req.url.split('?')[0];
    if (['/', '/app.js', '/style.css', '/api/login'].includes(path)) return;
    const session = sessions.get(cookie(req.headers.cookie), identities.get(req)!);
    if (!session) return reply.code(401).send({ error: 'Sign in required' });
    if (!['GET', 'HEAD'].includes(req.method) && req.headers['x-csrf-token'] !== session.csrf) return reply.code(403).send({ error: 'Request verification failed' });
  });
  app.setErrorHandler((error, _req, reply) => {
    // Never expose exception text, filesystem paths, request bodies or credentials.
    const code = (error as { statusCode?: number }).statusCode;
    reply.code(code && code >= 400 && code < 500 ? code : 503).send({ error: 'Request could not be completed' });
  });
  app.get('/', async (_req, reply) => reply.type('text/html').send(await readFile(new URL('../../public/index.html', import.meta.url), 'utf8')));
  app.get('/style.css', async (_req, reply) => reply.type('text/css').send(await readFile(new URL('../../public/style.css', import.meta.url), 'utf8')));
  app.get('/app.js', async (_req, reply) => reply.type('text/javascript').send(await readFile(new URL('../public/app.js', import.meta.url), 'utf8')));
  app.post('/api/login', async (req, reply) => {
    const identity = identities.get(req)!, body = req.body as { secret?: unknown; confirm?: unknown };
    if (!fields(body, ['secret', 'confirm']) || body.confirm !== true || typeof body.secret !== 'string' || !/^[a-zA-Z0-9_-]{43}$/.test(body.secret)) return reply.code(400).send({ error: 'Invalid sign-in request' });
    if (!logins.allow(identity.userId) || activeLogins >= 2) return reply.code(429).send({ error: 'Sign-in limit reached; try later' });
    activeLogins++;
    let valid: boolean;
    try { valid = await verify(options.credentials.find(c => c.userId === identity.userId)!, body.secret); } finally { activeLogins--; }
    await options.audit.record(identity.userId, valid ? 'login' : 'login-denied', valid ? 'ok' : 'denied');
    if (!valid) return reply.code(401).send({ error: 'Access denied' });
    sessions.delete(cookie(req.headers.cookie));
    const s = sessions.create(identity);
    reply.header('set-cookie', `${sessionCookie}=${s.id}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=900`);
    return { csrf: s.csrf, expires: s.expires };
  });
  app.get('/api/session', async req => { const s = sessions.get(cookie(req.headers.cookie), identities.get(req)!)!; return { csrf: s.csrf, expires: s.expires }; });
  app.post('/api/logout', async (req, reply) => {
    if (!fields(req.body, ['confirm']) || (req.body as any).confirm !== true) return reply.code(400).send({ error: 'Confirmation required' });
    // Invalidate even when the audit sink fails; a failed logout must not preserve access.
    sessions.delete(cookie(req.headers.cookie));
    reply.header('set-cookie', `${sessionCookie}=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0`);
    await options.audit.record(identities.get(req)!.userId, 'logout', 'ok'); return { signedOut: true };
  });
  app.get('/api/overview', async () => {
    let tailscale: object = { state: 'Unavailable', addresses: [] };
    try { tailscale = await options.tailscale(); } catch { /* UI reports unavailable, never healthy. */ }
    return { metrics: await telemetry.read(), tailscale, filesEnabled: !!options.files,
      alerts: ['Encrypted backup is repository-ready but not target-qualified.', 'Service health, database access and privileged operations are not connected.'],
      services: ['PostgreSQL', 'Auth', 'PostgREST', 'Envoy', 'Functions', 'Storage', 'Realtime', 'Studio'].map(name => ({ name, state: 'Unavailable', detail: 'No read-only collector configured' })),
      capabilities: { fileRead: !!options.files, fileWrite: false, sql: false, backup: false, restore: false, update: false, restart: false } };
  });
  app.get('/api/audit', async req => {
    const q = req.query as Record<string, unknown>;
    if (Object.keys(q).some(k => k !== 'outcome') || (q.outcome !== undefined && !['ok', 'denied'].includes(String(q.outcome)))) throw Object.assign(new Error(), { statusCode: 400 });
    return { events: options.audit.list().filter(e => !q.outcome || e.outcome === q.outcome), scope: 'Latest 200 events since process start; durable history is in the private audit file.' };
  });
  app.get('/api/files', async (req, reply) => {
    if (!options.files) return reply.code(503).send({ error: 'No curated file root configured' });
    const q = req.query as Record<string, unknown>;
    if (Object.keys(q).some(k => k !== 'path') || (q.path !== undefined && typeof q.path !== 'string')) return reply.code(400).send({ error: 'Invalid path' });
    try {
      const result = await options.files.list(String(q.path ?? ''));
      await options.audit.record(identities.get(req)!.userId, 'file-list', 'ok'); return result;
    } catch {
      await options.audit.record(identities.get(req)!.userId, 'file-denied', 'denied');
      return reply.code(503).send({ error: 'File listing unavailable or path denied' });
    }
  });
  app.get('/api/files/download', async (req, reply) => {
    if (!options.files) return reply.code(503).send({ error: 'No curated file root configured' });
    const q = req.query as Record<string, unknown>;
    if (Object.keys(q).some(k => k !== 'path') || typeof q.path !== 'string') return reply.code(400).send({ error: 'Invalid path' });
    try {
      const data = await options.files.download(q.path);
      await options.audit.record(identities.get(req)!.userId, 'file-download', 'ok');
      return reply.header('content-disposition', 'attachment; filename="admin-export.txt"').type('application/octet-stream').send(data);
    } catch {
      await options.audit.record(identities.get(req)!.userId, 'file-denied', 'denied');
      return reply.code(503).send({ error: 'File unavailable or path denied' });
    }
  });
  for (const operation of ['sql', 'backup', 'restore', 'update', 'restart', 'upload', 'delete']) {
    app.post(`/api/operations/${operation}`, async (req, reply) => {
      await options.audit.record(identities.get(req)!.userId, 'operation-denied', 'denied');
      return reply.code(501).send({ error: 'Operation unavailable: authorization, helper and recovery gates are not implemented' });
    });
  }
  return app;
}
