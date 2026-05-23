/**
 * Multimodal — attach an image, send with vision; ask for image generation.
 * Outcome assertions only.
 */
import { test, expect } from '@playwright/test';
import {
  gotoApp, dismissOnboardingIfPresent, sendChatMessage, waitForAssistantResponse,
  getLastAssistantText, ensureConfigured, pickModel,
} from './helpers/ui-helpers';
import { expectNonEmptyText } from './helpers/outcome-helpers';
import * as path from 'path';
import * as fs from 'fs';

const SAMPLE_PNG = path.join(__dirname, 'fixtures', 'sample.png');

test.beforeEach(async ({ page, request }) => {
  await ensureConfigured(request);
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
});

test('attach an image and get a non-empty response', async ({ page, request }) => {
  const model = await pickModel(request);
  test.skip(!model, 'no model configured');

  // Generate a tiny PNG once.
  if (!fs.existsSync(SAMPLE_PNG)) {
    fs.mkdirSync(path.dirname(SAMPLE_PNG), { recursive: true });
    // 1×1 transparent PNG
    const PNG = Buffer.from(
      '89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C4890000000A4944415478DA63000100000500010D0A2DB40000000049454E44AE426082',
      'hex',
    );
    fs.writeFileSync(SAMPLE_PNG, PNG);
  }

  // Enable vision toggle so the image is sent as a vision attachment.
  const vision = page.locator('#toggle-vision');
  if (await vision.isVisible().catch(() => false)) {
    await vision.check();
  }

  await page.locator('#attach-input').setInputFiles(SAMPLE_PNG);
  await page.locator('#attachments-preview').waitFor({ state: 'visible' });

  await sendChatMessage(page, 'Briefly describe the attached image.');
  await waitForAssistantResponse(page);
  const text = await getLastAssistantText(page);
  expectNonEmptyText(text);
});
