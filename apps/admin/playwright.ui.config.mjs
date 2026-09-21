import { defineConfig } from '@playwright/test';
import { existsSync } from 'node:fs';
const edge = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
export default defineConfig({
  testDir: './test/ui', workers: 1, timeout: 30000, reporter: 'list',
  use: { reducedMotion: 'reduce', launchOptions: process.platform === 'win32' && existsSync(edge) ? { executablePath: edge } : {} }
});
