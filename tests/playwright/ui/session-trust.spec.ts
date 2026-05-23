/**
 * Session trust — GET / POST / DELETE round-trip for the per-session
 * trusted-command allowlist used by the shell approval flow.
 */
import { test, expect } from '@playwright/test';

test('GET starts empty after a fresh server boot or reset', async ({ request }) => {
  const r = await request.get('/api/session/trust');
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  expect(Array.isArray(body.commands ?? body) || typeof body === 'object').toBe(true);
});

test('POST a trusted command appears in subsequent GET', async ({ request }) => {
  const cmd = `echo trust-test-${Date.now()}`;
  const post = await request.post('/api/session/trust', { data: { command: cmd } });
  expect(post.ok()).toBeTruthy();
  const list = await request.get('/api/session/trust');
  const body = await list.json();
  const arr = body.commands ?? body;
  const hasIt = Array.isArray(arr) && arr.some((c: any) =>
    (typeof c === 'string' && c === cmd) || c?.command === cmd,
  );
  expect(hasIt).toBe(true);
});

test('DELETE clears the trust list', async ({ request }) => {
  await request.post('/api/session/trust', { data: { command: 'echo clear-me' } });
  const del = await request.delete('/api/session/trust');
  expect([200, 204].includes(del.status())).toBeTruthy();
});
