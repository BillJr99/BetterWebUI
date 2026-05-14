/**
 * End-to-end chat tests.
 *
 * These tests send real messages through BetterWebUI → OpenWebUI → Ollama
 * (tinyllama by default). Assertions are model-agnostic — we verify that:
 *   - a response arrives (any text)
 *   - the SSE stream closes cleanly (_done event)
 *   - the conversation is persisted
 *
 * We do NOT assert on specific response text because tiny models are
 * non-deterministic. Tool-call format tests live in unit/service tests
 * where the model is mocked.
 */

import { test, expect } from '@playwright/test';
import { collectSSE, collectSSEPost } from '../helpers/sse';

const MODEL = process.env.OLLAMA_MODEL ?? 'tinyllama:1.1b';

// ── Health & configuration ────────────────────────────────────────────────────

test('BetterWebUI health endpoint is ok', async ({ request }) => {
  const r = await request.get('/api/health');
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  expect(body.ok).toBe(true);
});

test('model list includes the Ollama model', async ({ request }) => {
  const r = await request.get('/api/models');
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  expect(Array.isArray(body.models)).toBe(true);
  expect(body.models.length).toBeGreaterThan(0);
  const ids: string[] = body.models.map((m: { id: string }) => m.id);
  // Ollama models surface as "<name>" or "ollama/<name>" depending on
  // OpenWebUI version — check that at least one id contains the model name.
  const modelName = MODEL.split(':')[0];
  expect(ids.some(id => id.toLowerCase().includes(modelName))).toBe(true);
});

// ── Chat stream ───────────────────────────────────────────────────────────────

test('simple chat returns a non-empty streaming response', async ({ baseURL }) => {
  // POST /api/chat returns an SSE stream directly.
  const chatBody = {
    model: MODEL,
    messages: [{ role: 'user', content: 'Reply with one word only: hello.' }],
    mode: 'trusted',  // skip approval gates in automated testing
  };
  const events = await collectSSEPost(
    `${baseURL}/api/chat`,
    chatBody,
    200,     // max events
    120_000, // 2 min timeout — model cold start can be slow
  );

  expect(events.length).toBeGreaterThan(0);

  // Expect at least one text delta.
  const textEvents = events.filter(e => typeof e.delta === 'string' && (e.delta as string).length > 0);
  expect(textEvents.length).toBeGreaterThan(0);

  // Stream must close cleanly.
  const doneEvent = events.find(e => e._done === true);
  expect(doneEvent).toBeDefined();
});

test('conversation is saved and retrievable', async ({ request, baseURL }) => {
  const chatBody = {
    model: MODEL,
    messages: [{ role: 'user', content: 'What is 1+1?' }],
    mode: 'trusted',
  };

  const events = await collectSSEPost(`${baseURL}/api/chat`, chatBody, 200, 120_000);
  expect(events.find(e => e._done)).toBeDefined();

  // The stream should have emitted a conversation_id.
  const cidEvent = events.find(e => typeof e.conversation_id === 'string');
  expect(cidEvent).toBeDefined();

  if (cidEvent) {
    const cid = cidEvent.conversation_id as string;
    // Conversation should now exist in BetterWebUI's store.
    const r = await request.get(`/api/conversations/${cid}`);
    expect(r.ok()).toBeTruthy();
    const conv = await r.json();
    expect(conv.id).toBe(cid);
    expect(Array.isArray(conv.messages)).toBe(true);
  }
});

// ── Services health via the full stack ───────────────────────────────────────

test('services health reports all components', async ({ request }) => {
  const r = await request.get('/api/services/health');
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  expect(typeof body.ok).toBe('boolean');
  expect(typeof body.services).toBe('object');
  for (const svc of ['clk', 'autogui', 'osso']) {
    expect(body.services[svc]).toBeDefined();
  }
});

test('enable/disable toggle round-trips correctly', async ({ request }) => {
  // Disable CLK.
  const disR = await request.post('/api/services/clk/disable');
  expect(disR.ok()).toBeTruthy();
  expect((await disR.json()).enabled).toBe(false);

  // Status reflects the change.
  const statusR = await request.get('/api/services/status');
  expect(statusR.ok()).toBeTruthy();
  expect((await statusR.json()).services.clk.enabled).toBe(false);

  // Disabled service returns 503.
  const wfR = await request.get('/api/services/clk/workflows');
  expect(wfR.status()).toBe(503);

  // Re-enable.
  const enR = await request.post('/api/services/clk/enable');
  expect(enR.ok()).toBeTruthy();
  expect((await enR.json()).enabled).toBe(true);
});
