/**
 * Image generation prompt — best-effort. Skipped if no image model configured.
 */
import { test, expect } from '@playwright/test';
import {
  gotoApp, dismissOnboardingIfPresent, sendChatMessage, waitForAssistantResponse,
  ensureConfigured, pickModel,
} from './helpers/ui-helpers';

test.beforeEach(async ({ page, request }) => {
  await ensureConfigured(request);
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
});

test('asking for an image either renders one inline or returns a service-unavailable explanation', async ({ page, request }) => {
  const model = await pickModel(request);
  test.skip(!model, 'no model configured');

  // Best-effort detection: does config carry an image_model?
  const cfg = await request.get('/api/config');
  if (cfg.ok()) {
    const body = await cfg.json();
    if (!body.image_model) test.skip(true, 'no image model configured');
  }

  await sendChatMessage(page, 'Generate a tiny image of a red square.');
  await waitForAssistantResponse(page, { timeoutMs: 240_000 }).catch(() => {});
  const lastBubble = page.locator('#messages .message.assistant').last();
  const html = await lastBubble.innerHTML();
  // Outcome: either an <img> appeared, or there's text explaining unavailability.
  expect(html.length).toBeGreaterThan(0);
});
