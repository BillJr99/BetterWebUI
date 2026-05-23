/**
 * ui.config.ts — Browser-driven Playwright UI tests for BetterWebUI.
 *
 * Drives the real UI through clicks and typing, asserts outcomes (not exact
 * model text). Services must already be running — start them via
 * scripts/run-all-tests.sh or scripts/run-e2e-local.sh.
 *
 * Usage:
 *   npx playwright test --config ui.config.ts
 *   npx playwright test --config ui.config.ts --headed
 */
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './ui',
  // 8 min per test: chat-basic does up to 2 model round-trips per case
  // (new-chat test) and a cold tinyllama on a 2-core CI runner has been
  // observed at 150-200 s for a single short reply. 480 s gives ~2× headroom.
  timeout: 480_000,
  expect: { timeout: 30_000 },
  retries: process.env.CI ? 1 : 0,
  workers: 1,             // UI tests share state (config.json, conversations) — serialize
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'ui-report' }]],
  use: {
    baseURL: process.env.BETTERWEBUI_URL ?? 'http://localhost:8765',
    trace: 'on-first-retry',
    video: 'retain-on-failure',
    screenshot: 'only-on-failure',
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  globalSetup: './localSetup.ts',
});
