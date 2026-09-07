import { constants } from 'node:fs';
import { open, type FileHandle } from 'node:fs/promises';
import { digest } from './security.js';

export type AuditEvent = { at: string; actor: string; event: 'login' | 'login-denied' | 'logout' | 'file-list' | 'file-download' | 'operation-denied'; outcome: 'ok' | 'denied' };
export class Audit {
  private recent: AuditEvent[] = [];
  private queue: Promise<unknown> = Promise.resolve();
  private constructor(private file: FileHandle) {}
  static async create(path: string) {
    const file = await open(path, constants.O_APPEND | constants.O_CREAT | constants.O_RDWR | constants.O_NOFOLLOW, 0o600);
    const s = await file.stat();
    if (!s.isFile() || s.nlink !== 1 || (s.mode & 0o077)) { await file.close(); throw new Error('Unsafe audit file'); }
    return new Audit(file);
  }
  async record(userId: string, event: AuditEvent['event'], outcome: AuditEvent['outcome']) {
    const row = { at: new Date().toISOString(), actor: digest(userId).slice(0, 16), event, outcome };
    const job = this.queue.then(async () => {
      if ((await this.file.stat()).size > 16 * 1024 * 1024) throw new Error('Audit capacity reached');
      await this.file.writeFile(JSON.stringify(row) + '\n'); await this.file.sync();
      this.recent.push(row); if (this.recent.length > 200) this.recent.shift();
    });
    this.queue = job.catch(() => {}); await job;
  }
  list() { return [...this.recent].reverse(); }
  async close() { await this.queue; await this.file.close(); }
}
