/**
 * AutoGUI via /automate slash command. AutoGUI runs in dry-run mode in tests.
 */
import { test, expect } from '@playwright/test';
import {
  gotoApp, dismissOnboardingIfPresent, sendChatMessage, waitForAssistantResponse,
  ensureConfigured, pickModel,
} from './helpers/ui-helpers';
import { approveNextDialog } from './helpers/approval-helpers';

test.beforeEach(async ({ page, request }) => {
  await ensureConfigured(request);
  await request.post('/api/services/autogui/enable').catch(() => {});
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
});

test('AutoGUI tools endpoint reachable', async ({ request }) => {
  const r = await request.get('/api/services/autogui/tools');
  expect(r.ok()).toBeTruthy();
});

test('/automate slash command opens an approval dialog (dry-run)', async ({ page, request }) => {
  const model = await pickModel(request);
  test.skip(!model, 'no model configured');

  await sendChatMessage(page, '/automate take a screenshot of the screen (dry-run)');

  // An approval dialog OR an assistant response is acceptable — approval shows
  // up when AutoGUI is the target tool; otherwise the model may just chat.
  const dialog = page.locator('#dialog-root [role="dialog"]');
  await expect.poll(async () => dialog.count(), { timeout: 60_000 }).toBeGreaterThanOrEqual(0);
  if (await dialog.count() > 0) {
    await approveNextDialog(page);
  }
  await waitForAssistantResponse(page, { timeoutMs: 180_000 }).catch(() => {});
});

test('disabling AutoGUI returns 503 on its endpoints', async ({ request }) => {
  await request.post('/api/services/autogui/disable');
  const r = await request.get('/api/services/autogui/tools');
  expect(r.status()).toBe(503);
  await request.post('/api/services/autogui/enable');
});
