import { createServer } from 'node:http';
import { createHash, randomUUID } from 'node:crypto';

const NAME = /^[a-z][a-z0-9_]{0,62}$/;
const METHODS = ['GET', 'HEAD', 'POST', 'PATCH', 'DELETE'];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export function validateConfig(value) {
  if (value?.format !== 1 || !Array.isArray(value.apps) || !value.apps.length || value.apps.length > 50) throw Error('Invalid apps');
  const keys = new Set(), ids = new Set();
  for (const app of value.apps) {
    if (!NAME.test(app.id) || ids.has(app.id) || !/^ms_pub_[A-Za-z0-9_-]{43}$/.test(app.key) || keys.has(app.key)) throw Error('Invalid app identity');
    ids.add(app.id); keys.add(app.key);
    if (!Array.isArray(app.origins) || !app.origins.length || app.origins.length > 10) throw Error('Invalid origins');
    for (const origin of app.origins) {
      const url = new URL(origin);
      if (url.protocol !== 'https:' || url.origin !== origin || url.username || url.password) throw Error('HTTPS origins required');
    }
    if (!app.tables || !Object.keys(app.tables).length || Object.keys(app.tables).length > 100) throw Error('Table allowlist required');
    for (const [table, methods] of Object.entries(app.tables)) {
      if (!NAME.test(table) || ['rpc', 'admin'].includes(table) || !Array.isArray(methods) || !methods.length || methods.some(m => !METHODS.includes(m))) throw Error('Invalid table scope');
    }
    if (!Number.isInteger(app.requestsPerMinute) || app.requestsPerMinute < 1 || app.requestsPerMinute > 1000) throw Error('Invalid rate limit');
  }
  return value;
}

function claims(token) {
  try {
    if (token.length > 8192 || token.split('.').length !== 3) return null;
    const value = JSON.parse(Buffer.from(token.split('.')[1], 'base64url').toString('utf8'));
    // Unverified claims only reject elevated/malformed credentials. Auth verifies the token.
    if (value.role !== 'authenticated' || !UUID.test(value.sub) || !Number.isFinite(value.exp) || value.exp * 1000 <= Date.now()) return null;
    return value;
  } catch { return null; }
}
function queryAllowed(search) {
  if (search.length > 4096) return false;
  const seen = new Set();
  for (const [key, value] of new URLSearchParams(search)) {
    if (seen.has(key)) return false;
    seen.add(key);
    if (key === 'select' && !/^[a-zA-Z0-9_,*]+$/.test(value)) return false;
    else if (key === 'order' && !/^[a-zA-Z0-9_,.]+$/.test(value)) return false;
    else if (['limit', 'offset'].includes(key) && (!/^\d{1,6}$/.test(value) || (key === 'limit' && Number(value) > 1000))) return false;
    else if (key === 'on_conflict' && !/^[a-zA-Z0-9_,]+$/.test(value)) return false;
    else if (!['select', 'order', 'limit', 'offset', 'on_conflict'].includes(key) &&
      (!NAME.test(key) || !/^(eq|neq|gt|gte|lt|lte|like|ilike|is|in)\./.test(value))) return false;
  }
  return true;
}
export function routeFor(raw, method, app) {
  if (typeof raw !== 'string' || raw.length > 8192 || !raw.startsWith('/') || raw.startsWith('//')) return null;
  const [path, ...queryParts] = raw.split('?');
  if (queryParts.length > 1 || /[%\\\x00-\x20]/.test(path)) return null;
  const search = queryParts.length ? '?' + queryParts[0] : '';
  const table = /^\/rest\/v1\/([a-z][a-z0-9_]{0,62})$/.exec(path)?.[1];
  if (table && Object.hasOwn(app.tables, table) && app.tables[table].includes(method) && queryAllowed(search)) return { kind: 'data', id: 'table', path: '/' + table + search };
  if (method === 'POST' && path === '/auth/v1/token' && /^\?grant_type=(password|refresh_token)$/.test(search)) return { kind: 'auth', id: 'token', path: '/token' + search };
  if (!search && method === 'GET' && path === '/auth/v1/user') return { kind: 'auth', id: 'user', path: '/user', session: true };
  if (!search && method === 'POST' && path === '/auth/v1/logout') return { kind: 'auth', id: 'logout', path: '/logout', session: true };
  return null;
}

