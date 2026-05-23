/**
 * Onboarding wizard — three-step UI flow (URL+key → use-case → done).
 *
 * Wipes config.json before each test via /api/test/reset so the overlay
 * actually appears. Skips if the reset endpoint is unavailable (production
 * build).
 */
import { test, expect } from '@playwright/test';
import { gotoApp, resetServerState } from './helpers/ui-helpers';

test.describe('onboarding overlay', () => {
  test.beforeEach(async ({ request }) => {
    // Best-effort wipe; if not in test mode, subsequent tests just skip.
    await resetServerState(request);
  });

  test('three-step wizard completes and unhides chat composer', async ({ page, request }) => {
    // Skip the test if reset isn't available — onboarding can't be exercised cleanly.
    const probe = await request.post('/api/test/reset').catch(() => null);
    if (!probe || probe.status() === 404) test.skip(true, 'BWUI_TEST_MODE not enabled on server');

    const owUrl = process.env.OPENWEBUI_BASE_URL ?? '';
    const owKey = process.env.OPENWEBUI_API_KEY  ?? '';
    test.skip(!owUrl || !owKey, 'OPENWEBUI_BASE_URL / OPENWEBUI_API_KEY not set');

    await gotoApp(page);
    const overlay = page.locator('#onboarding-overlay');
    await expect(overlay).toBeVisible();

    await page.locator('#ob-url').fill(owUrl);
    await page.locator('#ob-key').fill(owKey);
    await page.locator('#ob-connect-btn').click();

    // Step 2 — use case grid
    await expect(page.locator('#onboarding-step-2')).toBeVisible();
    const firstTile = page.locator('#use-case-grid > *').first();
    await firstTile.click();
    await page.locator('#ob-usecase-btn').click();

    // Step 3 — done
    await expect(page.locator('#onboarding-step-3')).toBeVisible();
    await page.locator('#ob-finish-btn').click();

    // Overlay closes; composer visible.
    await expect(overlay).toBeHidden();
    await expect(page.locator('#composer-input')).toBeVisible();

    // /api/config now reports the key is set.
    const cfg = await request.get('/api/config');
    expect(cfg.ok()).toBeTruthy();
    const body = await cfg.json();
    expect(body.api_key_set).toBe(true);
  });
});
