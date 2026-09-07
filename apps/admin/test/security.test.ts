import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, symlink, link, mkdir, chmod, rm, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createApp } from '../src/app.js';
import { credential, Sessions, loadCredentials, token, Limiter } from '../src/security.js';
import { assertBind, parseIdentity } from '../src/identity.js';
import { Files } from '../src/files.js';
import { Audit } from '../src/audit.js';

const origin = 'https://appliance.example.ts.net:8443', user = { userId: '123', nodeId: 'node1' };
const secret = token(), admin = await credential(user.userId, secret);
async function fixture() {
  const dir = await mkdtemp(join(tmpdir(), 'macserver-admin-'));
  const audit = await Audit.create(join(dir, 'audit.jsonl'));
  let identity = user, denied = false, brokenAudit = false;
  const app = createApp({ origin, credentials: [admin], identity: async () => { if (denied) throw new Error(); return identity; },
    audit: { list: () => audit.list(), record: async (...args) => { if (brokenAudit) throw new Error('synthetic-secret'); await audit.record(...args); } },
    tailscale: async () => { throw new Error('offline'); } });
  const headers = { host: new URL(origin).host, origin, 'content-type': 'application/json', 'x-admin-request': '1' };
  return { app, dir, audit, headers,
    setIdentity: (v: typeof user) => { identity = v; }, deny: () => { denied = true; }, breakAudit: () => { brokenAudit = true; },
    login: () => app.inject({ method: 'POST', url: '/api/login', headers, payload: { secret, confirm: true } }),
    close: async () => { await app.close(); await audit.close(); await rm(dir, { recursive: true }); } };
}
test('bind policy rejects public, wildcard, LAN, and unassigned CGNAT addresses', () => {
  for (const host of ['0.0.0.0', '::', '192.168.1.1', '8.8.8.8', '100.64.0.2', 'localhost']) assert.throws(() => assertBind(host, []));
  assertBind('127.0.0.1', []); assertBind('100.64.0.2', ['100.64.0.2']);
});
test('daemon identity requires exact node address, matching user and untagged device', () => {
  const value = { Node: { StableID: 'node1', User: 123, MachineAuthorized: true, Addresses: ['100.64.0.2/32'] }, UserProfile: { ID: 123 } };
  assert.deepEqual(parseIdentity(value, '100.64.0.2'), user);
  assert.throws(() => parseIdentity(value, '100.64.0.3'));
  for (const extra of [{ Tags: ['tag:server'] }, { Expired: true }, { User: 999 }, { IsWireGuardOnly: true }]) assert.throws(() => parseIdentity({ ...value, Node: { ...value.Node, ...extra } }, '100.64.0.2'));
});
test('sessions expire absolutely and are bound to user and device; limiter resets', () => {
  let now = 1000; const sessions = new Sessions(() => now), s = sessions.create(user);
  assert.ok(sessions.get(s.id, user)); assert.equal(sessions.get(s.id, { ...user, nodeId: 'other' }), undefined);
  assert.equal(sessions.get(s.id, { ...user, userId: '456' }), undefined);
  now += 900_000; assert.equal(sessions.get(s.id, user), undefined);
  const limits = new Limiter(1, 100, () => now); assert.ok(limits.allow('a')); assert.equal(limits.allow('a'), false); now += 101; assert.ok(limits.allow('a'));
});
test('routes require identity, independent login, canonical host and no proxy headers', async () => {
  const f = await fixture();
  try {
    for (const url of ['/', '/style.css', '/app.js']) assert.equal((await f.app.inject({ url, headers: f.headers })).statusCode, 200);
    assert.equal((await f.app.inject({ url: '/api/overview', headers: f.headers })).statusCode, 401);
    assert.equal((await f.app.inject({ url: '/', headers: { ...f.headers, host: 'evil.test' } })).statusCode, 421);
    assert.equal((await f.app.inject({ url: '/', headers: { ...f.headers, 'tailscale-user-login': 'admin' } })).statusCode, 403);
    assert.equal((await f.app.inject({ url: '/', headers: { ...f.headers, 'x-forwarded-for': '100.64.0.2' } })).statusCode, 403);
    f.setIdentity({ ...user, userId: '456' }); assert.equal((await f.login()).statusCode, 403);
    f.deny(); assert.equal((await f.login()).statusCode, 403);
  } finally { await f.close(); }
});
test('secure login, CSRF, session binding, unavailable operations, logout and audit', async () => {
  const f = await fixture();
  try {
    const login = await f.login(); assert.equal(login.statusCode, 200);
    const setCookie = String(login.headers['set-cookie']);
    for (const flag of ['__Host-macserver=', 'HttpOnly', 'Secure', 'SameSite=Strict', 'Path=/', 'Max-Age=900']) assert.ok(setCookie.includes(flag));
    const cookie = setCookie.split(';')[0], headers = { ...f.headers, cookie }, csrf = login.json().csrf;
    const overview = await f.app.inject({ url: '/api/overview', headers });
    assert.equal(overview.statusCode, 200); assert.equal(overview.json().tailscale.state, 'Unavailable');
    assert.equal(overview.json().metrics.cpu, null); assert.equal(overview.json().capabilities.sql, false);
    assert.equal(overview.headers['cache-control'], 'no-store'); assert.match(String(overview.headers['content-security-policy']), /frame-ancestors 'none'/);
    assert.equal((await f.app.inject({ method: 'POST', url: '/api/logout', headers, payload: { confirm: true } })).statusCode, 403);
    assert.equal((await f.app.inject({ method: 'POST', url: '/api/logout', headers: { ...headers, origin: 'https://evil.test', 'x-csrf-token': csrf }, payload: { confirm: true } })).statusCode, 403);
    for (const name of ['sql', 'backup', 'restore', 'update', 'restart', 'upload', 'delete']) {
      assert.equal((await f.app.inject({ method: 'POST', url: `/api/operations/${name}`, headers: { ...headers, 'x-csrf-token': csrf }, payload: {} })).statusCode, 501);
    }
    f.setIdentity({ ...user, nodeId: 'other' }); assert.equal((await f.app.inject({ url: '/api/overview', headers })).statusCode, 401); f.setIdentity(user);
    assert.equal((await f.app.inject({ method: 'POST', url: '/api/logout', headers: { ...headers, 'x-csrf-token': csrf }, payload: { confirm: true } })).statusCode, 200);
    assert.equal((await f.app.inject({ url: '/api/overview', headers })).statusCode, 401);
    const evidence = await readFile(join(f.dir, 'audit.jsonl'), 'utf8'); assert.ok(evidence.includes('operation-denied')); assert.ok(!evidence.includes(secret)); assert.ok(!evidence.includes(csrf));
  } finally { await f.close(); }
});
test('login validation, request limits and audit failure fail closed', async () => {
  const f = await fixture();
  try {
    assert.equal((await f.app.inject({ method: 'POST', url: '/api/login', headers: f.headers, payload: { secret, confirm: true, extra: 'no' } })).statusCode, 400);
    assert.equal((await f.app.inject({ method: 'POST', url: '/api/login', headers: { ...f.headers, 'x-admin-request': '' }, payload: { secret, confirm: true } })).statusCode, 403);
    for (let i = 0; i < 5; i++) assert.equal((await f.app.inject({ method: 'POST', url: '/api/login', headers: f.headers, payload: { secret: token(), confirm: true } })).statusCode, 401);
    assert.equal((await f.login()).statusCode, 429);
  } finally { await f.close(); }
  const g = await fixture();
  try { g.breakAudit(); const result = await g.login(); assert.equal(result.statusCode, 503); assert.equal(result.headers['set-cookie'], undefined); assert.ok(!result.body.includes('synthetic-secret')); }
  finally { await g.close(); }
});
test('file reads reject traversal, symlinks, hardlinks, hidden files, oversized files and special files', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'macserver-files-')); let files: Files | undefined;
  try {
    await mkdir(join(dir, 'root')); await mkdir(join(dir, 'root/folder')); await writeFile(join(dir, 'root/folder/good.txt'), 'approved');
    await writeFile(join(dir, 'outside.txt'), 'private'); await symlink(join(dir, 'outside.txt'), join(dir, 'root/link.txt'));
    await symlink(dir, join(dir, 'root/escape')); await link(join(dir, 'outside.txt'), join(dir, 'root/hard.txt'));
    await writeFile(join(dir, 'root/large.txt'), Buffer.alloc(1024 * 1024 + 1));
    files = await Files.create(join(dir, 'root'));
    assert.equal((await files.download('folder/good.txt')).toString(), 'approved');
    for (const path of ['../outside.txt', '/etc/passwd', '.env', 'link.txt', 'escape/outside.txt', 'hard.txt', 'large.txt', 'folder/../folder/good.txt']) await assert.rejects(files.download(path));
    await assert.rejects(files.list('escape')); assert.ok(!(await files.list('')).entries.some(e => e.name === 'escape' || e.name === 'link.txt'));
    await symlink(join(dir, 'root'), join(dir, 'alias')); await assert.rejects(Files.create(join(dir, 'alias')));
  } finally { await files?.close(); await rm(dir, { recursive: true }); }
});
test('credential file permissions, symlinks and invalid records are refused', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'macserver-credentials-')), path = join(dir, 'credentials.json');
  try {
    await writeFile(path, JSON.stringify([admin]), { mode: 0o600 }); assert.deepEqual(await loadCredentials(path), [admin]);
    await chmod(path, 0o644); await assert.rejects(loadCredentials(path)); await chmod(path, 0o600);
    await symlink(path, join(dir, 'alias')); await assert.rejects(loadCredentials(join(dir, 'alias')));
    await writeFile(path, '[]'); await assert.rejects(loadCredentials(path));
  } finally { await rm(dir, { recursive: true }); }
});
