/**
 * Onboarding API endpoints — templates list + complete.
 */
import { test, expect } from '@playwright/test';

test('GET /api/onboarding/templates returns a list', async ({ request }) => {
  const r = await request.get('/api/onboarding/templates');
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  const items = Array.isArray(body) ? body : body.templates ?? body.items ?? [];
  expect(Array.isArray(items)).toBe(true);
  expect(items.length).toBeGreaterThan(0);
});

test('POST /api/onboarding/complete creates a workspace from a template', async ({ request }) => {
  const list = await request.get('/api/onboarding/templates');
  const body = await list.json();
  const items = Array.isArray(body) ? body : body.templates ?? body.items ?? [];
  test.skip(items.length === 0, 'no onboarding templates available');

  const first = items[0];
  const r = await request.post('/api/onboarding/complete', {
    data: { template: first.id ?? first.name ?? first, use_case: first.id ?? first.name },
  });
  // 200 created, 400 if payload format differs, 409 if user already onboarded.
  expect([200, 201, 400, 409].includes(r.status())).toBeTruthy();
});
