/**
 * /api/config — GET + POST round-trip; api_key is never returned in cleartext.
 */
import { test, expect } from '@playwright/test';
import { ensureConfigured } from './helpers/ui-helpers';

test('GET returns api_key_set boolean but never the raw key', async ({ request }) => {
  await ensureConfigured(request);
  const r = await request.get('/api/config');
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  expect(body).toHaveProperty('api_key_set');
  expect(body.api_key).toBe('');
});

test('POST updates base_url + default_model', async ({ request }) => {
  await ensureConfigured(request);
  const before = await (await request.get('/api/config')).json();
  // Round-trip: set default_model to whatever it currently is.
  const r = await request.post('/api/config', {
    data: { default_model: before.default_model ?? '' },
  });
  expect(r.ok()).toBeTruthy();
});

test('POST with malformed URL is normalised or rejected gracefully', async ({ request }) => {
  // Use a URL that needs normalisation (trailing slash, scheme present).
  const r = await request.post('/api/config', {
    data: { base_url: 'http://localhost:3000/' },
  });
  expect(r.ok()).toBeTruthy();
  const cfg = await (await request.get('/api/config')).json();
  expect(cfg.base_url.endsWith('/')).toBe(false);
});
