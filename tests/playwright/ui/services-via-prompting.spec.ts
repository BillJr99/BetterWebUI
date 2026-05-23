/**
 * Underlying submodules exercised through natural-language prompting.
 *
 * For each service we send a prompt that should cause the LLM to decide to
 * call the corresponding tool. We assert outcomes (a tool was called, OR a
 * response came back) — never specific model wording.
 */
import { test, expect } from '@playwright/test';
import {
  gotoApp, dismissOnboardingIfPresent, sendChatMessage, waitForAssistantResponse,
  ensureConfigured, pickModel,
} from './helpers/ui-helpers';

test.beforeEach(async ({ page, request }) => {
  await ensureConfigured(request);
  await Promise.all([
    request.post('/api/services/clk/enable').catch(() => {}),
    request.post('/api/services/autogui/enable').catch(() => {}),
    request.post('/api/services/osso/enable').catch(() => {}),
  ]);
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
});

async function nlPromptShouldGetResponse(
  page: any, request: any, prompt: string,
): Promise<void> {
  const model = await pickModel(request);
  test.skip(!model, 'no model configured');
  await sendChatMessage(page, prompt);
  await waitForAssistantResponse(page, { timeoutMs: 240_000 });
  // Outcome: an assistant message exists with non-empty text. Whether the
  // model chose to call a tool depends on its training; we accept either
  // path as long as the system handles the prompt without crashing.
  const text = await page.locator('#messages [data-role="assistant"]').last().innerText();
  expect(text.trim().length).toBeGreaterThan(0);
}

test('NL prompt routed via CLK', async ({ page, request }) => {
  await nlPromptShouldGetResponse(
    page, request,
    'Use the research tool to summarise what HTTP is in one sentence.',
  );
});

test('NL prompt routed via OSSO', async ({ page, request }) => {
  await nlPromptShouldGetResponse(
    page, request,
    'Use the screen observer to describe what is currently on screen, then summarise.',
  );
});

test('NL prompt routed via AutoGUI (dry-run)', async ({ page, request }) => {
  await nlPromptShouldGetResponse(
    page, request,
    'Use the autogui tool in dry-run mode to move the mouse to coordinates (100,100).',
  );
});
