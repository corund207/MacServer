import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './test/browser', timeout: 20_000, expect: { timeout: 5_000 }, workers: 1,
  use: { baseURL: 'http://127.0.0.1:7461', browserName: 'chromium', trace: 'retain-on-failure' },
  webServer: { command: 'node test/browser-fixture.mjs', port: 7461, reuseExistingServer: false, timeout: 15_000 },
  reporter: [['list']]
});
