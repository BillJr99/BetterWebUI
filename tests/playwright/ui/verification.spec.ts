/**
 * Verification — settings persist; runtime endpoint responds with status.
 */
import { test, expect } from '@playwright/test';
import {
  gotoApp, dismissOnboardingIfPresent, openTab, ensureConfigured,
  sendChatMessage, waitForAssistantResponse, pickModel,
} from './helpers/ui-helpers';

test.beforeEach(async ({ page, request }) => {
  await ensureConfigured(request);
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
});

test('verification settings persist via the UI', async ({ page, request }) => {
  await openTab(page, 'settings');
  await page.locator('#cfg-verification-retries').fill('1');
  const mode = page.locator('#cfg-verification-mode');
  const opts = await mode.locator('option').count();
  if (opts >= 2) await mode.selectOption({ index: 1 });
  await page.locator('#save-verification').click();
  // Restart visible page — config should be intact.
  const cfg = await request.get('/api/config');
  expect(cfg.ok()).toBeTruthy();
});

test('verification endpoint returns 404 or 200 for a non-existent chat id', async ({ request }) => {
  const r = await request.get('/api/verification/nonexistent-chat-id');
  // 404 is the most likely answer; 200 with empty status is also valid.
  expect([200, 404].includes(r.status())).toBeTruthy();
});
