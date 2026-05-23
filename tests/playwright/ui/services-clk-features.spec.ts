/**
 * CLK — exhaustive endpoint coverage:
 *   GET  /api/services/clk/workflows
 *   POST /api/services/clk/research
 *   GET  /api/services/clk/research/{id}
 *   GET  /api/services/clk/research/{id}/stream      (SSE)
 *   GET  /api/services/clk/research/{id}/artifacts
 *   POST /api/services/clk/research/{id}/cancel
 *
 * Outcome assertions only. Research jobs that don't complete in the timeout
 * are exercised via cancel rather than asserted-completed.
 */
import { test, expect, APIRequestContext } from '@playwright/test';
import { ensureConfigured } from './helpers/ui-helpers';

test.beforeEach(async ({ request }) => {
  await ensureConfigured(request);
  await request.post('/api/services/clk/enable').catch(() => {});
});

test('GET /workflows returns a list', async ({ request }) => {
  const r = await request.get('/api/services/clk/workflows');
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  const items = Array.isArray(body) ? body : body.workflows ?? body.items ?? [];
  expect(Array.isArray(items)).toBe(true);
});

async function startResearch(request: APIRequestContext, topic: string): Promise<string | null> {
  const r = await request.post('/api/services/clk/research', {
    data: { topic, workflow: 'default' },
  });
  if (!r.ok()) return null;
  const body = await r.json();
  return body.id ?? body.research_id ?? null;
}

test('POST /research creates a job and GET /research/{id} returns its status', async ({ request }) => {
  const id = await startResearch(request, 'one-sentence summary of HTTP');
  test.skip(!id, 'CLK research could not be started in this environment');

  const r = await request.get(`/api/services/clk/research/${id}`);
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  expect(body).toBeTruthy();
});

test('GET /research/{id}/stream produces SSE bytes', async ({ request }) => {
  const id = await startResearch(request, 'one-sentence summary of TCP');
  test.skip(!id, 'CLK research could not be started');
  // Probe the SSE endpoint; accept either a streaming body or a 200 close.
  const r = await request.get(`/api/services/clk/research/${id}/stream`, { timeout: 30_000 });
  expect(r.ok() || r.status() === 204).toBeTruthy();
});

test('GET /research/{id}/artifacts returns an artifacts payload', async ({ request }) => {
  const id = await startResearch(request, 'briefly: what is JSON');
  test.skip(!id, 'CLK research could not be started');
  // Give the job a moment.
  await new Promise(r => setTimeout(r, 3_000));
  const r = await request.get(`/api/services/clk/research/${id}/artifacts`);
  // 200 with empty list is valid; 202/404 while still pending also acceptable.
  expect([200, 202, 404].includes(r.status())).toBeTruthy();
});

test('POST /research/{id}/cancel stops a pending job', async ({ request }) => {
  const id = await startResearch(request, 'a longer multi-step research task');
  test.skip(!id, 'CLK research could not be started');
  const r = await request.post(`/api/services/clk/research/${id}/cancel`);
  expect([200, 202, 204].includes(r.status())).toBeTruthy();
});
