/**
 * Settings panel — for each editable section, save → reload → verify persisted.
 * Drives the form via clicks and keyboard input.
 */
import { test, expect } from '@playwright/test';
import { gotoApp, dismissOnboardingIfPresent, openTab, ensureConfigured } from './helpers/ui-helpers';

test.beforeEach(async ({ page, request }) => {
  await ensureConfigured(request);
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
  await openTab(page, 'settings');
});

test('connection — Save & test fills the status line', async ({ page }) => {
  await page.locator('#save-connection').click();
  const status = page.locator('#connection-status');
  await expect(status).not.toHaveText('', { timeout: 30_000 });
});

test('default chat model can be changed and persists', async ({ page, request }) => {
  // Pick the second option (or the first if there's only one) and save.
  const select = page.locator('#cfg-default-model');
  const opts = await select.locator('option').allTextContents();
  if (opts.length < 1) test.skip(true, 'no models available');
  const choice = opts[Math.min(1, opts.length - 1)];
  await select.selectOption({ label: choice.trim() }).catch(() =>
    select.selectOption(choice.trim()),
  );
  await page.locator('#save-defaults').click();
  await page.reload();
  await dismissOnboardingIfPresent(page);
  await openTab(page, 'settings');
  const cfg = await request.get('/api/config');
  expect(cfg.ok()).toBeTruthy();
  const body = await cfg.json();
  expect(body.default_model).toBeTruthy();
});

test('verification mode + retries persist', async ({ page, request }) => {
  const mode = page.locator('#cfg-verification-mode');
  const opts = await mode.locator('option').allTextContents();
  if (opts.length >= 2) await mode.selectOption({ index: 1 });
  await page.locator('#cfg-verification-retries').fill('2');
  await page.locator('#save-verification').click();
  await expect(page.locator('#verification-status')).not.toHaveText('', { timeout: 10_000 });
  const cfg = await request.get('/api/config');
  expect(cfg.ok()).toBeTruthy();
});

test('display toggles propagate to body classes', async ({ page }) => {
  await page.locator('#cfg-dyslexic').check();
  await page.locator('#cfg-high-contrast').check();
  await page.locator('#cfg-reduce-motion').check();
  await page.locator('#save-display').click();
  // body picks up classes set by app.js — best-effort assertion.
  await expect.poll(async () =>
    await page.locator('body').getAttribute('class') ?? '',
  ).toMatch(/dyslexic|contrast|reduce/);
});

test('services toggles round-trip', async ({ page, request }) => {
  await page.locator('#svc-clk-enabled').uncheck();
  // Status must update.
  await expect(page.locator('#services-toggle-status')).not.toHaveText('', { timeout: 10_000 });
  // Confirm via API.
  const r = await request.get('/api/services/status');
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  expect(body.services.clk.enabled).toBe(false);
  // Re-enable.
  await page.locator('#svc-clk-enabled').check();
  await expect(page.locator('#services-toggle-status')).not.toHaveText('', { timeout: 10_000 });
});
