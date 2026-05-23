/**
 * Uploads — persistent attachment (/api/upload) and transient per-chat upload
 * (/api/uploads/transient + DELETE).
 */
import { test, expect } from '@playwright/test';
import { ensureConfigured } from './helpers/ui-helpers';

test.beforeEach(async ({ request }) => { await ensureConfigured(request); });

test('POST /api/upload accepts a file', async ({ request }) => {
  const r = await request.post('/api/upload', {
    multipart: {
      file: { name: 'pw-upload.txt', mimeType: 'text/plain', buffer: Buffer.from('hello pw') },
    },
  });
  expect([200, 201].includes(r.status())).toBeTruthy();
  const body = await r.json();
  expect(body).toBeTruthy();
});

test('transient upload + delete round-trip', async ({ request }) => {
  const cid = `pw-transient-${Date.now()}`;
  const up = await request.post('/api/uploads/transient', {
    multipart: {
      chat_id: cid,
      file: { name: 't.txt', mimeType: 'text/plain', buffer: Buffer.from('temp') },
    },
  });
  expect([200, 201].includes(up.status())).toBeTruthy();

  const del = await request.delete(`/api/uploads/transient/${cid}`);
  expect([200, 204].includes(del.status())).toBeTruthy();
});
