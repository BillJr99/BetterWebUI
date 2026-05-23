/**
 * Workspace switching via the chat header dropdown.
 */
import { test, expect } from '@playwright/test';
import { gotoApp, dismissOnboardingIfPresent, ensureConfigured } from './helpers/ui-helpers';

test('switching workspaces updates the active workspace label', async ({ page, request }) => {
  await ensureConfigured(request);

  // Create two workspaces.
  const a = await request.post('/api/workspaces', { data: { name: 'PW Switch A' } });
  const b = await request.post('/api/workspaces', { data: { name: 'PW Switch B' } });
  const aId = (await a.json()).id;
  const bId = (await b.json()).id;

  await gotoApp(page);
  await dismissOnboardingIfPresent(page);

  const select = page.locator('#workspace-select');
  await select.selectOption({ label: 'PW Switch A' }).catch(() =>
    select.selectOption('PW Switch A'),
  );
  await expect(page.locator('#active-workspace-label')).toContainText('PW Switch A', { timeout: 5_000 });

  await select.selectOption({ label: 'PW Switch B' }).catch(() =>
    select.selectOption('PW Switch B'),
  );
  await expect(page.locator('#active-workspace-label')).toContainText('PW Switch B', { timeout: 5_000 });

  await request.delete(`/api/workspaces/${aId}`);
  await request.delete(`/api/workspaces/${bId}`);
});
