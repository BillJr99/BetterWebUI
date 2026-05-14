/**
 * local.config.ts — Playwright configuration for local (no-Docker) test runs.
 *
 * Runs both the service-integration tests and the end-to-end chat tests
 * against locally running services. Services must already be started (by
 * scripts/run-e2e-local.sh or manually) before invoking this config.
 *
 * Usage:
 *   npx playwright test --config local.config.ts
 */
import { defineConfig } from '@playwright/test';

export default defineConfig({
  // Run both integration (service API) and e2e (chat) tests.
  testMatch: [
    'integration/**/*.spec.ts',
    'e2e/**/*.spec.ts',
  ],
  timeout: 180_000,
  retries: 1,
  use: {
    baseURL: process.env.BETTERWEBUI_URL ?? 'http://localhost:8765',
    trace: 'on-first-retry',
  },
  globalSetup: './localSetup.ts',
  // No globalTeardown — services are managed by the shell script's trap.
});
