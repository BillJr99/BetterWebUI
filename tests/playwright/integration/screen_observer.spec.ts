import { test, expect } from '@playwright/test';

test('OSScreenObserver returns window list', async ({ request }) => {
  const r = await request.get('/api/services/osso/windows');
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  // Mock mode returns synthetic windows
  expect(typeof body.count).toBe('number');
  expect(Array.isArray(body.windows)).toBe(true);
});

test('OSScreenObserver returns description', async ({ request }) => {
  const r = await request.get('/api/services/osso/description?mode=accessibility');
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  expect(body.mode).toBe('accessibility');
  expect(typeof body.description).toBe('string');
  expect(body.description.length).toBeGreaterThan(0);
});

test('OSScreenObserver capabilities endpoint works', async ({ request }) => {
  const r = await request.get('/api/services/osso/capabilities');
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  expect(body.ok).toBe(true);
});
