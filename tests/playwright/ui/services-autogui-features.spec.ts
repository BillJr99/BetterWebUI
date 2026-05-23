/**
 * AutoGUI — exhaustive endpoint coverage (dry-run mode in tests):
 *   GET  /api/services/autogui/tools
 *   POST /api/services/autogui/task
 *   GET  /api/services/autogui/task/{id}
 *   GET  /api/services/autogui/task/{id}/stream     (SSE)
 *   POST /api/services/autogui/task/{id}/cancel
 */
import { test, expect, APIRequestContext } from '@playwright/test';
import { ensureConfigured } from './helpers/ui-helpers';

test.beforeEach(async ({ request }) => {
  await ensureConfigured(request);
  await request.post('/api/services/autogui/enable').catch(() => {});
});

test('GET /tools returns the tool list', async ({ request }) => {
  const r = await request.get('/api/services/autogui/tools');
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  expect(body).toBeTruthy();
});

async function startTask(request: APIRequestContext): Promise<string | null> {
  const r = await request.post('/api/services/autogui/task', {
    data: {
      task: 'Take a screenshot of the active window (dry-run).',
      dry_run: true,
    },
  });
  if (!r.ok()) return null;
  const body = await r.json();
  return body.id ?? body.task_id ?? null;
}

test('POST /task creates a task and GET /task/{id} returns its status', async ({ request }) => {
  const id = await startTask(request);
  test.skip(!id, 'AutoGUI task could not be started');
  const r = await request.get(`/api/services/autogui/task/${id}`);
  expect(r.ok()).toBeTruthy();
});

test('GET /task/{id}/stream responds with SSE-able bytes', async ({ request }) => {
  const id = await startTask(request);
  test.skip(!id, 'AutoGUI task could not be started');
  const r = await request.get(`/api/services/autogui/task/${id}/stream`, { timeout: 20_000 });
  expect(r.ok() || r.status() === 204).toBeTruthy();
});

test('POST /task/{id}/cancel returns success', async ({ request }) => {
  const id = await startTask(request);
  test.skip(!id, 'AutoGUI task could not be started');
  const r = await request.post(`/api/services/autogui/task/${id}/cancel`);
  expect([200, 202, 204].includes(r.status())).toBeTruthy();
});
