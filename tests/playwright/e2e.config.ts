import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  timeout: 180_000,
  retries: 1,
  use: {
    baseURL: process.env.BETTERWEBUI_URL ?? 'http://localhost:8080',
    trace: 'on-first-retry',
  },
  globalSetup: './e2eSetup.ts',
  globalTeardown: './e2eTeardown.ts',
});
