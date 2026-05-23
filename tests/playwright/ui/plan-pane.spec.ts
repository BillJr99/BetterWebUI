/**
 * Task plan pane — header button toggles it; list renders.
 */
import { test, expect } from '@playwright/test';
import { gotoApp, dismissOnboardingIfPresent, ensureConfigured } from './helpers/ui-helpers';

test.beforeEach(async ({ page, request }) => {
  await ensureConfigured(request);
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
});

test('plan-pane toggle button shows and hides the pane', async ({ page }) => {
  const btn = page.locator('#toggle-plan-btn');
  await btn.click();
  await expect(page.locator('#plan-pane')).toBeVisible();
  await expect(page.locator('#plan-list')).toBeAttached();
  // Close via the X button.
  await page.locator('#plan-pane-close').click();
  await expect(page.locator('#plan-pane')).toBeHidden();
});
