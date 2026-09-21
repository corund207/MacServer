import { constants } from 'node:fs';
import { open } from 'node:fs/promises';

const labels: Record<string, string> = { db: 'PostgreSQL', auth: 'Auth', rest: 'PostgREST', 'api-gw': 'Envoy', functions: 'Functions', storage: 'Storage', realtime: 'Realtime', studio: 'Studio', meta: 'Database metadata', imgproxy: 'Image proxy' };
const states = ['healthy', 'degraded', 'failed', 'unavailable'];
export type CollectorStatus = { observedAt: string; services: { name: string; state: string; detail: string }[]; backup: { state: string; lastSuccess: string | null } };
export function normalizeCollector(value: any, now = Date.now()): CollectorStatus {
  const at = Date.parse(value?.observedAt);
  if (value?.format !== 1 || !Number.isFinite(at) || at > now + 5000 || now - at > 60000 || !Array.isArray(value.services)) throw Error('Stale or invalid collector');
  const services = Object.entries(labels).map(([key, name]) => {
    const row = value.services.find((candidate: any) => candidate?.name === key);
    return { name, state: states.includes(row?.state) ? row.state : 'unavailable', detail: typeof row?.detail === 'string' ? row.detail.slice(0, 160) : 'No container observation' };
  });
  const backupAt = Date.parse(value.backup?.lastSuccess);
  return { observedAt: new Date(at).toISOString(), services,
    backup: { state: states.includes(value.backup?.state) ? value.backup.state : 'unavailable', lastSuccess: Number.isFinite(backupAt) && backupAt <= now ? new Date(backupAt).toISOString() : null } };
}
export async function readCollector(): Promise<CollectorStatus> {
  // Fixed root-controlled directory; never accepts a request path or a secret root.
  const file = await open('/run/macserver/status.json', constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const stat = await file.stat();
    if (!stat.isFile() || stat.nlink !== 1 || stat.uid !== 0 || stat.size > 131072 || (stat.mode & 0o022)) throw Error('Unsafe collector file');
    const bytes = Buffer.alloc(131073);
    const { bytesRead } = await file.read(bytes, 0, bytes.length, 0);
    if (bytesRead > 131072) throw Error('Collector too large');
    return normalizeCollector(JSON.parse(bytes.subarray(0, bytesRead).toString('utf8')));
  } finally { await file.close(); }
}
