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
  // openTab is called inside each test (matches memory.spec.ts pattern that
  // reliably resolves the sidebar layout before the assertions run).
});

test('Files tab opens with new-bundle button', async ({ page }) => {
  await openTab(page, 'files');
  await expect(page.locator('#new-bundle-btn')).toBeVisible();
  // #bundle-list starts empty (tab click goes through wireTabs(), not
  // switchTab(), so renderBundleList() isn't called). An empty <ul> has zero
  // height and isn't "visible" — confirm it's attached instead.
  await expect(page.locator('#bundle-list')).toBeAttached();
});

test('Files tab quota indicator renders', async ({ page }) => {
  await openTab(page, 'files');
  // Quota element exists even if empty.
  await expect(page.locator('#bundles-quota')).toBeAttached();
});
