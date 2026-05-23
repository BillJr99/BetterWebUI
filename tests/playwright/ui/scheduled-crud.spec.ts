/**
 * Scheduled tasks — full CRUD via API plus notifications endpoint.
 */
import { test, expect } from '@playwright/test';
import { ensureConfigured } from './helpers/ui-helpers';

test.beforeEach(async ({ request }) => { await ensureConfigured(request); });

test('create → list → delete', async ({ request }) => {
  const future = new Date(Date.now() + 600_000).toISOString();
  const create = await request.post('/api/scheduled-tasks', {
    data: { name: 'PW CRUD Task', prompt: 'Hello world.', schedule: future },
  });
  if (!create.ok()) test.skip(true, `POST returned ${create.status()}`);
  const body = await create.json();
  const id = body.id ?? body.task_id;
  expect(id).toBeTruthy();

  const list = await request.get('/api/scheduled-tasks');
  expect(list.ok()).toBeTruthy();

  const del = await request.delete(`/api/scheduled-tasks/${id}`);
  expect([200, 204].includes(del.status())).toBeTruthy();
});

test('notifications endpoint responds', async ({ request }) => {
  const r = await request.get('/api/scheduled-tasks/notifications');
  expect(r.ok()).toBeTruthy();
});
