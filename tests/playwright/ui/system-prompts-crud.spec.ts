/**
 * System prompts — full CRUD via API + UI list reflection.
 */
import { test, expect } from '@playwright/test';
import { gotoApp, dismissOnboardingIfPresent, openTab, ensureConfigured } from './helpers/ui-helpers';

test.beforeEach(async ({ page, request }) => {
  await ensureConfigured(request);
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
});

test('create → list → delete', async ({ page, request }) => {
  const create = await request.post('/api/system-prompts', {
    data: { name: 'PW SP CRUD', content: 'You are concise.' },
  });
  expect(create.ok()).toBeTruthy();
  const { id } = await create.json();

  const list = await request.get('/api/system-prompts');
  expect(list.ok()).toBeTruthy();
  const items = ((await list.json()).prompts ?? []) as any[];
  expect(items.some((p) => p.id === id)).toBe(true);

  await openTab(page, 'prompts');
  await expect(page.locator('#prompt-list')).toContainText('PW SP CRUD');

  const del = await request.delete(`/api/system-prompts/${id}`);
  expect([200, 204].includes(del.status())).toBeTruthy();
});
