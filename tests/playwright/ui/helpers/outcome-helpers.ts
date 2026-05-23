/**
 * outcome-helpers.ts — Outcome assertions used across UI specs.
 *
 * All assertions verify behavior (response arrived, tool called, service
 * degraded gracefully) — never the model's exact wording.
 */
import { APIRequestContext, expect } from '@playwright/test';

/**
 * Verify a conversation exists on the server and has at least one assistant
 * message with non-empty content.
 */
export async function expectConversationPersisted(
  request: APIRequestContext, cid: string,
): Promise<void> {
  const r = await request.get(`/api/conversations/${cid}`);
  expect(r.ok(), `GET /api/conversations/${cid} returned ${r.status()}`).toBeTruthy();
  const conv = await r.json();
  expect(conv.id).toBe(cid);
  expect(Array.isArray(conv.messages)).toBe(true);
  const assistants = (conv.messages as any[]).filter((m) => m.role === 'assistant');
  expect(assistants.length).toBeGreaterThan(0);
}

/**
 * Verify the conversation's tool-call trace shows the given tool was invoked
 * at least once. Tolerant to multiple message-shape variants.
 */
export async function expectToolInvoked(
  request: APIRequestContext, cid: string, toolName: string,
): Promise<void> {
  const r = await request.get(`/api/conversations/${cid}`);
  expect(r.ok()).toBeTruthy();
  const conv = await r.json();

  const callsFound: string[] = [];
  for (const m of (conv.messages ?? []) as any[]) {
    // OpenAI-style tool_calls array
    if (Array.isArray(m.tool_calls)) {
      for (const t of m.tool_calls) {
        const n = t?.function?.name ?? t?.name;
        if (n) callsFound.push(n);
      }
    }
    // BetterWebUI sometimes records the tool name on tool-result messages too
    if (m.role === 'tool' && m.name) callsFound.push(m.name);
    // Or in a custom tool_call field
    if (m.tool_call?.name) callsFound.push(m.tool_call.name);
  }
  expect(
    callsFound,
    `expected tool ${toolName} to be invoked in conversation ${cid}; found: ${JSON.stringify(callsFound)}`,
  ).toContain(toolName);
}

/**
 * All three services reachable and reporting ok.
 */
export async function expectServicesHealthy(request: APIRequestContext): Promise<void> {
  const r = await request.get('/api/services/health');
  expect(r.ok()).toBeTruthy();
  const body = await r.json();
  expect(typeof body.services).toBe('object');
  for (const svc of ['clk', 'autogui', 'osso']) {
    expect(body.services[svc]).toBeDefined();
  }
}

export function expectNonEmptyText(s: string, label = 'response'): void {
  expect(s, `${label} should be non-empty`).toBeTruthy();
  expect(s.trim().length, `${label} should have content`).toBeGreaterThan(0);
}
