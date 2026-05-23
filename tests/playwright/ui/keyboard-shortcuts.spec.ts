/**
 * Keyboard shortcuts — '?' opens the shortcut sheet; 'P' toggles plan pane;
 * 'F' toggles files pane.
 */
import { test, expect } from '@playwright/test';
import { gotoApp, dismissOnboardingIfPresent, ensureConfigured } from './helpers/ui-helpers';

test.beforeEach(async ({ page, request }) => {
  await ensureConfigured(request);
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
});

test("'?' opens the keyboard-shortcut sheet", async ({ page }) => {
  await page.keyboard.press('?');
  await expect(page.locator('#shortcut-sheet')).toBeVisible({ timeout: 5_000 });
  // Close via Escape.
  await page.keyboard.press('Escape');
  await expect(page.locator('#shortcut-sheet')).toBeHidden({ timeout: 5_000 });
});

test("'F' toggles the files pane", async ({ page }) => {
  await page.keyboard.press('f');
  await expect(page.locator('#files-pane')).toBeVisible({ timeout: 5_000 });
  await page.keyboard.press('f');
  await expect(page.locator('#files-pane')).toBeHidden({ timeout: 5_000 });
});

test("'P' toggles the plan pane", async ({ page }) => {
  await page.keyboard.press('p');
  await expect(page.locator('#plan-pane')).toBeVisible({ timeout: 5_000 });
  await page.keyboard.press('p');
  await expect(page.locator('#plan-pane')).toBeHidden({ timeout: 5_000 });
});
