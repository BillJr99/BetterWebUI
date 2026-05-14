import { test, expect } from '@playwright/test';

test('all services report healthy', async ({ request }) => {
  const r = await request.get('/api/services/health');
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  expect(body.ok).toBe(true);
  expect(body.services.clk.ok).toBe(true);
  expect(body.services.autogui.ok).toBe(true);
  expect(body.services.osso.ok).toBe(true);
});
