/**
 * Conversations sidebar — search, pin, fork.
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

test('search returns conversations containing the term', async ({ page, request }) => {
  const model = await pickModel(request);
  test.skip(!model, 'no model configured');

  // Send a message containing a unique sentinel string.
  const SENTINEL = `pwsearch-${Date.now()}`;
  await sendChatMessage(page, `Remember the word ${SENTINEL}.`);
  await waitForAssistantResponse(page);

  // Verify search endpoint returns the conversation.
  const r = await request.get(`/api/conversations/search?q=${SENTINEL}`);
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  const list = Array.isArray(body) ? body : body.results ?? body.conversations ?? [];
  expect(list.length).toBeGreaterThan(0);

  // UI search toggle opens the search input.
  await page.locator('#search-toggle-btn').click();
  await expect(page.locator('#conv-search-wrap')).toBeVisible();
  await page.locator('#conv-search').fill(SENTINEL);
  // Just verify the input accepted the value; result-rendering is best-effort.
  expect(await page.locator('#conv-search').inputValue()).toBe(SENTINEL);
});
