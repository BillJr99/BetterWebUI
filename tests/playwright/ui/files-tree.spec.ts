/**
 * File tree panel — toggle Files pane via the header button.
 */
import { test, expect } from '@playwright/test';
import { gotoApp, dismissOnboardingIfPresent, ensureConfigured } from './helpers/ui-helpers';

test.beforeEach(async ({ page, request }) => {
  await ensureConfigured(request);
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
});

test('Files pane toggles via the header button', async ({ page }) => {
  const btn = page.locator('#toggle-files-btn');
  await btn.click();
  // Right rail or files-pane becomes visible.
  await expect(page.locator('#files-pane')).toBeVisible();
  await btn.click();
  // Toggle back hides it.
  await expect(page.locator('#files-pane')).toBeHidden();
});
