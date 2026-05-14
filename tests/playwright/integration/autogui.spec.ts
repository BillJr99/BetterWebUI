import { test, expect } from '@playwright/test';
import { collectSSE } from '../helpers/sse';

test('AutoGUI dry-run task streams ReAct events', async ({ request, baseURL }) => {
  const startR = await request.post('/api/services/autogui/task', {
    data: { task: 'open settings and check the version number', dry_run: true }
  });
  expect(startR.ok()).toBeTruthy();
  const { task_id } = await startR.json();
  expect(typeof task_id).toBe('string');

  const events = await collectSSE(`${baseURL}/api/services/autogui/task/${task_id}/stream`, 20, 30000);
  expect(events.length).toBeGreaterThan(0);
  const kinds = events.map((e: any) => e.kind).filter(Boolean);
  expect(kinds).toContain('plan');
  expect(kinds).toContain('done');
});

test('AutoGUI task status is retrievable', async ({ request }) => {
  const startR = await request.post('/api/services/autogui/task', {
    data: { task: 'test task', dry_run: true }
  });
  const { task_id } = await startR.json();

  // Wait briefly then check status
  await new Promise(r => setTimeout(r, 3000));
  const statusR = await request.get(`/api/services/autogui/task/${task_id}`);
  expect(statusR.ok()).toBeTruthy();
  const status = await statusR.json();
  expect(status.ok).toBe(true);
  expect(['pending', 'running', 'done', 'failed']).toContain(status.status);
});
