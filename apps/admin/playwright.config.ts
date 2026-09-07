import { defineConfig } from '@playwright/test';
import { existsSync } from 'node:fs';

export default defineConfig({
  testDir: './test/browser', workers: 1, fullyParallel: false, timeout: 45_000,
  reporter: 'list', outputDir: 'test-results',
  use: { browserName: 'chromium', ignoreHTTPSErrors: true, viewport: { width: 1440, height: 1000 },
    launchOptions: { executablePath: process.env.ADMIN_TEST_CHROMIUM ?? (existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : undefined) },
    screenshot: 'only-on-failure', trace: 'off' },
});
