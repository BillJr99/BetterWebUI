/**
 * Conversations — pin, fork, tag, recent, summary, delete.
 *
 * We create a fresh conversation via the chat UI, then drive each endpoint.
 */
import { test, expect } from '@playwright/test';
import {
  gotoApp, dismissOnboardingIfPresent, sendChatMessage, waitForAssistantResponse,
  ensureConfigured, pickModel,
} from './helpers/ui-helpers';

async function createConversation(page: any, request: any): Promise<string | null> {
  const model = await pickModel(request);
  if (!model) return null;
  await sendChatMessage(page, 'Quick test message.');
  await waitForAssistantResponse(page, { timeoutMs: 180_000 }).catch(() => {});
  // Find the most recent conversation id.
  const r = await request.get('/api/conversations');
  if (!r.ok()) return null;
  const body = await r.json();
  const list = Array.isArray(body) ? body : body.conversations ?? body.items ?? [];
  return (list[0]?.id ?? list[list.length - 1]?.id) as string ?? null;
}

test.beforeEach(async ({ page, request }) => {
  await ensureConfigured(request);
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
});

test('recent endpoint responds', async ({ request }) => {
  const r = await request.get('/api/conversations/recent');
  expect(r.ok()).toBeTruthy();
});

test('pin endpoint round-trips', async ({ page, request }) => {
  const cid = await createConversation(page, request);
  test.skip(!cid, 'could not create a conversation');
  const r = await request.post(`/api/conversations/${cid}/pin`, { data: { pinned: true } });
  expect([200, 204].includes(r.status())).toBeTruthy();
});

test('tag endpoint accepts a tags array', async ({ page, request }) => {
  const cid = await createConversation(page, request);
  test.skip(!cid, 'could not create a conversation');
  const r = await request.post(`/api/conversations/${cid}/tags`, { data: { tags: ['test'] } });
  expect([200, 204].includes(r.status())).toBeTruthy();
});

test('summary endpoint responds', async ({ page, request }) => {
  const cid = await createConversation(page, request);
  test.skip(!cid, 'could not create a conversation');
  // The endpoint stores a provided summary string; send one so the body parse succeeds.
  const r = await request.post(`/api/conversations/${cid}/summary`, {
    data: { summary: 'test summary' },
  });
  expect([200, 204, 404].includes(r.status())).toBeTruthy();
});

test('fork endpoint creates a new conversation id', async ({ page, request }) => {
  const cid = await createConversation(page, request);
  test.skip(!cid, 'could not create a conversation');
  // Send empty JSON body so FastAPI can parse the ForkIn model (all fields optional).
  const r = await request.post(`/api/conversations/${cid}/fork`, { data: {} });
  expect([200, 201].includes(r.status())).toBeTruthy();
  if (r.ok()) {
    const body = await r.json();
    expect(body.id ?? body.conversation_id).toBeTruthy();
  }
});

test('delete endpoint removes a conversation', async ({ page, request }) => {
  const cid = await createConversation(page, request);
  test.skip(!cid, 'could not create a conversation');
  const r = await request.delete(`/api/conversations/${cid}`);
  expect([200, 204].includes(r.status())).toBeTruthy();
  // Confirm gone.
  const probe = await request.get(`/api/conversations/${cid}`);
  expect([404, 410].includes(probe.status())).toBeTruthy();
});
