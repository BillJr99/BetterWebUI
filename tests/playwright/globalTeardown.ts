import { execSync } from 'child_process';

const COMPOSE_FILE = '../../deploy/docker-compose.integration.yml';

export default async function globalTeardown() {
  execSync(`docker compose -f ${COMPOSE_FILE} --profile test down`, { stdio: 'inherit', cwd: __dirname });
}
