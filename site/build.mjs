import { mkdir, readFile, writeFile, cp } from 'node:fs/promises';
import { resolve, dirname, basename } from 'node:path';
import { fileURLToPath } from 'node:url';
import { marked } from 'marked';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');
// SITE_OUT lets a local build avoid a locked build/ folder; CI and Pages use the default.
const out = resolve(process.env.SITE_OUT ?? resolve(root, 'build/site'));
await mkdir(out, { recursive: true });
await cp(resolve(here, 'public'), out, { recursive: true });
await mkdir(resolve(out, 'assets'), { recursive: true });
for (const name of ['gsap.min.js', 'ScrollTrigger.min.js']) {
  await cp(resolve(here, 'node_modules/gsap/dist', name), resolve(out, 'assets', name));
}
for (const weight of [400, 500, 600, 700]) {
  const name = `outfit-latin-${weight}-normal.woff2`;
  await cp(resolve(here, 'node_modules/@fontsource/outfit/files', name), resolve(out, 'assets', name));
}
await cp(resolve(here, 'node_modules/@fontsource/outfit/LICENSE'), resolve(out, 'assets/OUTFIT-LICENSE.txt'));
await writeFile(resolve(out, 'assets/GSAP-LICENSE.txt'), 'GSAP 3.15.0 — Copyright GreenSock. Standard no-charge license: https://gsap.com/standard-license/\n');
const guides = ['GETTING-STARTED', 'PUBLIC-APPS', 'DOWNLOAD-REVIEW', 'INSTALL', 'DEPLOYMENT', 'DATA-SERVICES', 'MIGRATION', 'BACKUP-RESTORE', 'ADMIN', 'ADMIN-IDENTITY', 'DASHBOARD', 'UPDATE-ROLLBACK', 'FINAL-VERIFICATION', 'DEVELOPMENT'];
const template = await readFile(resolve(here, 'guide.html'), 'utf8');
for (const name of guides) {
  const source = await readFile(resolve(root, 'docs', `${name}.md`), 'utf8');
  // Only trusted, tracked operator documentation is rendered, never user input.
  let html = marked.parse(source);
  html = html.replace(/href="([^"]+\.md)(#[^"]*)?"/g, (_match, path, hash = '') => {
    const target = basename(path, '.md');
    return guides.includes(target) ? `href="${target.toLowerCase()}.html${hash}"` : `href="https://github.com/corund207/MacServer/blob/main/${path.replace(/^\.\.\//, '')}${hash}"`;
  });
  const title = source.split('\n')[0].replace(/^#\s*/, '');
  await writeFile(resolve(out, `${name.toLowerCase()}.html`), template.replaceAll('{{TITLE}}', title).replace('{{CONTENT}}', html));
}
await writeFile(resolve(out, '.nojekyll'), '');
console.log(`Built static installation site in ${out}. No secrets or appliance state included.`);
