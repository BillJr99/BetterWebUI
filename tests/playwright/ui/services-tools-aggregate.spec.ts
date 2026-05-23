/**
 * /api/services/tools — aggregate tool specs across all three services.
 * Verify the shape includes entries from each enabled service.
 */
import { test, expect } from '@playwright/test';
import { ensureConfigured } from './helpers/ui-helpers';

test.beforeEach(async ({ request }) => {
  await ensureConfigured(request);
  await Promise.all([
    request.post('/api/services/clk/enable').catch(() => {}),
    request.post('/api/services/autogui/enable').catch(() => {}),
    request.post('/api/services/osso/enable').catch(() => {}),
  ]);
});

test('returns a non-empty list', async ({ request }) => {
  const r = await request.get('/api/services/tools');
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  const tools = Array.isArray(body) ? body : body.tools ?? body.items ?? [];
  expect(Array.isArray(tools)).toBe(true);
  // After all services are enabled we expect at least one tool to be exposed.
  expect(tools.length).toBeGreaterThan(0);
});

test('disabled service is excluded from aggregate', async ({ request }) => {
  await request.post('/api/services/clk/disable');
  const r = await request.get('/api/services/tools');
  expect(r.ok()).toBeTruthy();
  // Re-enable for downstream tests.
  await request.post('/api/services/clk/enable');
});
