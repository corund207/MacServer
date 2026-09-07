import { mkdir, writeFile, lstat } from 'node:fs/promises';
import { isAbsolute, dirname } from 'node:path';
import { credential, token } from './security.js';

// Creates a NEW private directory; never rotates or overwrites an existing credential.
const [userId, directory] = process.argv.slice(2);
try {
  if (!/^[1-9][0-9]{0,15}$/.test(userId ?? '') || !directory || !isAbsolute(directory)) throw new Error();
  let parent = dirname(directory);
  while (true) { const s = await lstat(parent); if (!s.isDirectory() || s.isSymbolicLink()) throw new Error(); if (parent === '/') break; parent = dirname(parent); }
  await mkdir(directory, { mode: 0o700 });
  const secret = token();
  await writeFile(`${directory}/credentials.json`, JSON.stringify([await credential(userId, secret)]) + '\n', { mode: 0o600, flag: 'wx' });
  await writeFile(`${directory}/initial-token.txt`, secret + '\n', { mode: 0o600, flag: 'wx' });
  process.stdout.write('Created private credentials and initial-token.txt; retrieve the token privately. No service started.\n');
} catch { process.stderr.write('Provisioning refused; use a numeric Tailscale user ID and a new absolute directory under a trusted parent.\n'); process.exitCode = 1; }
