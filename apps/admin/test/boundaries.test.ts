import { test } from 'node:test';
import assert from 'node:assert/strict';
import { request } from 'node:https';
import { promisify } from 'node:util';
import { execFile } from 'node:child_process';
import { mkdtemp, writeFile, readFile, stat, chmod, symlink, truncate, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createApp } from '../src/app.js';
import { parseIdentity, parseStatus } from '../src/identity.js';
import { credential, loadCredentials, verify, token } from '../src/security.js';
import { Audit, AUDIT_LIMIT } from '../src/audit.js';
import { Files } from '../src/files.js';
import { tlsFixture } from './helpers.js';

const exec = promisify(execFile), user = { userId: '123', nodeId: 'node1' }, origin = 'https://localhost:8443';
const validNode = { Node: { StableID: 'node1', User: 123, MachineAuthorized: true, Addresses: ['100.64.0.2/32'] }, UserProfile: { ID: 123 } };
test('versioned daemon contract denies expired, unapproved, shared, malformed and userspace identities', () => {
  const now = Date.parse('2026-09-07T00:00:00Z');
  for (const patch of [{ KeyExpiry: '2026-09-06T23:59:59Z' }, { KeyExpiry: 'garbage' }, { KeyExpiry: null },
    { Expired: 'false' }, { MachineAuthorized: false }, { MachineAuthorized: undefined }, { Sharer: 456 }, { Tags: {} }]) {
    assert.throws(() => parseIdentity({ ...validNode, Node: { ...validNode.Node, ...patch } }, '100.64.0.2', now));
  }
  assert.deepEqual(parseIdentity({ ...validNode, Node: { ...validNode.Node, KeyExpiry: '2026-10-01T00:00:00Z' } }, '100.64.0.2', now), user);
  const s = { Version: '1.102.3-t9329c3677', BackendState: 'Running', TUN: true, Self: { Online: true }, TailscaleIPs: ['100.64.0.2'] };
  assert.equal(parseStatus(s, now).state, 'Running');
  for (const patch of [{ Version: '1.102.4' }, { Version: null }, { BackendState: 'Stopped' }, { TUN: false }, { Self: { Online: false } },
    { Self: { Online: true, Expired: true } }, { Self: { Online: true, KeyExpiry: '2026-09-06T00:00:00Z' } }, { TailscaleIPs: ['192.168.1.1'] }]) assert.throws(() => parseStatus({ ...s, ...patch }, now));
});
test('provisioning creates private verifier without printing token and refuses overwrite/unsafe parent', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'macserver-provision-'));
  const run = (destination: string, id = '123') => exec(process.execPath, ['build/src/provision.js', id, destination]);
  try {
    const destination = join(dir, 'new'); const result = await run(destination);
    const secret = (await readFile(join(destination, 'initial-token.txt'), 'utf8')).trim();
    const credentials = await loadCredentials(join(destination, 'credentials.json'));
    assert.ok(await verify(credentials[0], secret)); assert.ok(!result.stdout.includes(secret));
    assert.equal((await stat(destination)).mode & 0o777, 0o700);
    assert.equal((await stat(join(destination, 'initial-token.txt'))).mode & 0o777, 0o600);
    await assert.rejects(run(destination)); await assert.rejects(run(join(dir, 'invalid'), '9007199254740992'));
    await symlink(dir, join(dir, 'alias')); await assert.rejects(run(join(dir, 'alias/next')));
    await chmod(dir, 0o777); await assert.rejects(run(join(dir, 'unsafe'))); await chmod(dir, 0o700);
    assert.equal((await readFile(join(destination, 'initial-token.txt'), 'utf8')).trim(), secret);
  } finally { await rm(dir, { recursive: true }); }
});
test('audit refuses unsafe files and capacity overflow; serial writes survive reopening', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'macserver-audit-')), path = join(dir, 'audit.jsonl');
  try {
    const audit = await Audit.create(path);
    await Promise.all([audit.record('123', 'login', 'ok'), audit.record('123', 'logout', 'ok')]); await audit.close();
    assert.deepEqual((await readFile(path, 'utf8')).trim().split('\n').map(s => JSON.parse(s).event), ['login', 'logout']);
    const reopened = await Audit.create(path); assert.deepEqual(reopened.list(), []);
    await truncate(path, AUDIT_LIMIT - 1); await assert.rejects(reopened.record('123', 'login', 'ok')); await reopened.close();
    assert.equal((await stat(path)).size, AUDIT_LIMIT - 1);
    await chmod(path, 0o644); await assert.rejects(Audit.create(path));
    await symlink(path, join(dir, 'link')); await assert.rejects(Audit.create(join(dir, 'link')));
    await exec('/usr/bin/mkfifo', [join(dir, 'fifo')]); await assert.rejects(Audit.create(join(dir, 'fifo'))); await assert.rejects(loadCredentials(join(dir, 'fifo')));
  } finally { await rm(dir, { recursive: true }); }
});
test('file routes enforce authentication and audit; failed logout invalidates session', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'macserver-routes-')); const secret = token();
  await writeFile(join(dir, 'example.txt'), 'synthetic export');
  const files = await Files.create(dir), audit = await Audit.create(join(dir, '.audit'));
  let failAudit = false;
  const app = createApp({ origin, files, credentials: [await credential('123', secret)], identity: async () => user, tailscale: async () => ({ state: 'Running', addresses: [] }),
    audit: { list: () => audit.list(), record: async (...args) => { if (failAudit) throw new Error('sensitive failure'); await audit.record(...args); } } });
  const base = { host: 'localhost:8443', origin, 'content-type': 'application/json', 'x-admin-request': '1' };
  try {
    assert.equal((await app.inject({ url: '/api/files', headers: base })).statusCode, 401);
    const login = await app.inject({ method: 'POST', url: '/api/login', headers: base, payload: { secret, confirm: true } });
    const headers = { ...base, cookie: String(login.headers['set-cookie']).split(';')[0], 'x-csrf-token': login.json().csrf };
    assert.equal((await app.inject({ url: '/api/files', headers })).statusCode, 200);
    const read = await app.inject({ url: '/api/files/download?path=example.txt', headers });
    assert.equal(read.body, 'synthetic export'); assert.match(String(read.headers['content-disposition']), /^attachment/);
    assert.equal((await app.inject({ url: '/api/files/download?path=..%2Foutside.txt', headers })).statusCode, 503);
    assert.equal((await app.inject({ url: '/api/files?path=a&path=b', headers })).statusCode, 400);
    assert.equal((await app.inject({ url: '/api/audit?outcome=bad', headers })).statusCode, 400);
    assert.ok(audit.list().some(e => e.event === 'file-denied'));
    failAudit = true;
    const deniedRead = await app.inject({ url: '/api/files/download?path=example.txt', headers });
    assert.equal(deniedRead.statusCode, 503); assert.ok(!deniedRead.body.includes('synthetic export'));
    const logout = await app.inject({ method: 'POST', url: '/api/logout', headers, payload: { confirm: true } });
    assert.equal(logout.statusCode, 503); assert.match(String(logout.headers['set-cookie']), /Max-Age=0/);
    assert.equal((await app.inject({ url: '/api/session', headers })).statusCode, 401);
  } finally { await app.close(); await files.close(); await audit.close(); await rm(dir, { recursive: true }); }
});
test('synthetic TLS listener validates real socket peer, secure session and certificate trust', async () => {
  const f = await tlsFixture(), secret = token(); let observed = '';
  const app = createApp({ origin, tls: f.tls, credentials: [await credential('123', secret)], identity: async ip => { observed = ip; return user; },
    audit: { list: () => [], record: async () => {} }, tailscale: async () => ({ state: 'Running', addresses: [] }) });
  try {
    await app.listen({ host: '127.0.0.1', port: 0 }); const address = app.server.address(); assert.ok(address && typeof address !== 'string');
    const send = (trust: boolean, host = 'localhost:8443') => new Promise<{ code: number; cookie: string; body: string }>((resolve, reject) => {
      const req = request({ hostname: '127.0.0.1', servername: 'localhost', port: address.port, path: '/api/login', method: 'POST', ...(trust ? { ca: f.tls.cert } : {}),
        headers: { host, origin, 'content-type': 'application/json', 'x-admin-request': '1' } }, res => {
        let body = ''; res.on('data', c => { body += c; }); res.on('end', () => resolve({ code: res.statusCode!, cookie: String(res.headers['set-cookie']), body }));
      }); req.on('error', reject); req.end(JSON.stringify({ secret, confirm: true }));
    });
    await assert.rejects(send(false)); assert.equal((await send(true, 'evil.test')).code, 421);
    const good = await send(true); assert.equal(good.code, 200); assert.match(good.cookie, /Secure/); assert.equal(observed, '127.0.0.1');
    assert.ok(!good.body.includes(secret));
  } finally { await app.close(); await f.close(); }
});
