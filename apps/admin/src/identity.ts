import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { isIP } from 'node:net';

const exec = promisify(execFile);
export const TAILSCALE_VERSION = '1.102.3';
let activeCommands = 0;
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
  if (activeCommands >= 4) throw new Error('Identity verifier busy');
  activeCommands++;
  try {
    const { stdout } = await exec('/usr/bin/tailscale', args, { timeout: 2500, maxBuffer: 1024 * 1024, env: { PATH: '/usr/bin:/bin' } });
    return JSON.parse(stdout);
  } finally { activeCommands--; }
}
function validExpiry(expiry: unknown, now: number) {
  // v1.102.3 omits zero time when expiry is disabled. Reject malformed present data.
  if (expiry === undefined) return true;
  return typeof expiry === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z$/.test(expiry) && Date.parse(expiry) > now;
}
export function parseStatus(s: any, now = Date.now()) {
  if (typeof s?.Version !== 'string' || s.Version.split('-')[0] !== TAILSCALE_VERSION ||
      s.BackendState !== 'Running' || s.TUN !== true || !s.Self || s.Self.Online !== true ||
      (s.Self.Expired !== undefined && s.Self.Expired !== false) || !validExpiry(s.Self.KeyExpiry, now) ||
      !Array.isArray(s.TailscaleIPs) || !s.TailscaleIPs.length || s.TailscaleIPs.some((v: unknown) => typeof v !== 'string' || !tailnetAddress(v))) throw new Error('Tailscale unavailable or unsupported');
  return { state: 'Running', addresses: s.TailscaleIPs as string[] };
}
export async function tailscaleStatus() { return parseStatus(await cli(['status', '--json', '--peers=false'])); }
export function parseIdentity(value: any, ip: string, now = Date.now()): Identity {
  const n = value?.Node, u = value?.UserProfile;
  if (!tailnetAddress(ip) || !n || !u || (n.Tags !== undefined && (!Array.isArray(n.Tags) || n.Tags.length !== 0)) ||
      n.MachineAuthorized !== true || !validExpiry(n.KeyExpiry, now) ||
      [n.Expired, n.IsWireGuardOnly, n.UnsignedPeerAPIOnly].some(v => v !== undefined && v !== false) ||
      (n.Sharer !== undefined && n.Sharer !== 0) ||
      !Array.isArray(n.Addresses) || !n.Addresses.includes(ip + (isIP(ip) === 4 ? '/32' : '/128')) ||
      !Number.isSafeInteger(u.ID) || u.ID <= 0 || n.User !== u.ID ||
      typeof n.StableID !== 'string' || !/^[a-zA-Z0-9_-]{1,128}$/.test(n.StableID)) throw new Error('Identity denied');
  return { userId: String(u.ID), nodeId: n.StableID };
}
export async function whois(ip: string): Promise<Identity> {
  if (!tailnetAddress(ip)) throw new Error('Identity denied');
  await tailscaleStatus(); // Do not accept a cached whois after local logout/expiry.
  return parseIdentity(await cli(['whois', '--json', ip]), ip);
}
