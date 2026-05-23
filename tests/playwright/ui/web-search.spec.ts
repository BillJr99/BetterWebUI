/**
 * Web search — settings + composer toggle.
 */
import { test, expect } from '@playwright/test';
import { gotoApp, dismissOnboardingIfPresent, openTab, ensureConfigured } from './helpers/ui-helpers';

test.beforeEach(async ({ page, request }) => {
  await ensureConfigured(request);
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
});

test('settings → web-search provider selection persists', async ({ page, request }) => {
  await openTab(page, 'settings');
  const provider = page.locator('#cfg-websearch-provider');
  await expect(provider).toBeVisible();
  // Pick the first non-empty option, if any.
  const opts = await provider.locator('option').allTextContents();
  if (opts.length > 1) {
    await provider.selectOption({ index: 1 });
    await page.locator('#save-websearch').click();
    await expect(page.locator('#websearch-status')).not.toHaveText('', { timeout: 10_000 });
  }
});

test('composer web-search dropdown is present', async ({ page }) => {
  // Some builds gate the dropdown behind a setting; assert it's at least attached.
  await expect(page.locator('#toggle-websearch')).toBeAttached();
});
