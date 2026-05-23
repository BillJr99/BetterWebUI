/**
 * File bundles — Files tab. Bundles attach to chats and are managed via the
 * sidebar. We verify the tab opens and the new-bundle button is present;
 * actual bundle creation involves a multi-step modal that varies by build.
 */
import { test, expect } from '@playwright/test';
import { gotoApp, dismissOnboardingIfPresent, openTab, ensureConfigured } from './helpers/ui-helpers';

test.beforeEach(async ({ page, request }) => {
  await ensureConfigured(request);
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
  await openTab(page, 'files');
});

test('Files tab opens with new-bundle button', async ({ page }) => {
  await expect(page.locator('#new-bundle-btn')).toBeVisible();
  await expect(page.locator('#bundle-list')).toBeVisible();
});

test('Files tab quota indicator renders', async ({ page }) => {
  // Quota element exists even if empty.
  await expect(page.locator('#bundles-quota')).toBeAttached();
});
