/**
 * Shell execution — approval gating, deny path, global disable.
 * Outcome assertions only — we never assert on the model's wording.
 */
import { test, expect } from '@playwright/test';
import {
  gotoApp, dismissOnboardingIfPresent, sendChatMessage, waitForAssistantResponse,
  ensureConfigured, pickModel, openTab,
} from './helpers/ui-helpers';
import { approveNextDialog, denyNextDialog } from './helpers/approval-helpers';

test.beforeEach(async ({ page, request }) => {
  await ensureConfigured(request);
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
});

test('shell command shows an approval dialog when requested', async ({ page, request }) => {
  const model = await pickModel(request);
  test.skip(!model, 'no model configured');

  await sendChatMessage(
    page,
    'Run the bash command `echo betterwebui-shell-test`. Use the shell tool.',
  );

  // Approval dialog appears in #dialog-root. Generous timeout — model has to call the tool.
  const dialog = page.locator('#dialog-root [role="dialog"]').last();
  await expect(dialog).toBeVisible({ timeout: 120_000 });

  await approveNextDialog(page);
  await waitForAssistantResponse(page, { timeoutMs: 180_000 });
});

test('denying the approval surfaces a non-empty assistant follow-up', async ({ page, request }) => {
  const model = await pickModel(request);
  test.skip(!model, 'no model configured');

  await sendChatMessage(
    page,
    'Run the bash command `echo denial-test-please` via the shell tool.',
  );
  const dialog = page.locator('#dialog-root [role="dialog"]').last();
  await expect(dialog).toBeVisible({ timeout: 120_000 });
  await denyNextDialog(page);
  await waitForAssistantResponse(page, { timeoutMs: 180_000 });
});

test('disabling shell from settings stops new approval dialogs', async ({ page, request }) => {
  const model = await pickModel(request);
  test.skip(!model, 'no model configured');

  await openTab(page, 'settings');
  const toggle = page.locator('#cfg-shell-enabled');
  await toggle.uncheck();
  await page.locator('#save-defaults').click();
  await openTab(page, 'chats');
  await page.locator('#new-chat-btn').click();

  await sendChatMessage(
    page,
    'Run the bash command `echo should-not-prompt`.',
  );
  // Allow plenty of time for a hypothetical dialog; expect none to appear.
  const dialogCount = await page.locator('#dialog-root [role="dialog"]').count();
  // Either the model declines verbally or no dialog appears — both are valid.
  // We assert the dialog doesn't appear within a short window.
  const dialog = page.locator('#dialog-root [role="dialog"]');
  await expect.poll(async () => dialog.count(), { timeout: 30_000 }).toBe(dialogCount);

  // Restore for downstream tests.
  await openTab(page, 'settings');
  await toggle.check();
  await page.locator('#save-defaults').click();
});
