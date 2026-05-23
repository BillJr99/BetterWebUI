/**
 * Sanity checks: the app loads, every sidebar tab can be opened, and there
 * are no JS console errors on a fresh load.
 */
import { test, expect } from '@playwright/test';
import { gotoApp, dismissOnboardingIfPresent, openTab, ensureConfigured } from './helpers/ui-helpers';
import { expectServicesHealthy } from './helpers/outcome-helpers';

const TABS = ['chats', 'workspaces', 'files', 'memory', 'scheduled',
              'skills', 'prompts', 'tools', 'settings'];

test('app health endpoints respond', async ({ request }) => {
  const r = await request.get('/api/health');
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  expect(body.ok).toBe(true);
});

test('services health reports all three components', async ({ request }) => {
  await expectServicesHealthy(request);
});

test('index page loads without JS errors and serves static assets', async ({ page, request }) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (msg) => {
    if (msg.type() === 'error') errors.push(msg.text());
  });
  await ensureConfigured(request);
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
  await expect(page.locator('#sidebar')).toBeVisible();
  await expect(page.locator('#composer-input')).toBeVisible();

  // Tolerate well-known third-party noise (KaTeX font fetches, fonts.googleapis 404s on offline test boxes).
  const meaningful = errors.filter((e) =>
    !/katex|font|favicon|google|cdn/i.test(e),
  );
  expect(meaningful, `unexpected console errors: ${meaningful.join('\n')}`).toEqual([]);
});

for (const tab of TABS) {
  test(`sidebar tab "${tab}" opens`, async ({ page, request }) => {
    await ensureConfigured(request);
    await gotoApp(page);
    await dismissOnboardingIfPresent(page);
    await openTab(page, tab);
  });
}
