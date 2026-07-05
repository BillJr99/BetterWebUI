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

  // The assistant bubble appears on the `assistant_text` SSE event, but the
  // server only persists the conversation at `save_conversation`, immediately
  // before the terminating `done` event. Reloading as soon as text shows up
  // therefore races the save and can drop the just-created conversation.
  // Wait for the composer to return to idle: send() re-enables and relabels
  // the button "Send" in its finally block, which runs only after `done` (and
  // its loadConversations refresh) has been processed — a reliable "turn fully
  // persisted" barrier.
  await expect(page.locator('#send-btn')).toHaveText('Send', { timeout: 120_000 });
  await expect(page.locator('#send-btn')).toBeEnabled();

  await page.reload();
  await dismissOnboardingIfPresent(page);
  // Wait for the sidebar to populate, then explicitly select the most recent
  // conversation (the server sorts newest-first).
  await page.locator('#conversation-list li').first().waitFor({ state: 'visible', timeout: 30_000 });
  console.log('[reload] conversation list populated, clicking first item');
  await page.locator('#conversation-list li').first().click();
  // Wait for the persisted assistant message to render before reading it, so a
  // slow load surfaces as a clear "element never appeared" rather than an
  // opaque innerText timeout.
  const assistant = page.locator('#messages .message.assistant .content').last();
  await assistant.waitFor({ state: 'visible', timeout: 60_000 });
  const after = await assistant.innerText();
  expect(after.trim().length).toBeGreaterThan(0);
});
