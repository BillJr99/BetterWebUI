import { execSync } from 'child_process';
import { request } from '@playwright/test';

const COMPOSE_FILE = '../../deploy/docker-compose.integration.yml';
const SERVICES = {
  betterwebui: 'http://localhost:8080/api/services/health',
  clk: 'http://localhost:8001/api/healthz',
  autogui: 'http://localhost:8002/api/healthz',
  osso: 'http://localhost:5001/api/healthz',
};

async function waitForService(name: string, url: string, maxRetries = 30) {
  const ctx = await request.newContext();
  for (let i = 0; i < maxRetries; i++) {
    try {
      const r = await ctx.get(url, { timeout: 3000 });
      if (r.ok()) { console.log(`  ✓ ${name}`); return; }
    } catch {}
    await new Promise(r => setTimeout(r, 2000));
  }
  throw new Error(`Service ${name} did not become ready at ${url}`);
}

export default async function globalSetup() {
  console.log('Starting integration stack...');
  execSync(
    `docker compose -f ${COMPOSE_FILE} --profile test up -d --build --wait`,
    { stdio: 'inherit', cwd: __dirname }
  );
  console.log('Waiting for services...');
  await Promise.all(Object.entries(SERVICES).map(([n, u]) => waitForService(n, u)));
  console.log('All services ready.');
}
