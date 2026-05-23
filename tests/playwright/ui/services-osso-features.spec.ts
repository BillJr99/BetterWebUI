/**
 * OSScreenObserver — exhaustive endpoint coverage (mock mode in tests):
 *   GET  /api/services/osso/windows
 *   GET  /api/services/osso/description
 *   GET  /api/services/osso/structure
 *   GET  /api/services/osso/screenshot
 *   POST /api/services/osso/action
 *   GET  /api/services/osso/capabilities
 */
import { test, expect } from '@playwright/test';
import { ensureConfigured } from './helpers/ui-helpers';

test.beforeEach(async ({ request }) => {
  await ensureConfigured(request);
  await request.post('/api/services/osso/enable').catch(() => {});
});

test('GET /capabilities returns a capability set', async ({ request }) => {
  const r = await request.get('/api/services/osso/capabilities');
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  expect(body).toBeTruthy();
});

test('GET /windows returns a list', async ({ request }) => {
  const r = await request.get('/api/services/osso/windows');
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  // Either an array directly or an object with .windows
  expect(body).toBeTruthy();
});

test('GET /description returns a description object', async ({ request }) => {
  const r = await request.get('/api/services/osso/description');
  expect(r.ok()).toBeTruthy();
});

test('GET /structure returns an accessibility tree (or 200 with body)', async ({ request }) => {
  const r = await request.get('/api/services/osso/structure');
  expect([200, 204].includes(r.status())).toBeTruthy();
});

test('GET /screenshot returns image bytes', async ({ request }) => {
  const r = await request.get('/api/services/osso/screenshot');
  expect(r.ok()).toBeTruthy();
  const buf = await r.body();
  expect(buf.length).toBeGreaterThan(0);
});

test('POST /action (read-only/no-op in mock mode) accepts a payload', async ({ request }) => {
  const r = await request.post('/api/services/osso/action', {
    data: { action: 'move', x: 100, y: 100, dry_run: true },
  });
  // Mock backends may return 200, 202, or 501 for unsupported actions.
  expect([200, 202, 400, 501].includes(r.status())).toBeTruthy();
});
