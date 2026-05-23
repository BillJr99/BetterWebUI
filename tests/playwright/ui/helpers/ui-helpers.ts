/**
 * ui-helpers.ts — DOM-level helpers shared across UI specs.
 *
 * Keeps spec files short and outcome-focused. Centralizes flaky selectors
 * (e.g., the onboarding overlay) so a UI change only needs one update here.
 */
import { Page, expect, APIRequestContext } from '@playwright/test';

export async function gotoApp(page: Page): Promise<void> {
  // Surface browser console warnings+errors so CI logs show JS exceptions
  // without needing to download Playwright traces.
  page.on('console', msg => {
    const t = msg.type();
    if (t === 'error') {
      console.log(`[browser:error] ${msg.text()}`);
    } else if (t === 'warning') {
      console.log(`[browser:warn]  ${msg.text()}`);
    }
  });
  // Log every /api/ response so we can see the full picture of what the app
  // called and whether it succeeded — not just the two endpoints we first
  // expected to fail.
  page.on('response', resp => {
    const url = resp.url();
    if (url.includes('/api/')) {
      const status = resp.status();
      const method = resp.request().method();
      if (status >= 400) {
        console.log(`[net:ERR] ${method} ${url} → ${status}`);
      } else if (status >= 300) {
        console.log(`[net:redirect] ${method} ${url} → ${status}`);
      }
      // Log slow responses (>5 s) regardless of status so we can spot hangs.
      resp.finished().then(() => {
        const timing = resp.request().timing();
        const elapsed = timing ? Math.round(timing.responseEnd - timing.requestStart) : -1;
        if (elapsed > 5_000) {
          console.log(`[net:slow] ${method} ${url} → ${status} (${elapsed}ms)`);
        }
      }).catch(() => {});
    }
  });
  await page.goto('/');
  await page.waitForLoadState('networkidle').catch(() => {});
  const title = await page.title().catch(() => '?');
  console.log(`[nav] loaded → title="${title}" url=${page.url()}`);
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
  if (await overlay.isHidden().catch(() => true)) {
    console.log('[onboarding] overlay not visible — skipping dismiss');
    return;
  }
  console.log('[onboarding] overlay visible — injecting hidden attribute');
  await overlay.evaluate((el) => el.setAttribute('hidden', ''));
}

export async function openTab(page: Page, tabId: string): Promise<void> {
  // tabId is one of: chats, workspaces, files, memory, scheduled, skills,
  // prompts, tools, settings.
  console.log(`[tab] opening tab: ${tabId}`);
  await page.locator(`#tab-btn-${tabId}`).click();
  await expect(page.locator(`#tab-${tabId}`)).toHaveClass(/active/);
  // Confirm computed display is actually 'block' — toHaveClass passing isn't
  // enough if a later async init pass overwrites the class set (or if some
  // other panel ends up overlapping). A few past failures looked like
  // "panel has .active but elements still report not-visible".
  const display = await page.locator(`#tab-${tabId}`).evaluate(
    (el) => getComputedStyle(el).display,
  ).catch(() => '?');
  console.log(`[tab] tab active: ${tabId} (display=${display})`);
}

export async function sendChatMessage(page: Page, text: string): Promise<void> {
  const preview = text.length > 80 ? text.slice(0, 77) + '...' : text;
  console.log(`[chat] sending: "${preview}"`);
  const input = page.locator('#composer-input');
  await input.click();
  await input.fill(text);
  await page.locator('#send-btn').click();
  console.log(`[chat] message sent`);
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
  const startedAt = Date.now();
  console.log(`[wait] waiting for assistant response (timeout=${timeoutMs / 1000}s, minLen=${minLen})`);

  const last = page.locator('#messages .message.assistant').last();
  // Log how many assistant bubbles already exist before we start waiting.
  const countBefore = await page.locator('#messages .message.assistant').count().catch(() => -1);
  console.log(`[wait] assistant bubbles already in DOM: ${countBefore}`);

  await expect(last).toBeVisible({ timeout: timeoutMs }).catch(async (err) => {
    // Dump page state before re-throwing so CI logs show what went wrong.
    const msgCount = await page.locator('#messages .message').count().catch(() => -1);
    const html = await page.locator('#messages').innerHTML().catch(() => '<unavailable>');
    console.log(`[wait:ERR] assistant bubble never became visible after ${Math.round((Date.now() - startedAt) / 1000)}s`);
    console.log(`[wait:ERR] #messages child count: ${msgCount}`);
    console.log(`[wait:ERR] #messages innerHTML (first 800 chars): ${html.slice(0, 800)}`);
    throw err;
  });

  console.log(`[wait] assistant bubble appeared after ${Math.round((Date.now() - startedAt) / 1000)}s`);

  // Watch the .content element specifically: the bubble's outer text always
  // contains the role label ("Assistant") plus action button labels, even
  // during the placeholder phase. .content is empty (typing dots have no
  // text) until the model's response starts streaming in.
  const content = last.locator('.content');
  let loggedAt = Date.now();
  await expect.poll(
    async () => {
      const len = (await content.innerText().catch(() => '')).trim().length;
      const now = Date.now();
      if (now - loggedAt > 15_000) {
        console.log(`[wait] assistant content length=${len} elapsed=${Math.round((now - startedAt) / 1000)}s`);
        loggedAt = now;
      }
      return len;
    },
    { timeout: timeoutMs, intervals: [1000, 2000, 3000] },
  ).toBeGreaterThanOrEqual(minLen);

  const finalLen = (await content.innerText().catch(() => '')).trim().length;
  console.log(`[wait] response complete: length=${finalLen} total=${Math.round((Date.now() - startedAt) / 1000)}s`);
  // Settle: streaming class should clear (best-effort).
  await page.waitForTimeout(500);
}

export async function getLastAssistantText(page: Page): Promise<string> {
  // Read .content only so we get the model's reply, not the "Assistant" role
  // label or the action-button labels that surround it.
  const last = page.locator('#messages .message.assistant').last().locator('.content');
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
  if (!owUrl || !owKey) {
    console.log(`[config] ensureConfigured: missing base_url or api_key — skipping POST (url="${owUrl ? '(set)' : ''}", key="${owKey ? '(set)' : ''}")`);
    return;
  }
  const payload: Record<string, unknown> = { base_url: owUrl, api_key: owKey, onboarding_done: true };
  if (model) payload.default_model = model;
  console.log(`[config] posting /api/config: base_url=${owUrl} model=${model || '(none)'}`);
  const r = await request.post('/api/config', { data: payload }).catch(() => null);
  if (r) {
    console.log(`[config] /api/config POST → ${r.status()}`);
  }
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
    if (body.default_model) {
      console.log(`[model] resolved from /api/config default_model: ${body.default_model}`);
      return body.default_model;
    }
    console.log(`[model] /api/config has no default_model (onboarding_done=${body.onboarding_done})`);
  } else {
    console.log(`[model] /api/config returned ${cfg.status()}`);
  }
  const models = await request.get('/api/models');
  if (models.ok()) {
    const body = await models.json();
    if (Array.isArray(body.models) && body.models.length > 0) {
      const id = body.models[0].id ?? '';
      console.log(`[model] resolved from /api/models first entry: ${id} (${body.models.length} total)`);
      return id;
    }
    console.log(`[model] /api/models returned empty list`);
  } else {
    console.log(`[model] /api/models returned ${models.status()}`);
  }
  console.log(`[model] no model found — test will be skipped`);
  return '';
}
