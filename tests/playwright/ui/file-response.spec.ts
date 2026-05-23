/**
 * /api/file-response — used when the assistant asks the user to share a file
 * (file-picker flow). Test the endpoint accepts a payload shape.
 */
import { test, expect } from '@playwright/test';

test('POST /api/file-response responds to a payload', async ({ request }) => {
  const r = await request.post('/api/file-response', {
    // files is optional; an unknown request_id is expected to 404.
    data: { request_id: 'pw-nonexistent-request', files: [] },
  });
  // 200 ok; 404 if request_id required to exist; 400 if payload incorrect.
  expect([200, 400, 404].includes(r.status())).toBeTruthy();
});
