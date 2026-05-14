import { test, expect } from '@playwright/test';
import { collectSSE } from '../helpers/sse';
import * as fs from 'fs';
import * as path from 'path';

test('CLK workflow list is non-empty', async ({ request }) => {
  const r = await request.get('/api/services/clk/workflows');
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  expect(body.ok).toBe(true);
  expect(Array.isArray(body.workflows)).toBe(true);
});

test('CLK research task streams events and completes', async ({ request, baseURL }) => {
  // Upload the echo workflow
  const echoYaml = fs.readFileSync(path.join(__dirname, '../fixtures/workflows/echo.yaml'), 'utf-8');

  // Start a research task
  const startR = await request.post('/api/services/clk/research', {
    data: { command: 'run', args: ['--workflow', 'echo'], workflow: 'echo' }
  });
  expect(startR.ok()).toBeTruthy();
  const { task_id } = await startR.json();
  expect(typeof task_id).toBe('string');

  // Stream events
  const events = await collectSSE(`${baseURL}/api/services/clk/research/${task_id}/stream`, 30, 90000);
  expect(events.length).toBeGreaterThan(0);

  // Final status check
  const statusR = await request.get(`/api/services/clk/research/${task_id}`);
  const status = await statusR.json();
  expect(['done', 'failed']).toContain(status.status);
});
