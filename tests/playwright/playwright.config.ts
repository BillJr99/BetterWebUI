import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './integration',
  timeout: 120_000,
  retries: 1,
  use: {
    baseURL: process.env.BETTERWEBUI_URL ?? 'http://localhost:8080',
    trace: 'on-first-retry',
  },
  globalSetup: './globalSetup.ts',
  globalTeardown: './globalTeardown.ts',
});
