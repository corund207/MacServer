import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { browserFixture } from '../browser-fixture.mjs';

let fixture: Awaited<ReturnType<typeof browserFixture>>;
test.beforeEach(async () => { fixture = await browserFixture(); });
test.afterEach(async () => { await fixture.close(); });
async function signIn(page: Page) {
  await page.goto(fixture.url);
  await page.getByLabel('Administration token', { exact: true }).fill(fixture.secret);
  await page.getByRole('button', { name: 'Confirm sign in' }).click();
  await expect(page.locator('#workspace')).toBeVisible();
  await expect(page.locator('#metric-cards')).toContainText('Allocated memory');
}
test('login, independent credential, secure cookie, reload and logout', async ({ page, context }) => {
  await page.goto(fixture.url);
  await page.getByLabel('Administration token', { exact: true }).fill('a'.repeat(43));
  await page.getByRole('button', { name: 'Confirm sign in' }).click();
  await expect(page.getByRole('status')).toHaveText('Access denied');
  await page.getByLabel('Administration token', { exact: true }).fill(fixture.secret);
  await page.getByRole('button', { name: 'Confirm sign in' }).click();
  await expect(page.locator('#metric-cards')).toContainText('CPU utilization');
  const cookie = (await context.cookies()).find(c => c.name === '__Host-macserver')!;
  expect(cookie.httpOnly).toBe(true); expect(cookie.secure).toBe(true); expect(cookie.sameSite).toBe('Strict');
  expect(await page.evaluate(() => document.cookie)).not.toContain('__Host-macserver');
  await page.reload(); await expect(page.locator('#workspace')).toBeVisible();
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page.locator('#login')).toBeVisible();
  await expect(page.locator('#metric-cards')).toBeEmpty();
  expect((await page.request.get(fixture.url + '/api/overview')).status()).toBe(401);
});
test('all views, dark/light themes and WCAG accessibility', async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await signIn(page);
  for (const theme of ['dark', 'light']) {
    if (await page.locator('html').getAttribute('data-theme') !== theme) await page.getByRole('button', { name: theme === 'dark' ? 'Dark theme' : 'Light theme' }).click();
    for (const view of ['overview', 'services', 'metrics', 'network', 'backups', 'files', 'database', 'updates', 'audit', 'configuration']) {
      await page.locator(`nav a[href="#${view}"]`).click(); await expect(page.locator(`#${view}`)).toBeVisible();
      const report = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
      expect(report.violations, `${theme}/${view}: ${JSON.stringify(report.violations.map(v => ({ id: v.id, nodes: v.nodes.map(n => n.target) })))}`).toEqual([]);
    }
  }
  await page.reload(); await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  expect(errors).toEqual([]);
});
test('mobile layout, keyboard access and disabled privileged controls', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 }); await page.goto(fixture.url);
  await page.keyboard.press('Tab'); await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused();
  await page.keyboard.press('Enter'); await expect(page.locator('main')).toBeFocused();
  const loginAxe = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze(); expect(loginAxe.violations).toEqual([]);
  await signIn(page);
  for (const view of ['overview', 'metrics', 'files', 'database', 'backups', 'updates']) {
    await page.locator(`nav a[href="#${view}"]`).click();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await expect(page.locator('nav a[aria-current="page"]')).toHaveAttribute('href', '#' + view);
  }
  await expect(page.getByRole('button', { name: 'Apply update · unavailable' })).toBeDisabled();
  await page.screenshot({ path: 'test-results/mobile.png', fullPage: true });
});
test('curated file navigation/download and audit filtering', async ({ page }) => {
  await signIn(page); await page.locator('nav a[href="#files"]').click();
  await page.getByRole('button', { name: 'Open reports', exact: true }).click();
  const downloaded = page.waitForEvent('download'); await page.getByRole('link', { name: 'Download status.txt' }).click();
  const download = await downloaded; expect(await download.failure()).toBeNull(); expect(download.suggestedFilename()).toBe('admin-export.txt');
  await page.getByRole('button', { name: 'Up one folder' }).click(); await expect(page.locator('#file-path')).toHaveText('/');
  await page.locator('nav a[href="#audit"]').click(); await expect(page.locator('#audit-rows')).toContainText('file-download');
  await page.getByLabel('Outcome', { exact: true }).selectOption('denied'); await expect(page.locator('#audit-rows')).toContainText('No events');
  await page.getByLabel('Outcome', { exact: true }).selectOption('ok'); await expect(page.locator('#audit-rows')).toContainText('login');
});
test('stale data and session expiry stay honest after network failure', async ({ page }) => {
  await page.clock.install(); await signIn(page);
  await page.route('**/api/overview', route => route.abort('failed'));
  await page.clock.setSystemTime(Date.now() + 40_000); await page.clock.runFor(1000);
  await expect(page.locator('#freshness')).toContainText('Stale telemetry');
  await page.getByRole('button', { name: 'Refresh', exact: true }).click(); await expect(page.getByRole('status')).toContainText('stale');
  await page.clock.setSystemTime(Date.now() + 16 * 60_000); await page.clock.runFor(1000);
  await expect(page.locator('#login')).toBeVisible(); await expect(page.getByRole('status')).toContainText('expired');
  await expect(page.locator('#metric-cards')).toBeEmpty();
});
