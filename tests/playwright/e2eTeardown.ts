import { execSync } from 'child_process';
import * as fs from 'fs';
import { E2E_STATE_FILE } from './e2eSetup';

const COMPOSE_FILE = '../../deploy/docker-compose.e2e.yml';

export default async function globalTeardown() {
  // Clean up state file.
  if (fs.existsSync(E2E_STATE_FILE)) fs.unlinkSync(E2E_STATE_FILE);

  execSync(
    `docker compose -f ${COMPOSE_FILE} down -v`,
    { stdio: 'inherit', cwd: `${__dirname}/../../deploy` }
  );
}
