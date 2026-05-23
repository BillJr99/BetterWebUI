/**
 * Services enable/disable matrix — exercise toggling for all three services
 * via every entry point (API direct, Settings UI, and via the /api/services/status).
 */
import { test, expect } from '@playwright/test';
import {
  gotoApp, dismissOnboardingIfPresent, openTab, ensureConfigured,
} from './helpers/ui-helpers';

const SERVICES = ['clk', 'autogui', 'osso'] as const;

test.beforeEach(async ({ page, request }) => {
  await ensureConfigured(request);
  // Restore all to enabled at the start.
  for (const s of SERVICES) {
    await request.post(`/api/services/${s}/enable`).catch(() => {});
  }
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
});

for (const svc of SERVICES) {
  test(`API enable/disable round-trip for ${svc}`, async ({ request }) => {
    const dis = await request.post(`/api/services/${svc}/disable`);
    expect(dis.ok()).toBeTruthy();
    expect((await dis.json()).enabled).toBe(false);

    const status = await request.get('/api/services/status');
    expect((await status.json()).services[svc].enabled).toBe(false);

    const en = await request.post(`/api/services/${svc}/enable`);
    expect(en.ok()).toBeTruthy();
    expect((await en.json()).enabled).toBe(true);
  });

  test(`Settings UI toggle for ${svc} flips the API state`, async ({ page, request }) => {
    await openTab(page, 'settings');
    const toggle = page.locator(`#svc-${svc}-enabled`);
    await toggle.uncheck();
    await expect.poll(async () => {
      const r = await request.get('/api/services/status');
      const body = await r.json();
      return body.services[svc].enabled;
    }, { timeout: 10_000 }).toBe(false);
    await toggle.check();
    await expect.poll(async () => {
      const r = await request.get('/api/services/status');
      const body = await r.json();
      return body.services[svc].enabled;
    }, { timeout: 10_000 }).toBe(true);
  });
}