class Budget {
  constructor(limit, now) { this.limit = limit; this.now = now; this.start = 0; this.count = 0; }
  take() { const now = this.now(); if (now - this.start >= 60000) { this.start = now; this.count = 0; } return ++this.count <= this.limit; }
}
async function readBody(req, max) {
  if (Number(req.headers['content-length'] || 0) > max) throw Object.assign(Error(), { status: 413 });
  const chunks = []; let size = 0;
  for await (const chunk of req) { size += chunk.length; if (size > max) throw Object.assign(Error(), { status: 413 }); chunks.push(chunk); }
  return Buffer.concat(chunks);
}
async function responseBytes(response, max) {
  const reader = response.body?.getReader(); if (!reader) return Buffer.alloc(0);
  const chunks = []; let size = 0;
  try { while (true) { const { done, value } = await reader.read(); if (done) break; size += value.length; if (size > max) throw Error('Response too large'); chunks.push(value); } }
  finally { await reader.cancel(); }
  return Buffer.concat(chunks);
}

export function createGateway(config, { fetcher = fetch, audit = event => {
  if (process.stdout.writableLength > 65536) throw Error('Audit backpressure');
  process.stdout.write(JSON.stringify(event) + '\n');
}, now = Date.now, authURL = 'http://auth:9999', restURL = 'http://rest:3000' } = {}) {
  validateConfig(config);
  const apps = new Map(config.apps.map(app => [createHash('sha256').update(app.key).digest('hex'), app]));
  const budgets = new Map(config.apps.map(app => [app.id, new Budget(app.requestsPerMinute, now)]));
  const global = new Budget(3000, now), authBudget = new Budget(60, now);
  let active = 0;
  const server = createServer({ maxHeaderSize: 16384, requestTimeout: 15000, headersTimeout: 10000, keepAliveTimeout: 5000 }, async (req, res) => {
    const id = randomUUID(); let app, route;
    const finish = (status, message) => {
      try { audit({ at: new Date(now()).toISOString(), id, app: app?.id || null, route: route?.id || 'denied', status }); }
      catch { status = 503; message = 'Audit unavailable'; }
      res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store', 'x-content-type-options': 'nosniff', 'x-request-id': id });
      res.end(JSON.stringify({ error: message, requestId: id }));
    };
    if (req.url === '/healthz' && req.method === 'GET' && ['127.0.0.1', '::1', '::ffff:127.0.0.1'].includes(req.socket.remoteAddress)) return res.writeHead(200).end('ok');
    if (!global.take() || active >= 32) return finish(429, 'Request budget exceeded');
    const origin = req.headers.origin;
    // CORS is browser policy only. Preflight carries no credential and grants no data access.
    if (req.method === 'OPTIONS') {
      const method = req.headers['access-control-request-method'];
      const permitted = config.apps.some(candidate => candidate.origins.includes(origin) && routeFor(req.url, method, candidate));
      const requested = String(req.headers['access-control-request-headers'] || '').toLowerCase().split(',').map(x => x.trim()).filter(Boolean);
      if (!permitted || requested.some(h => !['apikey', 'authorization', 'content-type', 'accept', 'prefer', 'x-client-info', 'accept-profile', 'content-profile'].includes(h))) return finish(403, 'Preflight denied');
      res.writeHead(204, { 'access-control-allow-origin': origin, 'access-control-allow-methods': method, 'access-control-allow-headers': requested.join(', '), vary: 'Origin, Access-Control-Request-Method, Access-Control-Request-Headers', 'access-control-max-age': '300' }); return res.end();
    }
    const key = req.headers.apikey;
    if (typeof key !== 'string' || key.length > 128) return finish(401, 'App credential required');
    app = apps.get(createHash('sha256').update(key).digest('hex'));
    if (!app) return finish(401, 'App credential denied');
    if (origin && !app.origins.includes(origin)) return finish(403, 'Origin not allowed');
    if (origin) { res.setHeader('access-control-allow-origin', origin); res.setHeader('vary', 'Origin'); res.setHeader('access-control-expose-headers', 'Content-Range, X-Request-Id'); }
    if (!budgets.get(app.id).take()) return finish(429, 'App request budget exceeded');
    route = routeFor(req.url, req.method, app);
    if (!route) return finish(404, 'Route not available');
    if (['accept-profile', 'content-profile'].some(h => req.headers[h] && req.headers[h] !== 'api')) return finish(403, 'Schema not allowed');
    if (route.kind === 'auth' && !authBudget.take()) return finish(429, 'Authentication request budget exceeded');
    active++;
    const controller = new AbortController();
    const timeout = setTimeout(() => { controller.abort(); if (!res.writableEnded) res.destroy(); req.destroy(); }, 15000);
    res.on('close', () => { if (!res.writableEnded) controller.abort(); });
    try {
      const headers = { accept: 'application/json' };
      if (route.kind === 'data' || route.session) {
        const auth = req.headers.authorization;
        if (typeof auth !== 'string' || !auth.startsWith('Bearer ')) return finish(401, 'User session required');
        const token = auth.slice(7), decoded = claims(token);
        if (!decoded) return finish(401, 'User session denied');
        const checked = await fetcher(authURL + '/user', { headers: { authorization: auth }, signal: controller.signal, redirect: 'error' });
        if (!checked.ok) { await checked.body?.cancel(); return finish(401, 'User session denied'); }
        const user = JSON.parse((await responseBytes(checked, 65536)).toString());
        if (user.id !== decoded.sub) return finish(401, 'User session denied');
        headers.authorization = auth;
      }
      if (route.kind === 'data') { headers['accept-profile'] = 'api'; headers['content-profile'] = 'api'; }
      if (req.headers.prefer) {
        const preferences = req.headers.prefer.split(',').map(s => s.trim());
        if (preferences.some(p => !['return=representation', 'return=minimal', 'count=exact', 'count=planned', 'count=estimated', 'resolution=merge-duplicates', 'resolution=ignore-duplicates'].includes(p))) return finish(400, 'Preference not supported');
        headers.prefer = preferences.join(',');
      }
      let body;
      if (['POST', 'PATCH', 'DELETE'].includes(req.method)) {
        const bytes = await readBody(req, 1024 * 1024);
        if (bytes.length) {
          if (!String(req.headers['content-type']).toLowerCase().startsWith('application/json')) return finish(415, 'JSON required');
          try { JSON.parse(bytes.toString()); } catch { return finish(400, 'Invalid JSON'); }
          body = bytes; headers['content-type'] = 'application/json';
        }
      }
      audit({ at: new Date(now()).toISOString(), id, app: app.id, route: route.id, stage: 'authorized', status: 102 });
      const upstream = await fetcher((route.kind === 'data' ? restURL : authURL) + route.path, { method: req.method, headers, body, signal: controller.signal, redirect: 'error' });
      if (!upstream.ok) { await upstream.body?.cancel(); return finish(upstream.status >= 500 ? 502 : upstream.status, 'Request rejected by backend'); }
      const bytes = await responseBytes(upstream, 2 * 1024 * 1024);
      audit({ at: new Date(now()).toISOString(), id, app: app.id, route: route.id, status: upstream.status });
      const outgoing = { 'content-type': 'application/json', 'cache-control': 'no-store', 'x-content-type-options': 'nosniff', 'x-request-id': id };
      if (upstream.headers.has('content-range')) outgoing['content-range'] = upstream.headers.get('content-range');
      res.writeHead(upstream.status, outgoing); res.end(bytes);
    } catch (error) { if (!res.headersSent && !res.destroyed) finish(error.status || 502, 'Request unavailable'); }
    finally { clearTimeout(timeout); active--; }
  });
  server.maxConnections = 128;
  server.on('upgrade', (_req, socket) => socket.destroy());
  return server;
}
