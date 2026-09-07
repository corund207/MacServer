import { constants } from 'node:fs';
import { open, opendir, type FileHandle } from 'node:fs/promises';

// Linux descriptor-relative traversal: pin every ancestor, never follow a user symlink.
const directoryFlags = constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW;
export class Files {
  private constructor(private root: FileHandle) {}
  static async create(root: string) {
    if (!root.startsWith('/') || root === '/' || root.split('/').includes('..')) throw new Error('Unsafe root');
    let fd = await open('/', directoryFlags);
    try {
      for (const part of root.split('/').filter(Boolean)) {
        const next = await open(`/proc/self/fd/${fd.fd}/${part}`, directoryFlags); await fd.close(); fd = next;
      }
      return new Files(fd);
    } catch (e) { await fd.close(); throw e; }
  }
  private async resolve(path: string, file: boolean) {
    if (path.length > 512 || (path && path.split('/').some(p => !/^[a-zA-Z0-9][a-zA-Z0-9._ -]{0,127}$/.test(p) || p === '..'))) throw new Error('Invalid path');
    const parts = path ? path.split('/') : [];
    if (file && (!parts.length || !/\.(txt|csv|json|log)$/.test(parts.at(-1)!))) throw new Error('File type denied');
    let fd = await open(`/proc/self/fd/${this.root.fd}/.`, directoryFlags);
    try {
      for (let i = 0; i < parts.length; i++) {
        const flags = file && i === parts.length - 1 ? constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK : directoryFlags;
        const next = await open(`/proc/self/fd/${fd.fd}/${parts[i]}`, flags); await fd.close(); fd = next;
      }
      return fd;
    } catch (e) { await fd.close(); throw e; }
  }
  async list(path: string) {
    const fd = await this.resolve(path, false);
    try {
      const d = await opendir(`/proc/self/fd/${fd.fd}`); const entries = []; let scanned = 0;
      for await (const entry of d) {
        if (/^[a-zA-Z0-9][a-zA-Z0-9._ -]{0,127}$/.test(entry.name) && (entry.isDirectory() || (entry.isFile() && /\.(txt|csv|json|log)$/.test(entry.name)))) entries.push({ name: entry.name, directory: entry.isDirectory() });
        if (++scanned >= 1000 || entries.length >= 200) break;
      }
      return { entries: entries.sort((a, b) => a.name.localeCompare(b.name)), limit: 200 };
    } finally { await fd.close(); }
  }
  async download(path: string) {
    const fd = await this.resolve(path, true);
    try {
      const s = await fd.stat();
      if (!s.isFile() || s.nlink !== 1 || s.size > 1024 * 1024) throw new Error('Unsafe file');
      const buffer = Buffer.alloc(1024 * 1024 + 1); const { bytesRead } = await fd.read(buffer, 0, buffer.length, 0);
      if (bytesRead > 1024 * 1024) throw new Error('File too large');
      return buffer.subarray(0, bytesRead);
    } finally { await fd.close(); }
  }
  close() { return this.root.close(); }
}
