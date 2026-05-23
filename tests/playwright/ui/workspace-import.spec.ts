/**
 * Workspace export → import round-trip via the API.
 *
 * UI import is gated by a file picker; the API path is exercised here for
 * deterministic outcomes. The Workspaces tab is also opened to confirm the
 * imported workspace shows.
 */
import { test, expect } from '@playwright/test';
import { gotoApp, dismissOnboardingIfPresent, openTab, ensureConfigured } from './helpers/ui-helpers';

test.beforeEach(async ({ page, request }) => {
  await ensureConfigured(request);
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
});

test('export a workspace as bundle, then import it back', async ({ page, request }) => {
  // Create source workspace.
  const create = await request.post('/api/workspaces', {
    data: { name: 'Roundtrip Source', description: 'export then import' },
  });
  const { id } = await create.json();

  // Export.
  const exp = await request.get(`/api/workspaces/${id}/export`);
  expect(exp.ok()).toBeTruthy();
  const blob = await exp.body();
  expect(blob.length).toBeGreaterThan(0);

  // Delete the original so the import truly recreates state.
  await request.delete(`/api/workspaces/${id}`);

  // Import the bytes back via multipart upload.
  const imp = await request.post('/api/workspaces/import', {
    multipart: {
      file: {
        name: 'roundtrip.bwui',
        mimeType: 'application/octet-stream',
        buffer: blob,
      },
    },
  });
  expect([200, 201].includes(imp.status())).toBeTruthy();

  // switchTab() doesn't refresh #workspace-list — reload so the page re-fetches.
  await page.reload();
  await dismissOnboardingIfPresent(page);
  await openTab(page, 'workspaces');
  await expect(page.locator('#workspace-list')).toContainText('Roundtrip Source');

  // Clean up.
  const after = await request.get('/api/workspaces');
  const ws = ((await after.json()).workspaces as any[]) ?? [];
  for (const w of ws.filter((w) => w.name === 'Roundtrip Source')) {
    await request.delete(`/api/workspaces/${w.id}`);
  }
});
