import { execSync } from 'child_process';
import { request } from '@playwright/test';

const COMPOSE_FILE = '../../deploy/docker-compose.e2e.yml';
const BWUI_URL = process.env.BETTERWEBUI_URL ?? 'http://localhost:8080';
const OLLAMA_URL = process.env.OLLAMA_URL ?? 'http://localhost:11434';
const OPENWEBUI_URL = process.env.OPENWEBUI_URL ?? 'http://localhost:3000';
const OLLAMA_MODEL = process.env.OLLAMA_MODEL ?? 'tinyllama:1.1b';

// Shared between setup and tests via an env file written at the end.
export const E2E_STATE_FILE = '/tmp/bwui_e2e_state.json';

async function waitForUrl(name: string, url: string, maxRetries = 40, intervalMs = 3000) {
  const ctx = await request.newContext();
  for (let i = 0; i < maxRetries; i++) {
    try {
      const r = await ctx.get(url, { timeout: 4000 });
      if (r.ok()) { console.log(`  ✓ ${name} ready`); await ctx.dispose(); return; }
    } catch {}
    await new Promise(r => setTimeout(r, intervalMs));
  }
  await ctx.dispose();
  throw new Error(`Timed out waiting for ${name} at ${url}`);
}

async function pullModel(model: string) {
  console.log(`  Pulling ${model} (this may take a few minutes on first run)…`);
  const ctx = await request.newContext({ timeout: 600_000 });
  // Ollama pull is a streaming NDJSON response — POST and wait for completion.
  const r = await ctx.post(`${OLLAMA_URL}/api/pull`, {
    data: { model, stream: false },
    timeout: 600_000,
  });
  if (!r.ok()) {
    const body = await r.text();
    throw new Error(`Failed to pull model ${model}: ${r.status()} ${body}`);
  }
  await ctx.dispose();
  console.log(`  ✓ ${model} ready`);
}

async function setupOpenWebUI(): Promise<string> {
  const ctx = await request.newContext({ baseURL: OPENWEBUI_URL });

  // Create the first admin user (OpenWebUI accepts the first signup as admin).
  const signupR = await ctx.post('/api/v1/auths/signup', {
    data: {
      name: 'E2E Admin',
      email: 'e2e@bwui.test',
      password: 'bwui-e2e-pass',
    },
  });
  let token: string;
  if (signupR.ok()) {
    const body = await signupR.json();
    token = body.token;
    console.log('  ✓ OpenWebUI admin user created');
  } else {
    // User already exists (re-run) — sign in instead.
    const signinR = await ctx.post('/api/v1/auths/signin', {
      data: { email: 'e2e@bwui.test', password: 'bwui-e2e-pass' },
    });
    if (!signinR.ok()) throw new Error('OpenWebUI auth failed');
    token = (await signinR.json()).token;
    console.log('  ✓ OpenWebUI signed in');
  }

  // Create an API key for BetterWebUI to use.
  const keyR = await ctx.post('/api/v1/auths/api_key', {
    headers: { Authorization: `Bearer ${token}` },
  });
  await ctx.dispose();
  if (!keyR.ok()) throw new Error(`Failed to create OpenWebUI API key: ${keyR.status()}`);
  const apiKey: string = (await keyR.json()).api_key;
  console.log('  ✓ OpenWebUI API key obtained');
  return apiKey;
}

async function configureBetterWebUI(openWebuiApiKey: string) {
  const ctx = await request.newContext({ baseURL: BWUI_URL });
  const r = await ctx.post('/api/config', {
    data: {
      base_url: OPENWEBUI_URL,
      api_key: openWebuiApiKey,
      default_model: OLLAMA_MODEL,
    },
  });
  await ctx.dispose();
  if (!r.ok()) throw new Error(`Failed to configure BetterWebUI: ${r.status()}`);
  console.log('  ✓ BetterWebUI configured');
}

export default async function globalSetup() {
  const COMPOSE_CWD = `${__dirname}/../../deploy`;

  console.log('\n=== E2E Stack Setup ===');
  console.log('Starting containers…');
  execSync(
    `docker compose -f ${COMPOSE_FILE} up -d --build --wait`,
    { stdio: 'inherit', cwd: COMPOSE_CWD }
  );

  console.log('Waiting for services…');
  await waitForUrl('Ollama', `${OLLAMA_URL}/api/tags`);
  await pullModel(OLLAMA_MODEL);
  await waitForUrl('OpenWebUI', `${OPENWEBUI_URL}/health`);
  await waitForUrl('BetterWebUI', `${BWUI_URL}/api/health`);

  console.log('Configuring…');
  const apiKey = await setupOpenWebUI();
  await configureBetterWebUI(apiKey);

  // Persist state for tests that need the api key.
  const fs = await import('fs');
  fs.writeFileSync(E2E_STATE_FILE, JSON.stringify({ openWebuiApiKey: apiKey }));

  console.log('=== E2E Stack Ready ===\n');
}
