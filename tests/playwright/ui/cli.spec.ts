/**
 * CLI shortcuts — register a custom CLI tool; UI list shows it.
 */
import { test, expect } from '@playwright/test';
import { gotoApp, dismissOnboardingIfPresent, openTab, ensureConfigured } from './helpers/ui-helpers';

const ID = 'pw-cli-echo';

test.beforeEach(async ({ page, request }) => {
  await ensureConfigured(request);
  await request.delete(`/api/cli/tools/${ID}`).catch(() => {});
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
});

test('register a CLI tool via API; UI list shows it', async ({ page, request }) => {
  const r = await request.post('/api/cli/tools', {
    data: { id: ID, name: 'PW Echo', template: 'echo {args}', description: 'Echo for PW UI test' },
  });
  expect(r.ok()).toBeTruthy();
  await openTab(page, 'tools');
  await expect(page.locator('#cli-tool-list')).toContainText('PW Echo');
});

test('registry returns curated CLI shortcuts', async ({ request }) => {
  const r = await request.get('/api/cli/registry');
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  const items = Array.isArray(body) ? body : body.tools ?? body.items ?? [];
  expect(items.length).toBeGreaterThan(0);
});

test.afterEach(async ({ request }) => {
  await request.delete(`/api/cli/tools/${ID}`).catch(() => {});
});
