/**
 * File bundles — Files tab. Bundles attach to chats and are managed via the
 * sidebar. We verify the tab opens and the new-bundle button is present;
 * actual bundle creation involves a multi-step modal that varies by build.
 */
import { test, expect } from '@playwright/test';
import { gotoApp, dismissOnboardingIfPresent, openTab, ensureConfigured } from './helpers/ui-helpers';

test.beforeEach(async ({ page, request }) => {
  await ensureConfigured(request);
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
  await openTab(page, 'files');
});

test('Files tab opens with new-bundle button', async ({ page }) => {
  // Dump diagnostics if the button isn't visible — the new logging in openTab
  // shows the panel has display=block, so something deeper is preventing
  // visibility. Capture the actual computed state for next-run debugging.
  await expect(page.locator('#new-bundle-btn')).toBeVisible().catch(async (err) => {
    const diag = await page.evaluate(() => {
      const btn = document.getElementById('new-bundle-btn');
      const panel = document.getElementById('tab-files');
      const sidebar = document.getElementById('sidebar');
      const dump = (el: Element | null) => {
        if (!el) return 'null';
        const s = getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return JSON.stringify({
          tag: el.tagName, id: el.id, cls: el.className,
          display: s.display, visibility: s.visibility, opacity: s.opacity,
          width: r.width, height: r.height, top: r.top, left: r.left,
          inDOM: document.body.contains(el),
        });
      };
      return {
        btn: dump(btn),
        panel: dump(panel),
        sidebar: dump(sidebar),
        body: dump(document.body),
        bodyAriaHidden: document.body.getAttribute('aria-hidden'),
        bodyInert: document.body.hasAttribute('inert'),
        activeTabPanels: Array.from(document.querySelectorAll('.tab-panel.active')).map(p => p.id),
      };
    });
    console.log('[bundles:DIAG]', JSON.stringify(diag, null, 2));
    throw err;
  });
  await expect(page.locator('#bundle-list')).toBeVisible();
});

test('Files tab quota indicator renders', async ({ page }) => {
  // Quota element exists even if empty.
  await expect(page.locator('#bundles-quota')).toBeAttached();
});
