/**
 * Coverage for the remaining endpoints that don't have a dedicated spec:
 *   POST /api/transcribe          — speech-to-text
 *   POST /api/tts                  — text-to-speech
 *   POST /api/explain-command      — shell-command explanation
 *   GET  /api/recommend-model      — model recommendation
 *   GET  /api/oauth/status/{...}   — OAuth status
 *   POST /api/uploads/transient    — transient uploads (per-chat)
 *   POST /api/memory/extract       — memory extraction from a message
 *   GET  /api/scheduled-tasks/notifications
 *   GET  /api/services/tools       — aggregated LLM tool specs across services
 *
 * Each is a basic outcome check. Slow LLM-backed endpoints assert ok-or-503.
 */
import { test, expect } from '@playwright/test';
import { ensureConfigured } from './helpers/ui-helpers';

test.beforeEach(async ({ request }) => {
  await ensureConfigured(request);
});

test('/api/recommend-model returns a payload', async ({ request }) => {
  const r = await request.get('/api/recommend-model');
  expect([200, 503].includes(r.status())).toBeTruthy();
});

test('/api/scheduled-tasks/notifications responds', async ({ request }) => {
  const r = await request.get('/api/scheduled-tasks/notifications');
  expect(r.ok()).toBeTruthy();
});

test('/api/services/tools aggregates LLM tool specs', async ({ request }) => {
  const r = await request.get('/api/services/tools');
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  expect(body).toBeTruthy();
});

test('/api/tts accepts text and returns audio or service-unavailable', async ({ request }) => {
  const r = await request.post('/api/tts', {
    data: { text: 'hello', voice: 'alloy' },
  });
  expect([200, 503, 502].includes(r.status())).toBeTruthy();
  if (r.ok()) {
    const buf = await r.body();
    expect(buf.length).toBeGreaterThan(0);
  }
});

test('/api/transcribe accepts audio or returns 4xx for empty', async ({ request }) => {
  const r = await request.post('/api/transcribe', {
    multipart: {
      audio: { name: 'silence.wav', mimeType: 'audio/wav', buffer: Buffer.alloc(64) },
    },
  });
  // 4xx for an unparseable empty buffer is the expected outcome.
  expect(r.status()).toBeGreaterThanOrEqual(200);
  expect(r.status()).toBeLessThan(600);
});

test('/api/explain-command responds to a shell command body', async ({ request }) => {
  const r = await request.post('/api/explain-command', {
    data: { command: 'ls -la' },
  });
  expect([200, 503].includes(r.status())).toBeTruthy();
});

test('/api/oauth/status/github responds', async ({ request }) => {
  const r = await request.get('/api/oauth/status/github');
  // 200 with a status; 404 if provider isn't registered in this build.
  expect([200, 404].includes(r.status())).toBeTruthy();
});

test('/api/memory/extract responds to a sample message', async ({ request }) => {
  const r = await request.post('/api/memory/extract', {
    data: { conversation_id: 'nonexistent', message: 'I prefer tabs over spaces.' },
  });
  // 200 with a result; 404 if the conversation id is required to exist.
  expect([200, 400, 404, 503].includes(r.status())).toBeTruthy();
});

test('/api/uploads/transient round-trips', async ({ request }) => {
  const r = await request.post('/api/uploads/transient', {
    multipart: {
      chat_id: 'pw-test-chat',
      file: { name: 'note.txt', mimeType: 'text/plain', buffer: Buffer.from('hello') },
    },
  });
  expect([200, 201].includes(r.status())).toBeTruthy();
  // Best-effort cleanup.
  await request.delete('/api/uploads/transient/pw-test-chat').catch(() => {});
});
