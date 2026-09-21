import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { createApp } from '../../build/src/app.js';
import { credential } from '../../build/src/security.js';

// Synthetic, loopback-only visual fixture. Never imported by production startup.
let app, url;
const secret = 'a'.repeat(43);
test.beforeEach(async () => {
  const options = { origin: 'http://127.0.0.1:0', credentials: [await credential('123', secret)],
    identity: async ip => { if (ip !== '127.0.0.1') throw Error(); return { userId: '123', nodeId: 'synthetic' }; },
    audit: { record: async () => {}, list: () => [] }, tailscale: async () => ({ state: 'Test fixture', addresses: [] }),
    collector: async () => ({ observedAt: new Date().toISOString(), services: [{ name: 'PostgreSQL', state: 'healthy', detail: 'Synthetic observation' }], backup: { state: 'healthy', lastSuccess: new Date().toISOString() } }) };
  app = createApp(options); await app.listen({ host: '127.0.0.1', port: 0 });
  url = `http://127.0.0.1:${app.server.address().port}`; options.origin = url;
});
test.afterEach(async () => { await app?.close(); });
async function signIn(page) {
  await page.goto(url); await page.getByLabel('Administration token', { exact: true }).fill(secret);
  await page.getByRole('button', { name: 'Confirm sign in' }).click(); await expect(page.locator('#workspace')).toBeVisible();
}
test('project command, validation, live observations and logout clearing', async ({ page }) => {
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  await signIn(page);
  await expect(page.locator('#service-summary')).toContainText('Synthetic observation');
  await expect(page.locator('#collector-status')).toContainText('Observed');
  await page.getByRole('link', { name: 'Connect a project', exact: true }).click();
  await page.getByLabel('Public API URL').fill('https://api.example.test');
  await page.getByLabel('Application origin').fill('https://app.example.test');
  await page.getByRole('button', { name: 'Prepare connection' }).click();
  await expect(page.locator('#project-command')).toContainText('--name vexvortex --table vexvortex_items');
  await expect(page.locator('#project-command')).toContainText('--output "$HOME/.macserver-private/vexvortex"');
  await page.getByLabel('Public API URL').fill('https://api.example.test/invalid');
  await page.getByRole('button', { name: 'Prepare connection' }).click();
  await expect(page.locator('#project-error')).toContainText('without paths');
  await expect(page.locator('#project-result')).toBeHidden();
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page.locator('#project-command')).toBeEmpty();
  expect(errors).toEqual([]);
});
for (const width of [390, 1440]) {
  test(`branded workspace is accessible at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 }); await signIn(page);
    for (const theme of ['dark', 'light']) {
      if (await page.locator('html').getAttribute('data-theme') !== theme) await page.getByRole('button', { name: theme === 'light' ? 'Light theme' : 'Dark theme' }).click();
      for (const view of ['overview', 'connect', 'services', 'backups']) {
        await page.locator(`nav a[href="#${view}"]`).click();
        await expect(page.locator(`#${view}`)).toBeVisible();
        await page.evaluate(() => document.fonts.ready);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        expect((await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations).toEqual([]);
        if (view === 'connect' && theme === 'dark') await page.screenshot({ path: `../../build/admin-${width}.png`, fullPage: true });
      }
    }
  });
}
