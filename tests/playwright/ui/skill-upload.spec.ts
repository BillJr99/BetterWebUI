/**
 * POST /api/skills/upload — upload a .md skill file directly.
 */
import { test, expect } from '@playwright/test';
import { ensureConfigured } from './helpers/ui-helpers';

const SKILL_BODY = `---
name: Uploaded PW Skill
description: A skill uploaded by Playwright as a multipart file.
---

When the user asks for the test thing, do it.
`;

test.beforeEach(async ({ request }) => { await ensureConfigured(request); });

test('upload a skill markdown file via multipart', async ({ request }) => {
  const r = await request.post('/api/skills/upload', {
    multipart: {
      file: { name: 'pw-uploaded.md', mimeType: 'text/markdown', buffer: Buffer.from(SKILL_BODY) },
    },
  });
  expect([200, 201].includes(r.status())).toBeTruthy();
  const body = await r.json();
  expect(body.id ?? body.skill?.id).toBeTruthy();

  const id = body.id ?? body.skill.id;
  await request.delete(`/api/skills/${id}`).catch(() => {});
});

test('upload rejects non-markdown files', async ({ request }) => {
  const r = await request.post('/api/skills/upload', {
    multipart: {
      file: { name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('not a skill') },
    },
  });
  // Endpoint should reject with a 4xx; the exact code may vary.
  expect(r.status()).toBeGreaterThanOrEqual(400);
  expect(r.status()).toBeLessThan(500);
});
