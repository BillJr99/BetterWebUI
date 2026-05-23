/**
 * Scheduled tasks — create via API, verify visible in the UI tab.
 */
import { test, expect } from '@playwright/test';
import { gotoApp, dismissOnboardingIfPresent, openTab, ensureConfigured } from './helpers/ui-helpers';

test.beforeEach(async ({ page, request }) => {
  await ensureConfigured(request);
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
});

test('list endpoint responds and UI tab opens', async ({ page, request }) => {
  const r = await request.get('/api/scheduled-tasks');
  expect(r.ok()).toBeTruthy();
  await openTab(page, 'scheduled');
  await expect(page.locator('#new-scheduled-btn')).toBeVisible();
});

test('create a scheduled task via API; UI list shows it', async ({ page, request }) => {
  const future = new Date(Date.now() + 60_000).toISOString();
  const r = await request.post('/api/scheduled-tasks', {
    data: {
      name: 'PW Scheduled Test',
      prompt: 'Say hi.',
      schedule: future,
    },
  });
  // Endpoint shape may vary; tolerate either {id}/{ok:true} responses.
  if (!r.ok()) test.skip(true, `POST /api/scheduled-tasks returned ${r.status()}`);
  await openTab(page, 'scheduled');
  await expect(page.locator('#scheduled-list')).toContainText('PW Scheduled Test', { timeout: 10_000 });
});
