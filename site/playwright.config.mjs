import { defineConfig } from '@playwright/test';
import { existsSync } from 'node:fs';
const edge = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
export default defineConfig({
  testDir: './test', workers: 1, timeout: 30000,
  use: { baseURL: 'http://127.0.0.1:4173', reducedMotion: 'reduce', launchOptions: process.platform === 'win32' && existsSync(edge) ? { executablePath: edge } : {} },
  webServer: { command: 'node serve.mjs', port: 4173, reuseExistingServer: false },
  reporter: 'list'
});
