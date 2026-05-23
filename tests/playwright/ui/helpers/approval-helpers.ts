/**
 * approval-helpers.ts — Drive the shell-command / file / save approval dialogs.
 *
 * BetterWebUI renders dialogs into #dialog-root (see static/index.html).
 * The exact internal class names may evolve; these helpers look up by ARIA
 * role + button text so they survive incidental CSS refactors.
 */
import { Page, expect } from '@playwright/test';

async function waitForDialog(page: Page, timeoutMs = 60_000) {
  const dialog = page.locator('#dialog-root [role="dialog"]').last();
  await expect(dialog).toBeVisible({ timeout: timeoutMs });
  return dialog;
}

export async function approveNextDialog(page: Page, timeoutMs?: number): Promise<void> {
  const dialog = await waitForDialog(page, timeoutMs);
  // Buttons are typically labelled "Approve" / "Run" / "Accept" / "Allow".
  const approve = dialog.locator(
    'button:has-text("Approve"), button:has-text("Run"), button:has-text("Accept"), button:has-text("Allow")',
  ).first();
  await approve.click();
}

export async function denyNextDialog(page: Page, timeoutMs?: number): Promise<void> {
  const dialog = await waitForDialog(page, timeoutMs);
  const deny = dialog.locator(
    'button:has-text("Deny"), button:has-text("Reject"), button:has-text("Cancel")',
  ).first();
  await deny.click();
}

/**
 * Set the chat mode dropdown in the header. Values are typically
 * "approve" (default) and "trusted" (skip approvals).
 */
export async function setChatMode(page: Page, value: 'trusted' | 'approve'): Promise<void> {
  const select = page.locator('#mode-select');
  await select.selectOption(value);
}
