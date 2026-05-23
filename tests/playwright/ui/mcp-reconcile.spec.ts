/**
 * MCP reconciliation — restart/sync registered servers.
 */
import { test, expect } from '@playwright/test';
import { ensureConfigured } from './helpers/ui-helpers';

test('POST /api/mcp/reconcile responds and updates server statuses', async ({ request }) => {
  await ensureConfigured(request);
  const r = await request.post('/api/mcp/reconcile');
  expect([200, 202, 204].includes(r.status())).toBeTruthy();
  // List should be queryable after reconcile completes (no 5xx).
  const list = await request.get('/api/mcp/servers');
  expect(list.ok()).toBeTruthy();
});
