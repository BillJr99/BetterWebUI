/**
 * Branding + About — endpoint responds and About text renders.
 */
import { test, expect } from '@playwright/test';
import { gotoApp, dismissOnboardingIfPresent, openTab, ensureConfigured } from './helpers/ui-helpers';

test.beforeEach(async ({ page, request }) => {
  await ensureConfigured(request);
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
});

test('/api/branding returns a payload', async ({ request }) => {
  const r = await request.get('/api/branding');
  expect(r.ok()).toBeTruthy();
});

test('About section in Settings displays loaded info', async ({ page }) => {
  await openTab(page, 'settings');
  const about = page.locator('#about-info');
  await expect(about).toBeVisible();
  // After load, text should no longer be the literal placeholder.
  await expect.poll(async () => await about.innerText())
    .not.toBe('Loading…');
});
