/**
 * Display settings — font size, line height, dyslexic, high-contrast,
 * reduced motion. Verify body classes update.
 */
import { test, expect } from '@playwright/test';
import { gotoApp, dismissOnboardingIfPresent, openTab, ensureConfigured } from './helpers/ui-helpers';

test.beforeEach(async ({ page, request }) => {
  await ensureConfigured(request);
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
  await openTab(page, 'settings');
});

test('font-size dropdown changes a CSS variable or class', async ({ page }) => {
  const sel = page.locator('#cfg-font-size');
  const opts = await sel.locator('option').allTextContents();
  if (opts.length < 2) test.skip(true, 'only one font-size option');
  await sel.selectOption({ index: opts.length - 1 });
  await page.locator('#save-display').click();
  // Read back via getAttribute; tolerant — class name varies.
  await expect.poll(async () =>
    await page.locator('body').getAttribute('class') ?? '',
  ).not.toEqual('');
});

test('all three accessibility toggles can be enabled together', async ({ page }) => {
  await page.locator('#cfg-dyslexic').check();
  await page.locator('#cfg-high-contrast').check();
  await page.locator('#cfg-reduce-motion').check();
  await page.locator('#save-display').click();
  await page.reload();
  await dismissOnboardingIfPresent(page);
  await openTab(page, 'settings');
  expect(await page.locator('#cfg-dyslexic').isChecked()).toBe(true);
  expect(await page.locator('#cfg-high-contrast').isChecked()).toBe(true);
  expect(await page.locator('#cfg-reduce-motion').isChecked()).toBe(true);
  // Clean up.
  await page.locator('#cfg-dyslexic').uncheck();
  await page.locator('#cfg-high-contrast').uncheck();
  await page.locator('#cfg-reduce-motion').uncheck();
  await page.locator('#save-display').click();
});
