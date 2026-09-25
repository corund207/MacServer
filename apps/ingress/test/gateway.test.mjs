import { test } from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { clientKey, createGateway, routeFor, validateConfig } from '../src/gateway.mjs';

const user = '11111111-1111-4111-8111-111111111111';
const app = { id: 'vexvortex', key: 'ms_pub_' + 'a'.repeat(43), origins: ['https://app.example.test'], tables: { items: ['GET', 'POST', 'PATCH', 'DELETE'] }, requestsPerMinute: 100 };
const token = (role = 'authenticated', exp = Math.floor(Date.now() / 1000) + 300) => 'header.' + Buffer.from(JSON.stringify({ role, sub: user, exp })).toString('base64url') + '.signature';
const headers = () => ({ apikey: app.key, authorization: 'Bearer ' + token(), 'content-type': 'application/json' });
async function fixture(t, options = {}) {
  const calls = [], events = [];
  const gateway = createGateway({ format: 1, apps: [structuredClone(app)] }, {
    audit: event => events.push(event),
    fetcher: async (url, init) => { calls.push({ url, init }); return new Response(JSON.stringify(url.endsWith('/user') ? { id: user } : [{ id: 1 }]), { status: 200 }); },
    ...options
  });
  gateway.listen(0, '127.0.0.1'); await once(gateway, 'listening');
  t.after(() => { gateway.closeAllConnections(); gateway.close(); });
  return { url: 'http://127.0.0.1:' + gateway.address().port, calls, events };
}
test('configuration refuses wildcard origins, broad scopes and duplicate keys', () => {
  for (const patch of [{ origins: ['*'] }, { origins: ['http://app.example.test'] }, { tables: { rpc: ['POST'] } }, { tables: { items: ['TRACE'] } }, { requestsPerMinute: 0 }]) {
    assert.throws(() => validateConfig({ format: 1, apps: [{ ...app, ...patch }] }));
  }
  assert.throws(() => validateConfig({ format: 1, apps: [app, { ...app, id: 'other' }] }));
});
test('route boundary denies admin, encoded paths, RPC, joins and schema escapes', () => {
  for (const path of ['/', '/studio', '/metrics', '/auth/v1/admin/users', '/rest/v1/rpc/run', '/rest/v1/other', '/rest/v1/%69tems', '//rest/v1/items', '/rest/v1/items/..', '/rest/v1/items?select=*,users(*)', '/rest/v1/items?select=secret:id', '/rest/v1/items?limit=99999', '/rest/v1/items?select=*&select=id']) {
    assert.equal(routeFor(path, 'GET', app), null, path);
  }
  assert.ok(routeFor('/rest/v1/items?select=id&owner_id=eq.' + user, 'GET', app));
});
test('forged headers and public app key never substitute for a user session', async t => {
  const f = await fixture(t);
  for (const h of [{}, { apikey: app.key }, { apikey: app.key, origin: app.origins[0], referer: app.origins[0], 'user-agent': 'ApprovedApp', 'x-forwarded-for': '127.0.0.1' }, { ...headers(), authorization: 'Bearer ' + token('service_role') }, { ...headers(), authorization: 'Bearer ' + token('authenticated', 1) }]) {
    assert.equal((await fetch(f.url + '/rest/v1/items', { headers: h })).status, 401);
  }
  assert.equal(f.calls.length, 0);
});
test('forged JWT and mismatched Auth identity are denied before database access', async t => {
  for (const response of [new Response('{}', { status: 401 }), new Response(JSON.stringify({ id: 'different' }))]) {
    const f = await fixture(t, { fetcher: async () => response });
    assert.equal((await fetch(f.url + '/rest/v1/items', { headers: headers() })).status, 401);
  }
});
test('authorized request uses fixed upstream, user token, api schema and bounded headers', async t => {
  const f = await fixture(t);
  const result = await fetch(f.url + '/rest/v1/items?select=id', { headers: { ...headers(), origin: app.origins[0], 'x-forwarded-host': 'evil.test', cookie: 'secret', 'x-client-info': 'untrusted' } });
  assert.equal(result.status, 200); assert.deepEqual(await result.json(), [{ id: 1 }]);
  assert.equal(f.calls.length, 2); assert.equal(f.calls[1].url, 'http://rest:3000/items?select=id');
  assert.equal(f.calls[1].init.headers['accept-profile'], 'api');
  assert.equal(f.calls[1].init.headers.cookie, undefined); assert.equal(f.calls[1].init.headers.apikey, undefined);
  assert.equal(result.headers.get('access-control-allow-origin'), app.origins[0]);
  assert.ok(!JSON.stringify(f.events).includes(token())); assert.ok(!JSON.stringify(f.events).includes('select='));
});
test('CORS only permits exact origins, routes, methods and headers', async t => {
  const f = await fixture(t);
  const base = { origin: app.origins[0], 'access-control-request-method': 'GET', 'access-control-request-headers': 'apikey, authorization' };
  assert.equal((await fetch(f.url + '/rest/v1/items', { method: 'OPTIONS', headers: base })).status, 204);
  assert.equal((await fetch(f.url + '/studio', { method: 'OPTIONS', headers: base })).status, 403);
  assert.equal((await fetch(f.url + '/rest/v1/items', { method: 'OPTIONS', headers: { ...base, origin: 'https://evil.test' } })).status, 403);
  assert.equal(f.calls.length, 0);
});
test('schema overrides and unsupported body types are rejected', async t => {
  const f = await fixture(t);
  assert.equal((await fetch(f.url + '/rest/v1/items', { headers: { ...headers(), 'accept-profile': 'public' } })).status, 403);
  assert.equal((await fetch(f.url + '/rest/v1/items', { method: 'POST', headers: { ...headers(), 'content-type': 'text/plain' }, body: '{}' })).status, 415);
  assert.equal((await fetch(f.url + '/rest/v1/items', { method: 'POST', headers: headers(), body: 'bad json' })).status, 400);
});
test('upsert merge cannot widen an insert-only table scope', async t => {
  const insertOnly = { ...app, tables: { items: ['GET', 'POST'] } };
  assert.equal(routeFor('/rest/v1/items?on_conflict=id', 'POST', insertOnly), null);
  assert.ok(routeFor('/rest/v1/items?on_conflict=id', 'POST', app));
  const calls = [];
  const gateway = createGateway({ format: 1, apps: [insertOnly] }, { audit: () => {},
    fetcher: async (url) => { calls.push(url); return new Response(JSON.stringify(url.endsWith('/user') ? { id: user } : [])); } });
  gateway.listen(0, '127.0.0.1'); await once(gateway, 'listening');
  t.after(() => { gateway.closeAllConnections(); gateway.close(); });
  const url = 'http://127.0.0.1:' + gateway.address().port + '/rest/v1/items';
  assert.equal((await fetch(url, { method: 'POST', headers: { ...headers(), prefer: 'resolution=merge-duplicates' }, body: '{}' })).status, 403);
  assert.ok(!calls.some(call => call.startsWith('http://rest:3000')));
  assert.equal((await fetch(url, { method: 'POST', headers: { ...headers(), prefer: 'resolution=ignore-duplicates' }, body: '{}' })).status, 200);
});
test('password and refresh exchanges are explicitly allowed but never admin or signup', async t => {
  const f = await fixture(t);
  for (const grant of ['password', 'refresh_token']) {
    assert.equal((await fetch(f.url + '/auth/v1/token?grant_type=' + grant, { method: 'POST', headers: { apikey: app.key, 'content-type': 'application/json' }, body: '{}' })).status, 200);
  }
  assert.equal((await fetch(f.url + '/auth/v1/signup', { method: 'POST', headers: headers(), body: '{}' })).status, 404);
});
test('client keys group IPv6 by /64 and never trust malformed values', () => {
  assert.equal(clientKey('203.0.113.9'), '203.0.113.9');
  assert.equal(clientKey('2001:db8:1:2:3:4:5:6'), clientKey('2001:DB8:1:2::9'));
  assert.notEqual(clientKey('2001:db8:1:2::1'), clientKey('2001:db8:1:3::1'));
  assert.equal(clientKey('::ffff:198.51.100.7'), '198.51.100.7');
  for (const value of [undefined, '', 'unknown', '203.0.113.9, 198.51.100.7', 'fe80::1%eth0', ['203.0.113.9']]) assert.equal(clientKey(value), 'unattributed');
});
test('one client cannot exhaust sign-in for others, and the total still applies', async t => {
  const f = await fixture(t, { authPerClient: 3, authTotal: 5 });
  const signIn = ip => fetch(f.url + '/auth/v1/token?grant_type=password', { method: 'POST', headers: { apikey: app.key, 'content-type': 'application/json', 'cf-connecting-ip': ip }, body: '{}' });
  for (let i = 0; i < 3; i++) assert.equal((await signIn('203.0.113.9')).status, 200);
  assert.equal((await signIn('203.0.113.9')).status, 429);
  assert.equal((await signIn('198.51.100.7')).status, 200);
  assert.equal((await signIn('198.51.100.8')).status, 200);
  assert.equal((await signIn('198.51.100.9')).status, 429);
  assert.ok(!JSON.stringify(f.events).includes('198.51.100'));
});
test('per-client budgets keep one source from exhausting shared capacity', async t => {
  const f = await fixture(t, { clientPerMinute: 3 });
  const from = ip => fetch(f.url + '/missing', { headers: { ...headers(), 'cf-connecting-ip': ip } });
  for (let i = 0; i < 3; i++) assert.equal((await from('203.0.113.9')).status, 404);
  assert.equal((await from('203.0.113.9')).status, 429);
  assert.equal((await from('198.51.100.7')).status, 404);
  assert.throws(() => validateConfig({ format: 1, apps: [{ ...app, clientRequestsPerMinute: app.requestsPerMinute + 1 }] }));
  const shared = { ...app, clientRequestsPerMinute: 2 };
  const gateway = createGateway({ format: 1, apps: [shared] }, { audit: () => {}, fetcher: async url => new Response(JSON.stringify(url.endsWith('/user') ? { id: user } : [])) });
  gateway.listen(0, '127.0.0.1'); await once(gateway, 'listening');
  t.after(() => { gateway.closeAllConnections(); gateway.close(); });
  const read = ip => fetch('http://127.0.0.1:' + gateway.address().port + '/rest/v1/items', { headers: { ...headers(), 'cf-connecting-ip': ip } });
  assert.equal((await read('203.0.113.9')).status, 200);
  assert.equal((await read('203.0.113.9')).status, 200);
  assert.equal((await read('203.0.113.9')).status, 429);
  assert.equal((await read('198.51.100.7')).status, 200);
});
test('one client cannot hold every in-flight slot', async t => {
  let release; const held = new Promise(resolve => { release = resolve; });
  const f = await fixture(t, { clientConcurrency: 1, fetcher: async url => {
    if (url.endsWith('/user')) return new Response(JSON.stringify({ id: user }));
    await held; return new Response('[]');
  } });
  const read = ip => fetch(f.url + '/rest/v1/items', { headers: { ...headers(), 'cf-connecting-ip': ip } });
  const slow = read('203.0.113.9');
  await new Promise(resolve => setTimeout(resolve, 50));
  assert.equal((await read('203.0.113.9')).status, 429);
  const other = read('198.51.100.7');
  await new Promise(resolve => setTimeout(resolve, 50));
  release();
  assert.equal((await slow).status, 200);
  assert.equal((await other).status, 200);
  assert.equal((await read('203.0.113.9')).status, 200);
});
test('app limits, audit failure and backend failures fail closed', async t => {
  const f = await fixture(t);
  for (let i = 0; i < app.requestsPerMinute; i++) await fetch(f.url + '/missing', { headers: headers() });
  assert.equal((await fetch(f.url + '/rest/v1/items', { headers: headers() })).status, 429);
  const failed = await fixture(t, { audit: () => { throw Error('disk unavailable'); } });
  assert.equal((await fetch(failed.url + '/rest/v1/items', { headers: headers() })).status, 503);
  const backend = await fixture(t, { fetcher: async () => { throw Error('sensitive detail'); } });
  const result = await fetch(backend.url + '/rest/v1/items', { headers: headers() });
  assert.equal(result.status, 502); assert.ok(!(await result.text()).includes('sensitive'));
});
