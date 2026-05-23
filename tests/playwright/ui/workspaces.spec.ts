/**
 * Workspaces — create, switch, export, delete via the sidebar Workspaces tab.
 */
import { test, expect } from '@playwright/test';
import { gotoApp, dismissOnboardingIfPresent, openTab, ensureConfigured } from './helpers/ui-helpers';

test.beforeEach(async ({ page, request }) => {
  await ensureConfigured(request);
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
  await openTab(page, 'workspaces');
});

test('create a workspace and see it in the list', async ({ page, request }) => {
  const before = await request.get('/api/workspaces');
  const beforeList = (await before.json()).workspaces ?? [];

  await page.locator('#new-workspace-btn').click();
  // The workspace dialog uses #dlg-name (no placeholder / no aria-label) and a
  // <button>Save</button>; selectors target IDs/text exactly as rendered.
  await page.locator('#dlg-name').fill('Playwright Test Workspace');
  await page.locator('.dialog-actions button.primary').click();

  await expect.poll(async () => {
    const r = await request.get('/api/workspaces');
    const ws = (await r.json()).workspaces ?? [];
    return ws.length;
  }, { timeout: 15_000 }).toBeGreaterThan(beforeList.length);

  // Clean up.
  const after = await request.get('/api/workspaces');
  const newW = ((await after.json()).workspaces as any[]).find(
    (w) => w.name === 'Playwright Test Workspace',
  );
  if (newW) await request.delete(`/api/workspaces/${newW.id}`);
});

test('workspace-select dropdown reflects current workspaces', async ({ page, request }) => {
  // Seed a workspace via API so the dropdown has at least one entry.
  const create = await request.post('/api/workspaces', {
    data: { name: 'WS Dropdown Test', description: 'dropdown' },
  });
  expect(create.ok()).toBeTruthy();
  const { id } = await create.json();

  await page.reload();
  await dismissOnboardingIfPresent(page);
  const select = page.locator('#workspace-select');
  await expect(select).toBeVisible();
  const opts = await select.locator('option').allTextContents();
  expect(opts.some((o) => o.includes('WS Dropdown Test'))).toBe(true);

  await request.delete(`/api/workspaces/${id}`);
});

test('export and delete a workspace round-trip via API', async ({ request }) => {
  const create = await request.post('/api/workspaces', {
    data: { name: 'Export Test WS' },
  });
  const { id } = await create.json();

  const exp = await request.get(`/api/workspaces/${id}/export`);
  expect(exp.ok()).toBeTruthy();
  const buf = await exp.body();
  expect(buf.length).toBeGreaterThan(0);

  const del = await request.delete(`/api/workspaces/${id}`);
  expect(del.ok()).toBeTruthy();
});
