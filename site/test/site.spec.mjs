import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { readdir, readFile } from 'node:fs/promises';

test('navigation, examples, accordion, and motion controls work', async ({ page }) => {
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Your backend.Your machine.');
  await page.getByRole('button', { name: 'Query', exact: true }).click();
  await expect(page.locator('#code-example')).toContainText(".from('projects')");
  await page.getByRole('button', { name: 'Install', exact: false }).click();
  await expect(page.locator('#install-panel')).toBeVisible();
  await expect(page.locator('#prepare-panel')).toBeHidden();
  await page.getByRole('button', { name: 'Pause motion' }).click();
  await expect(page.getByRole('button', { name: 'Resume motion' })).toHaveAttribute('aria-pressed', 'true');
  expect(errors).toEqual([]);
});
for (const width of [390, 768, 1440]) {
  test(`responsive layout and accessibility at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 }); await page.goto('/');
    await page.evaluate(() => document.fonts.ready);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const lines = await page.locator('h1').evaluate(el => el.clientHeight / parseFloat(getComputedStyle(el).lineHeight));
    expect(lines).toBeLessThan(3.1);
    expect((await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations).toEqual([]);
    await page.screenshot({ path: `../build/site-${width}.png`, fullPage: true });
  });
}
test('all local guides and assets resolve; installation guide is accessible', async ({ page, request }) => {
  const root = new URL('../../build/site/', import.meta.url);
  for (const name of (await readdir(root)).filter(name => name.endsWith('.html'))) {
    const html = await readFile(new URL(name, root), 'utf8');
    for (const match of html.matchAll(/(?:href|src)="([^"#]+)"/g)) {
      if (/^(https?:|mailto:)/.test(match[1])) continue;
      expect((await request.get('/' + match[1].split('#')[0])).ok(), `${name}: ${match[1]}`).toBe(true);
    }
  }
  await page.goto('/getting-started.html');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Install MacServer');
  expect((await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze()).violations).toEqual([]);
});
