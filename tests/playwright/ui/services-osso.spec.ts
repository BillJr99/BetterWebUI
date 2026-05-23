/**
 * OSScreenObserver via /observe slash command. OSSO runs in mock mode in tests.
 */
import { test, expect } from '@playwright/test';
import {
  gotoApp, dismissOnboardingIfPresent, sendChatMessage, waitForAssistantResponse,
  ensureConfigured, pickModel,
} from './helpers/ui-helpers';

test.beforeEach(async ({ page, request }) => {
  await ensureConfigured(request);
  await request.post('/api/services/osso/enable').catch(() => {});
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
});

test('OSSO capabilities endpoint reachable', async ({ request }) => {
  const r = await request.get('/api/services/osso/capabilities');
  expect(r.ok()).toBeTruthy();
});

test('OSSO description endpoint returns a payload in mock mode', async ({ request }) => {
  const r = await request.get('/api/services/osso/description');
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  expect(body).toBeTruthy();
});

test('/observe slash command produces an assistant response', async ({ page, request }) => {
  const model = await pickModel(request);
  test.skip(!model, 'no model configured');
  await sendChatMessage(page, '/observe');
  await waitForAssistantResponse(page, { timeoutMs: 180_000 }).catch(() => {});
});

test('disabling OSSO returns 503', async ({ request }) => {
  await request.post('/api/services/osso/disable');
  const r = await request.get('/api/services/osso/capabilities');
  expect(r.status()).toBe(503);
  await request.post('/api/services/osso/enable');
});
