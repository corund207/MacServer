import { readFile } from 'node:fs/promises';
import { validateConfig } from './gateway.mjs';
try {
  validateConfig(JSON.parse(await readFile(process.argv[2] || '/run/secrets/ingress.json', 'utf8')));
  console.log('PASS: scoped ingress configuration; no credentials printed.');
} catch {
  console.error('Ingress configuration refused. Review IDs, keys, HTTPS origins, table methods, and limits.');
  process.exitCode = 1;
}
