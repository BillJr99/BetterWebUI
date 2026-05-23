/**
 * /api/chat stream — verify the SSE response shape and the trusted-mode flag.
 * Backstop for the e2e/chat.spec.ts coverage with explicit assertions on
 * incremental deltas and the final _done event.
 */
import { test, expect } from '@playwright/test';
import { collectSSEPost } from '../helpers/sse';
import { ensureConfigured, pickModel } from './helpers/ui-helpers';

test('streams deltas and ends with a _done sentinel', async ({ baseURL, request }) => {
  await ensureConfigured(request);
  const model = await pickModel(request);
  test.skip(!model, 'no model configured');

  const body = {
    model,
    messages: [{ role: 'user', content: 'Reply with one short word only.' }],
    mode: 'trusted',
  };
  const events = await collectSSEPost(`${baseURL}/api/chat`, body, 200, 240_000);
  expect(events.length).toBeGreaterThan(0);
  const deltas = events.filter((e) => typeof e.delta === 'string' && e.delta);
  expect(deltas.length).toBeGreaterThan(0);
  const done = events.find((e) => e._done === true);
  expect(done).toBeDefined();
});

test('returns a conversation_id we can fetch back', async ({ baseURL, request }) => {
  await ensureConfigured(request);
  const model = await pickModel(request);
  test.skip(!model, 'no model configured');

  const body = {
    model,
    messages: [{ role: 'user', content: 'One word reply, please.' }],
    mode: 'trusted',
  };
  const events = await collectSSEPost(`${baseURL}/api/chat`, body, 200, 240_000);
  const cidEvent = events.find((e) => typeof e.conversation_id === 'string');
  expect(cidEvent).toBeDefined();
  const r = await request.get(`/api/conversations/${cidEvent!.conversation_id}`);
  expect(r.ok()).toBeTruthy();
});
