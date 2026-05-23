/**
 * ui-helpers.ts — DOM-level helpers shared across UI specs.
 *
 * Keeps spec files short and outcome-focused. Centralizes flaky selectors
 * (e.g., the onboarding overlay) so a UI change only needs one update here.
 */
import { Page, expect, APIRequestContext } from '@playwright/test';

export async function gotoApp(page: Page): Promise<void> {
  // Surface browser console errors in the test runner output so CI logs show
  // JS exceptions without needing to download Playwright traces.
  page.on('console', msg => {
    if (msg.type() === 'error') console.log(`[browser:error] ${msg.text()}`);
  });
  // Log non-2xx responses on key API routes so we can tell "network error"
  // from "model too slow" in CI without reading SSE body content.
  page.on('response', resp => {
    const url = resp.url();
    if ((url.includes('/api/chat') || url.includes('/api/config')) && resp.status() >= 400) {
      console.log(`[net] ${resp.request().method()} ${url} → ${resp.status()}`);
    }
  });
  await page.goto('/');
  await page.waitForLoadState('networkidle').catch(() => {});
}

/**
 * Bypass onboarding by either ensuring config is already set (so the overlay
 * never shows) or by closing it if it does. The full onboarding flow is
 * exercised in onboarding.spec.ts.
 *
 * `init()` in app.js calls `checkOnboarding()` LAST, after several network
 * awaits (loadConfig, refreshModels, …). A one-shot `isHidden` check can
 * therefore pass while the overlay is briefly hidden, then init() finishes
 * loading, sees `onboarding_done === false` for any reason (stale config,
 * race with /api/config POST, etc.) and pops the overlay open AFTER we've
 * "dismissed" it — blocking the next click. We address this by also
 * injecting a permanent CSS rule that keeps the overlay hidden for the
 * remainder of the page lifetime.
 */
export async function dismissOnboardingIfPresent(page: Page): Promise<void> {
  await page.addStyleTag({
    content: '#onboarding-overlay { display: none !important; }',
  }).catch(() => {});
  const overlay = page.locator('#onboarding-overlay');
  if (await overlay.isHidden().catch(() => true)) return;
  await overlay.evaluate((el) => el.setAttribute('hidden', ''));
}

export async function openTab(page: Page, tabId: string): Promise<void> {
  // tabId is one of: chats, workspaces, files, memory, scheduled, skills,
  // prompts, tools, settings.
  await page.locator(`#tab-btn-${tabId}`).click();
  await expect(page.locator(`#tab-${tabId}`)).toHaveClass(/active/);
}

export async function sendChatMessage(page: Page, text: string): Promise<void> {
  const input = page.locator('#composer-input');
  await input.click();
  await input.fill(text);
  await page.locator('#send-btn').click();
}

/**
 * Wait for an assistant response bubble to appear and finish streaming.
 * Outcome: at least one assistant message with non-empty text content exists
 * in #messages by the timeout.
 *
 * Default timeout 480 s. tinyllama on a 2-core CI runner has a measured
 * end-to-end latency of ~120–250 s for a short reply when the system prompt
 * includes the full tool-protocol block (~1k tokens). Vision turns bloat the
 * prompt with base64 image data and can take 3–5 min even for a 1×1 PNG.
 * 480 s gives us ~2× headroom on the worst observed case.
 *
 * Tests that need multiple round-trips (e.g. new-chat creation) rely on the
 * suite-level timeout in ui.config.ts (currently 960 s) to give two slow
 * turns room.
 */
export async function waitForAssistantResponse(
  page: Page,
  opts: { timeoutMs?: number; minLengthChars?: number } = {},
): Promise<void> {
  const timeoutMs = opts.timeoutMs ?? 480_000;
  const minLen   = opts.minLengthChars ?? 1;
  const last = page.locator('#messages [data-role="assistant"]').last();
  await expect(last).toBeVisible({ timeout: timeoutMs });
  let loggedAt = Date.now();
  await expect.poll(
    async () => {
      const len = (await last.innerText().catch(() => '')).trim().length;
      const now = Date.now();
      if (now - loggedAt > 15_000) {
        console.log(`[wait] assistant bubble length=${len} elapsed=${Math.round((now - loggedAt) / 1000)}s`);
        loggedAt = now;
      }
      return len;
    },
    { timeout: timeoutMs, intervals: [1000, 2000, 3000] },
  ).toBeGreaterThanOrEqual(minLen);
  // Settle: streaming class should clear (best-effort).
  await page.waitForTimeout(500);
}

export async function getLastAssistantText(page: Page): Promise<string> {
  const last = page.locator('#messages [data-role="assistant"]').last();
  return (await last.innerText().catch(() => '')).trim();
}

/**
 * Wipe persistent server state via the test-mode reset endpoint. No-op when
 * BWUI_TEST_MODE != 1 on the server (returns 404, which we tolerate).
 */
export async function resetServerState(request: APIRequestContext): Promise<void> {
  const r = await request.post('/api/test/reset').catch(() => null);
  if (r && !r.ok() && r.status() !== 404) {
    throw new Error(`/api/test/reset returned ${r.status()}`);
  }
}

/**
 * Ensure /api/config has a base_url + api_key set. Reads OPENWEBUI_BASE_URL /
 * OPENWEBUI_API_KEY / DEFAULT_MODEL from process.env (set by the test runner).
 * No-op if already configured.
 */
export async function ensureConfigured(request: APIRequestContext): Promise<void> {
  const owUrl = process.env.OPENWEBUI_DOCKER_URL ?? process.env.OPENWEBUI_BASE_URL ?? '';
  const owKey = process.env.OPENWEBUI_API_KEY  ?? '';
  const model = process.env.DEFAULT_MODEL       ?? process.env.OPENWEBUI_MODEL ?? '';
  if (!owUrl || !owKey) return;
  const payload: Record<string, unknown> = { base_url: owUrl, api_key: owKey, onboarding_done: true };
  if (model) payload.default_model = model;
  await request.post('/api/config', { data: payload }).catch(() => {});
}

/**
 * Look up the currently-selected default model from /api/config. Falls back
 * to the first item in /api/models if no default is configured. Returns ''
 * if neither yields a value.
 */
export async function pickModel(request: APIRequestContext): Promise<string> {
  const cfg = await request.get('/api/config');
  if (cfg.ok()) {
    const body = await cfg.json();
    if (body.default_model) return body.default_model;
  }
  const models = await request.get('/api/models');
  if (models.ok()) {
    const body = await models.json();
    if (Array.isArray(body.models) && body.models.length > 0) {
      return body.models[0].id ?? '';
    }
  }
  return '';
}
