import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { mkdir } from 'node:fs/promises';

test.beforeAll(async () => mkdir('../../.impeccable/review', { recursive: true }));

test('renders real fixture evidence and exposes no controls', async ({ page }) => {
  await page.goto('/'); await expect(page.locator('#verdict-label')).toHaveText('ATTENTION');
  await expect(page.locator('#services li')).toHaveCount(4); await expect(page.locator('#backup')).toHaveText('degraded');
  await expect(page.locator('button,input,select,textarea')).toHaveCount(0);
  await expect(page.locator('body')).not.toContainText(/token|password|secret/i);
  await expect(page.locator('#source')).toHaveText('Host: Synthetic browser fixture · Collector: Synthetic browser fixture');
  const findings = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
  expect(findings.violations).toEqual([]);
});

test('labels missing collector evidence unverified rather than stale', async ({ page }) => {
  await page.route('**/api/status', async route => {
    const response = await route.fetch(), value = await response.json();
    value.evidence = { observedAt: null, ageSeconds: null, stale: true, collector: 'unavailable',
      tailscale: { state: 'unavailable', detail: 'No valid collector snapshot' },
      backup: { state: 'unavailable', detail: 'No valid collector snapshot', lastSuccess: null },
      requests: { state: 'unavailable', perMinute: null, errorsPerMinute: null, detail: 'No valid collector snapshot' },
      services: [], alerts: [] };
    await route.fulfill({ response, json: value });
  });
  await page.goto('/'); await expect(page.locator('#verdict-label')).toHaveText('UNVERIFIED');
  await expect(page.locator('#evidence-age')).toHaveText('Unavailable');
  await expect(page.locator('#verdict-detail')).toContainText('No valid collector snapshot');
  await expect(page.locator('body')).not.toContainText(/stale|null/i);
});

test('fits kiosk and narrow display without horizontal overflow', async ({ page }) => {
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport); await page.goto('/'); await expect(page.locator('#verdict-label')).toHaveText('ATTENTION');
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    expect(overflow).toBe(false);
    await page.screenshot({ path: `../../.impeccable/review/${viewport.width === 1440 ? 'desktop' : 'mobile'}.png`, fullPage: true });
  }
});

test('keeps last evidence visible when local API fails', async ({ page }) => {
  await page.goto('/'); await expect(page.locator('#cpu')).toContainText('%');
  await page.route('**/api/status', route => route.abort());
  await page.evaluate(() => fetch('/api/status').catch(() => {}));
  await page.waitForTimeout(10_100);
  await expect(page.locator('#verdict-label')).toHaveText('DISPLAY DEGRADED');
  await expect(page.locator('#cpu')).toContainText('%');
});
