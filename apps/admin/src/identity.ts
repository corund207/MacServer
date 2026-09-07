import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { isIP } from 'node:net';

const exec = promisify(execFile);
export type Identity = { userId: string; nodeId: string };
export function tailnetAddress(ip: string): boolean {
  if (isIP(ip) === 4) { const p = ip.split('.').map(Number); return p[0] === 100 && p[1] >= 64 && p[1] <= 127; }
  return isIP(ip) === 6 && ip.toLowerCase().startsWith('fd7a:115c:a1e0:');
}
export function assertBind(host: string, assigned: string[]) {
  if (host === '127.0.0.1' || host === '::1') return;
  if (!tailnetAddress(host) || !assigned.includes(host)) throw new Error('Bind must be loopback or an assigned Tailscale address');
}
async function cli(args: string[]) {
  const { stdout } = await exec('/usr/bin/tailscale', args, { timeout: 2500, maxBuffer: 1024 * 1024, env: { PATH: '/usr/bin:/bin' } });
  return JSON.parse(stdout);
}
export async function tailscaleStatus() {
  const s = await cli(['status', '--json']);
  if (s.BackendState !== 'Running' || !Array.isArray(s.TailscaleIPs)) throw new Error('Tailscale unavailable');
  return { state: 'Running', addresses: s.TailscaleIPs.filter((v: unknown) => typeof v === 'string' && tailnetAddress(v)) as string[] };
}
export function parseIdentity(value: any, ip: string): Identity {
  const n = value?.Node, u = value?.UserProfile;
  if (!tailnetAddress(ip) || !n || !u || (n.Tags?.length ?? 0) !== 0 ||
      n.Expired === true || n.IsWireGuardOnly === true || n.UnsignedPeerAPIOnly === true ||
      !Array.isArray(n.Addresses) || !n.Addresses.includes(ip + (isIP(ip) === 4 ? '/32' : '/128')) ||
      !Number.isSafeInteger(u.ID) || u.ID <= 0 || n.User !== u.ID ||
      typeof n.StableID !== 'string' || !/^[a-zA-Z0-9_-]{1,128}$/.test(n.StableID)) throw new Error('Identity denied');
  return { userId: String(u.ID), nodeId: n.StableID };
}
export async function whois(ip: string): Promise<Identity> {
  if (!tailnetAddress(ip)) throw new Error('Identity denied');
  return parseIdentity(await cli(['whois', '--json', ip]), ip);
}
