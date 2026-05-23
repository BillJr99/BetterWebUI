/**
 * System prompts — CRUD via the Prompts tab.
 */
import { test, expect } from '@playwright/test';
import { gotoApp, dismissOnboardingIfPresent, openTab, ensureConfigured } from './helpers/ui-helpers';

test.beforeEach(async ({ page, request }) => {
  await ensureConfigured(request);
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
});

test('create a system prompt via API; UI list shows it', async ({ page, request }) => {
  const r = await request.post('/api/system-prompts', {
    data: { name: 'PW Prompt', content: 'You are helpful.' },
  });
  expect(r.ok()).toBeTruthy();
  const { id } = await r.json();

  // Reload so the JS fetches the updated prompt list before we switch tabs.
  await page.reload();
  await dismissOnboardingIfPresent(page);
  await openTab(page, 'prompts');
  await expect(page.locator('#prompt-list')).toContainText('PW Prompt');

  await request.delete(`/api/system-prompts/${id}`);
});

test('Prompts tab loads with no console errors', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await openTab(page, 'prompts');
  expect(errors).toEqual([]);
});
