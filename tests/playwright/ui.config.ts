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
  // 16 min per test: chat-basic does up to 2 model round-trips per case
  // (new-chat test) and chat-multimodal sends a base64 image that bloats
  // tinyllama's context to ~5 min/turn on a 2-core CI runner. 960 s = 2×
  // the per-turn response budget, leaving room for setup + a second turn.
  timeout: 960_000,
  expect: { timeout: 30_000 },
  retries: 0,             // No retries: slow tests already use generous timeouts;
                          // retries double CI time without adding diagnostic value.
  workers: 1,             // UI tests share state (config.json, conversations) — serialize
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'ui-report' }]],
  use: {
    baseURL: process.env.BETTERWEBUI_URL ?? 'http://localhost:8765',
    trace: 'on',          // Always capture traces — cheap to produce, invaluable to debug.
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
