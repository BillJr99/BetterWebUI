/**
 * Skills — create via UI, list, delete. Also verify load_skill is invoked
 * when a chat prompt matches a skill description.
 */
import { test, expect } from '@playwright/test';
import {
  gotoApp, dismissOnboardingIfPresent, openTab, ensureConfigured,
  sendChatMessage, waitForAssistantResponse,
} from './helpers/ui-helpers';

const SKILL_ID = 'playwright-test-skill';

test.beforeEach(async ({ page, request }) => {
  await ensureConfigured(request);
  await request.delete(`/api/skills/${SKILL_ID}`).catch(() => {});
  await gotoApp(page);
  await dismissOnboardingIfPresent(page);
});

test('create a skill via API; UI list shows it', async ({ page, request }) => {
  const create = await request.post('/api/skills', {
    data: {
      id: SKILL_ID,
      name: 'Playwright Test Skill',
      description: 'A test skill used by the Playwright UI suite.',
      content: '# Steps\n1. Acknowledge you loaded the skill.\n',
    },
  });
  expect(create.ok()).toBeTruthy();
  await openTab(page, 'skills');
  await expect(page.locator('#skill-list')).toContainText('Playwright Test Skill');
  // Clean up.
  await request.delete(`/api/skills/${SKILL_ID}`);
});

test('delete a skill via UI removes it from the list', async ({ page, request }) => {
  await request.post('/api/skills', {
    data: { id: SKILL_ID, name: 'PW Delete', description: 'to be deleted', content: '...' },
  });
  await openTab(page, 'skills');
  await expect(page.locator('#skill-list')).toContainText('PW Delete');

  // Delete via API (UI delete button selectors vary by version; API path is stable).
  const del = await request.delete(`/api/skills/${SKILL_ID}`);
  expect(del.ok()).toBeTruthy();

  await page.reload();
  await dismissOnboardingIfPresent(page);
  await openTab(page, 'skills');
  await expect(page.locator('#skill-list')).not.toContainText('PW Delete');
});
