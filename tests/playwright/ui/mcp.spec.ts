/**
 * MCP servers — Tools tab. Register a custom server via the API, verify it
 * shows in the UI, then delete. We use the API rather than driving the
 * "+ Add from registry" modal to keep the test resilient across UI changes.
 */
import { test, expect } from '@playwright/test';
import { gotoApp, dismissOnboardingIfPresent, openTab, ensureConfigured } from './helpers/ui-helpers';

const NAME = 'pw-mcp-test';

test.beforeEach(async ({ page, request }) => {
  await ensureConfigured(request);
  await request.delete(`/api/mcp/servers/${NAME}`).catch(() => {});
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
});

test('register a custom MCP server; UI list shows it', async ({ page, request }) => {
  const r = await request.post('/api/mcp/servers', {
    data: {
      name: NAME,
      command: 'true',         // command that exits 0; we don't need it to be functional
      args: [],
      env: {},
      description: 'PW UI test',
    },
  });
  expect(r.ok()).toBeTruthy();
  // Reload so the JS fetches the updated server list before we switch tabs.
  await page.reload();
  await dismissOnboardingIfPresent(page);
  await openTab(page, 'tools');
  await expect(page.locator('#mcp-server-list')).toContainText(NAME);
});

test('registry endpoint returns a non-empty curated list', async ({ request }) => {
  const r = await request.get('/api/mcp/registry');
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  // Could be an array directly or wrapped under "servers", "items", or "registry".
  const items = Array.isArray(body) ? body : body.servers ?? body.items ?? body.registry ?? [];
  expect(items.length).toBeGreaterThan(0);
});

test.afterEach(async ({ request }) => {
  await request.delete(`/api/mcp/servers/${NAME}`).catch(() => {});
});
