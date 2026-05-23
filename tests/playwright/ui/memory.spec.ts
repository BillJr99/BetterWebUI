/**
 * Memory tab — UI surface check. Memory creation is gated through chat
 * interaction in BetterWebUI; here we verify the tab and toggle behave.
 */
import { test, expect } from '@playwright/test';
import { gotoApp, dismissOnboardingIfPresent, openTab, ensureConfigured } from './helpers/ui-helpers';

test.beforeEach(async ({ page, request }) => {
  await ensureConfigured(request);
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
});

test('Memory tab opens and the pause toggle works', async ({ page }) => {
  await openTab(page, 'memory');
  await expect(page.locator('#new-memory-btn')).toBeVisible();
  const pause = page.locator('#memory-pause-toggle');
  await pause.check();
  expect(await pause.isChecked()).toBe(true);
  await pause.uncheck();
  expect(await pause.isChecked()).toBe(false);
});

test('Memory list renders without console errors', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await openTab(page, 'memory');
  await expect(page.locator('#memory-list')).toBeVisible();
  expect(errors).toEqual([]);
});
