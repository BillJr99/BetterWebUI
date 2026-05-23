/**
 * Project tree + checkpoints + revert.
 * Endpoints:
 *   GET  /api/project/tree
 *   GET  /api/project/file
 *   GET  /api/project/checkpoints
 *   POST /api/project/revert
 */
import { test, expect } from '@playwright/test';
import { ensureConfigured } from './helpers/ui-helpers';

test.beforeEach(async ({ request }) => {
  await ensureConfigured(request);
});

test('/api/project/tree responds (200 or 404 if no workspace configured)', async ({ request }) => {
  const r = await request.get('/api/project/tree');
  // 404 is acceptable when no project root has been configured.
  expect([200, 404].includes(r.status())).toBeTruthy();
});

test('/api/project/checkpoints responds', async ({ request }) => {
  // filename is optional; omitting it returns an empty list (200).
  const r = await request.get('/api/project/checkpoints');
  expect([200, 404].includes(r.status())).toBeTruthy();
});

test('/api/project/file requires a path and returns 4xx without one', async ({ request }) => {
  const r = await request.get('/api/project/file');
  expect(r.status()).toBeGreaterThanOrEqual(400);
  expect(r.status()).toBeLessThan(500);
});
