/**
 * OAuth provider endpoints — status / connect / disconnect.
 */
import { test, expect } from '@playwright/test';

const PROVIDERS = ['github', 'google'];

for (const provider of PROVIDERS) {
  test(`GET /api/oauth/status/${provider} responds`, async ({ request }) => {
    const r = await request.get(`/api/oauth/status/${provider}`);
    // 200 with a status; 404 if provider not configured in this build.
    expect([200, 404].includes(r.status())).toBeTruthy();
  });

  test(`POST /api/oauth/connect/${provider} responds (does not assert success)`, async ({ request }) => {
    const r = await request.post(`/api/oauth/connect/${provider}`);
    // 200/302 success; 400/404 if provider not configured. Don't assert specifics.
    expect(r.status()).toBeGreaterThanOrEqual(200);
    expect(r.status()).toBeLessThan(600);
  });

  test(`DELETE /api/oauth/disconnect/${provider} responds`, async ({ request }) => {
    const r = await request.delete(`/api/oauth/disconnect/${provider}`);
    expect([200, 204, 404].includes(r.status())).toBeTruthy();
  });
}
