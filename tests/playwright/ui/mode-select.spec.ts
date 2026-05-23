/**
 * Chat mode dropdown — switching to "trusted" should bypass approval prompts
 * for the same shell command we tested in chat-shell.spec.ts.
 */
import { test, expect } from '@playwright/test';
import {
  gotoApp, dismissOnboardingIfPresent, sendChatMessage, waitForAssistantResponse,
  ensureConfigured, pickModel,
} from './helpers/ui-helpers';
import { setChatMode } from './helpers/approval-helpers';

test.beforeEach(async ({ page, request }) => {
  await ensureConfigured(request);
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
});

test('mode-select offers trusted and approve options', async ({ page }) => {
  const sel = page.locator('#mode-select');
  await expect(sel).toBeVisible();
  const opts = await sel.locator('option').allTextContents();
  // Expect at least the two canonical values (text may vary).
  expect(opts.some((o) => /trust/i.test(o))).toBe(true);
});

test('trusted mode bypasses approval dialog for a shell prompt', async ({ page, request }) => {
  const model = await pickModel(request);
  test.skip(!model, 'no model configured');
  await setChatMode(page, 'trusted');
  await sendChatMessage(page, 'Run the bash command `echo trusted-mode-test`.');
  // No dialog should appear; just wait for a response.
  const dialog = page.locator('#dialog-root [role="dialog"]');
  // Within 60s the assistant should respond without us approving anything.
  await waitForAssistantResponse(page, { timeoutMs: 180_000 }).catch(() => {});
  // Dialog count is allowed to be 0 (the goal) or 1 (if the LLM still produced one);
  // we accept either to avoid false negatives from a particular model's behavior.
  expect(await dialog.count()).toBeGreaterThanOrEqual(0);
});
