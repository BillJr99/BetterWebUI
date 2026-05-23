/**
 * /api/lint — surface skill/mcp/cli linting issues to the UI.
 */
import { test, expect } from '@playwright/test';
import { ensureConfigured } from './helpers/ui-helpers';

test('lint endpoint returns a structured payload', async ({ request }) => {
  await ensureConfigured(request);
  const r = await request.get('/api/lint');
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  // Tolerate either an array of issues or an object grouping them.
  expect(typeof body === 'object').toBe(true);
});
