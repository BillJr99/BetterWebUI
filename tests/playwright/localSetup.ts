/**
 * localSetup.ts — global setup for the local (no-Docker) test run.
 *
 * Services are already started by scripts/run-e2e-local.sh before Playwright
 * is invoked, so this setup only needs to wait for readiness and optionally
 * configure BetterWebUI if environment variables are provided.
 *
 * The shell script sets: BETTERWEBUI_URL, OPENWEBUI_BASE_URL,
 * OPENWEBUI_API_KEY, DEFAULT_MODEL.
 */
import { request } from '@playwright/test';

const BWUI_URL    = process.env.BETTERWEBUI_URL   ?? 'http://localhost:8765';
const OW_URL      = process.env.OPENWEBUI_BASE_URL ?? '';
const OW_KEY      = process.env.OPENWEBUI_API_KEY  ?? '';
const MODEL       = process.env.DEFAULT_MODEL      ?? '';

async function waitForUrl(name: string, url: string, maxRetries = 45, intervalMs = 2000) {
  const ctx = await request.newContext();
  for (let i = 0; i < maxRetries; i++) {
    try {
      const r = await ctx.get(url, { timeout: 3000 });
      if (r.ok()) { console.log(`  ✓ ${name}`); await ctx.dispose(); return; }
    } catch {}
    await new Promise(r => setTimeout(r, intervalMs));
  }
  await ctx.dispose();
  throw new Error(`Timed out waiting for ${name} at ${url}`);
}

export default async function globalSetup() {
  console.log('Waiting for local services…');
  await Promise.all([
    waitForUrl('BetterWebUI',      `${BWUI_URL}/api/health`),
    waitForUrl('CLK',              'http://localhost:8001/api/healthz'),
    waitForUrl('AutoGUI',          'http://localhost:8002/api/healthz'),
    waitForUrl('OSScreenObserver', 'http://localhost:5001/api/healthz'),
  ]);

  // Configure BetterWebUI if the shell script provided credentials.
  if (OW_URL && OW_KEY) {
    const ctx = await request.newContext({ baseURL: BWUI_URL });
    const payload: Record<string, string> = { base_url: OW_URL, api_key: OW_KEY };
    if (MODEL) payload.default_model = MODEL;
    const r = await ctx.post('/api/config', { data: payload });
    await ctx.dispose();
    if (r.ok()) {
      console.log('  ✓ BetterWebUI configured');
    } else {
      console.warn('  Warning: failed to configure BetterWebUI (will use existing config)');
    }
  }
}
