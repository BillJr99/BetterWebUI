/**
 * approval-helpers.ts — Drive the shell-command / file / save approval dialogs.
 *
 * BetterWebUI renders dialogs into #dialog-root (see static/index.html).
 * The exact internal class names may evolve; these helpers look up by ARIA
 * role + button text so they survive incidental CSS refactors.
 */
import { Page, expect } from '@playwright/test';

async function waitForDialog(page: Page, timeoutMs = 60_000) {
  console.log(`[dialog] waiting for dialog (timeout=${timeoutMs / 1000}s)`);
  const dialog = page.locator('#dialog-root [role="dialog"]').last();
  await expect(dialog).toBeVisible({ timeout: timeoutMs }).catch(async (err) => {
    const html = await page.locator('#dialog-root').innerHTML().catch(() => '<unavailable>');
    console.log(`[dialog:ERR] no dialog appeared within ${timeoutMs / 1000}s`);
    console.log(`[dialog:ERR] #dialog-root innerHTML: ${html.slice(0, 400)}`);
    throw err;
  });
  const text = await dialog.innerText().catch(() => '?');
  console.log(`[dialog] dialog visible. text (first 200): "${text.slice(0, 200)}"`);
  return dialog;
}

export async function approveNextDialog(page: Page, timeoutMs?: number): Promise<void> {
  const dialog = await waitForDialog(page, timeoutMs);
  // Buttons are typically labelled "Approve" / "Run" / "Accept" / "Allow".
  const approve = dialog.locator(
    'button:has-text("Approve"), button:has-text("Run"), button:has-text("Accept"), button:has-text("Allow")',
  ).first();
  console.log(`[dialog] clicking approve`);
  await approve.click();
  console.log(`[dialog] approved`);
}

export async function denyNextDialog(page: Page, timeoutMs?: number): Promise<void> {
  const dialog = await waitForDialog(page, timeoutMs);
  const deny = dialog.locator(
    'button:has-text("Deny"), button:has-text("Reject"), button:has-text("Cancel")',
  ).first();
  console.log(`[dialog] clicking deny`);
  await deny.click();
  console.log(`[dialog] denied`);
}

/**
 * Set the chat mode dropdown in the header. Values are typically
 * "approve" (default) and "trusted" (skip approvals).
 */
export async function setChatMode(page: Page, value: 'trusted' | 'approve'): Promise<void> {
  const select = page.locator('#mode-select');
  await select.selectOption(value);
}
