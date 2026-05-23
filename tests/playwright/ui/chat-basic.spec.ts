/**
 * Basic chat flow — send a message, see a response, conversation persists.
 * Asserts outcomes only (response is non-empty), never exact text.
 */
import { test, expect } from '@playwright/test';
import {
  gotoApp, dismissOnboardingIfPresent, sendChatMessage, waitForAssistantResponse,
  getLastAssistantText, ensureConfigured, pickModel,
} from './helpers/ui-helpers';
import { expectNonEmptyText } from './helpers/outcome-helpers';

test.beforeEach(async ({ page, request }) => {
  await ensureConfigured(request);
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
});

test('send a message and receive a non-empty response', async ({ page, request }) => {
  const model = await pickModel(request);
  test.skip(!model, 'no model configured');
  await sendChatMessage(page, 'Reply with one short word only.');
  await waitForAssistantResponse(page);
  const text = await getLastAssistantText(page);
  expectNonEmptyText(text);
});

test('new-chat button creates a separate conversation', async ({ page, request }) => {
  const model = await pickModel(request);
  test.skip(!model, 'no model configured');

  await sendChatMessage(page, 'First chat hello.');
  await waitForAssistantResponse(page);

  const before = await page.locator('#conversation-list li').count();
  await page.locator('#new-chat-btn').click();
  await sendChatMessage(page, 'Second chat hello.');
  await waitForAssistantResponse(page);

  const after = await page.locator('#conversation-list li').count();
  expect(after).toBeGreaterThanOrEqual(before + 1);
});

test('conversation persists across page reload', async ({ page, request }) => {
  const model = await pickModel(request);
  test.skip(!model, 'no model configured');

  await sendChatMessage(page, 'Say anything.');
  await waitForAssistantResponse(page);
  const before = await getLastAssistantText(page);
  expectNonEmptyText(before);

  await page.reload();
  await dismissOnboardingIfPresent(page);
  // The most recent conversation should be selected and load its messages.
  const after = await page.locator('#messages [data-role="assistant"]').last().innerText({ timeout: 30_000 });
  expect(after.trim().length).toBeGreaterThan(0);
});
