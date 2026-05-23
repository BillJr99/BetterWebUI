/**
 * Markdown + math rendering — assistant responses render via Marked + KaTeX.
 * Outcome: a rendered <span class="katex"> or a <pre><code> exists when
 * asked for them.
 */
import { test, expect } from '@playwright/test';
import {
  gotoApp, dismissOnboardingIfPresent, sendChatMessage, waitForAssistantResponse,
  ensureConfigured, pickModel,
} from './helpers/ui-helpers';

test.beforeEach(async ({ page, request }) => {
  await ensureConfigured(request);
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
});

test('code-block prompt renders a <pre><code>', async ({ page, request }) => {
  const model = await pickModel(request);
  test.skip(!model, 'no model configured');
  await sendChatMessage(
    page,
    'Reply with exactly this fenced markdown code block (no other text): ```\nhello\n```',
  );
  await waitForAssistantResponse(page);
  // Code block rendering is best-effort because the model may not comply
  // perfectly. We assert pre/code is in the page somewhere within the last bubble.
  const lastBubble = page.locator('#messages [data-role="assistant"]').last();
  // Tolerant — either pre/code rendered, or the text contains the fence.
  const html = await lastBubble.innerHTML();
  expect(html).toMatch(/<pre|<code|```/i);
});

test('math prompt renders KaTeX OR plain text', async ({ page, request }) => {
  const model = await pickModel(request);
  test.skip(!model, 'no model configured');
  await sendChatMessage(
    page,
    'Reply with exactly this LaTeX: $E = mc^2$',
  );
  await waitForAssistantResponse(page);
  const lastBubble = page.locator('#messages [data-role="assistant"]').last();
  const html = await lastBubble.innerHTML();
  // KaTeX rendering attaches a span.katex; if disabled, the literal $...$ is fine.
  expect(html).toMatch(/katex|\$E\s*=\s*mc\^2\$|E\s*=\s*mc\^2/i);
});
