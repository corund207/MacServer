import { open } from 'node:fs/promises';
import { constants } from 'node:fs';
import { createGateway } from './gateway.mjs';

const file = await open('/run/secrets/ingress.json', constants.O_RDONLY | constants.O_NOFOLLOW);
let config;
try {
  const info = await file.stat();
  if (!info.isFile() || info.nlink !== 1 || info.size > 65536 || info.uid !== 0 || (info.mode & 0o007) || (info.mode & 0o022)) throw Error('Unsafe ingress configuration');
  config = JSON.parse(await file.readFile('utf8'));
} finally { await file.close(); }
const gateway = createGateway(config);
gateway.listen(8080, '0.0.0.0');
process.on('SIGTERM', () => { gateway.close(); setTimeout(() => process.exit(1), 20000).unref(); });
