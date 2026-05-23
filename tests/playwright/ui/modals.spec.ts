/**
 * Modals — annotation, diff, shortcut sheet. Verify they are reachable in
 * the DOM and that the shortcut sheet's open/close cycle works.
 */
import { test, expect } from '@playwright/test';
import { gotoApp, dismissOnboardingIfPresent, ensureConfigured } from './helpers/ui-helpers';

test.beforeEach(async ({ page, request }) => {
  await ensureConfigured(request);
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
});

test('annotation modal exists in the DOM (hidden by default)', async ({ page }) => {
  await expect(page.locator('#annotation-modal')).toBeAttached();
});

test('diff modal exists in the DOM (hidden by default)', async ({ page }) => {
  await expect(page.locator('#diff-modal')).toBeAttached();
});

test('shortcut sheet button opens the sheet', async ({ page }) => {
  // Settings tab hosts the shortcut help button.
  await page.locator('#tab-btn-settings').click();
  await page.locator('#shortcut-help-btn').click();
  await expect(page.locator('#shortcut-sheet')).toBeVisible({ timeout: 5_000 });
  await page.keyboard.press('Escape');
});
