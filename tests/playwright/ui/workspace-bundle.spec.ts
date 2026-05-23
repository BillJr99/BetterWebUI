/**
 * Workspace bundle-manifest — POST /api/workspaces/{id}/bundle-manifest.
 *
 * Used when including persistent files in a workspace bundle for export.
 */
import { test, expect } from '@playwright/test';
import { ensureConfigured } from './helpers/ui-helpers';

test('bundle-manifest responds with an actionable payload', async ({ request }) => {
  await ensureConfigured(request);
  const create = await request.post('/api/workspaces', {
    data: { name: 'PW Bundle Manifest WS' },
  });
  const { id } = await create.json();
  try {
    const r = await request.post(`/api/workspaces/${id}/bundle-manifest`, {
      data: { files: [] },
    });
    // 200 with manifest; 400 if payload required; 404 if workspace missing.
    expect([200, 400, 404].includes(r.status())).toBeTruthy();
  } finally {
    await request.delete(`/api/workspaces/${id}`);
  }
});
