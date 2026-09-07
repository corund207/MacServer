import { createHash, randomBytes, scrypt, timingSafeEqual } from 'node:crypto';
import { promisify } from 'node:util';
import { constants } from 'node:fs';
import { open } from 'node:fs/promises';
import type { Identity } from './identity.js';

const derive = promisify(scrypt);
export type Credential = { userId: string; salt: string; hash: string };
export const token = () => randomBytes(32).toString('base64url');
export const digest = (v: string) => createHash('sha256').update(v).digest('hex');
export async function credential(userId: string, secret: string): Promise<Credential> {
  const salt = randomBytes(32).toString('hex');
  return { userId, salt, hash: (await derive(secret, salt, 64) as Buffer).toString('hex') };
}
export async function verify(c: Credential, secret: string) {
  return timingSafeEqual(await derive(secret, c.salt, 64) as Buffer, Buffer.from(c.hash, 'hex'));
}
export async function loadCredentials(path: string): Promise<Credential[]> {
  const f = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const s = await f.stat();
    if (!s.isFile() || (s.mode & 0o077) || s.size > 65536 || s.nlink !== 1) throw new Error('Unsafe credential file');
    const value = JSON.parse(await f.readFile('utf8'));
    if (!Array.isArray(value) || !value.length || value.length > 32 || value.some(c => !/^[1-9][0-9]{0,15}$/.test(c.userId) || !/^[a-f0-9]{64}$/.test(c.salt) || !/^[a-f0-9]{128}$/.test(c.hash)) || new Set(value.map(c => c.userId)).size !== value.length) throw new Error('Invalid credentials');
    return value;
  } finally { await f.close(); }
}
type Session = Identity & { csrf: string; expires: number };
export class Sessions {
  private values = new Map<string, Session>();
  constructor(private now = Date.now) {}
  create(identity: Identity) {
    for (const [k, v] of this.values) if (v.expires <= this.now()) this.values.delete(k);
    if (this.values.size >= 128) throw new Error('Session capacity');
    const id = token(), value = { ...identity, csrf: token(), expires: this.now() + 15 * 60_000 };
    this.values.set(digest(id), value); return { id, ...value };
  }
  get(id: string, identity: Identity) {
    const v = this.values.get(digest(id));
    if (!v || v.expires <= this.now() || v.userId !== identity.userId || v.nodeId !== identity.nodeId) return undefined;
    return v;
  }
  delete(id: string) { this.values.delete(digest(id)); }
}
export class Limiter {
  private entries = new Map<string, { count: number; until: number }>();
  constructor(private max: number, private period: number, private now = Date.now) {}
  allow(key: string) {
    for (const [k, v] of this.entries) if (v.until <= this.now()) this.entries.delete(k);
    let v = this.entries.get(key);
    if (!v) { if (this.entries.size >= 1024) return false; v = { count: 0, until: this.now() + this.period }; this.entries.set(key, v); }
    return ++v.count <= this.max;
  }
}
