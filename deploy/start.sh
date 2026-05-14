#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Build Docker images
echo "[1/4] Building Docker images..."
docker compose -f "$SCRIPT_DIR/docker-compose.integration.yml" build

# Start core services
echo "[2/4] Starting services..."
docker compose -f "$SCRIPT_DIR/docker-compose.integration.yml" up -d

# Wait for BetterWebUI to be healthy
echo "[3/4] Waiting for services to be ready..."
timeout 60 bash -c 'until curl -sf http://localhost:8080/api/health > /dev/null; do sleep 2; done'
echo "Services ready."

# Run Playwright tests (optional - pass --test flag)
if [[ "${1:-}" == "--test" ]]; then
    echo "[4/4] Running Playwright tests..."
    cd "$REPO_ROOT/tests/playwright"
    npm ci
    npx playwright install --with-deps
    docker compose -f "$SCRIPT_DIR/docker-compose.integration.yml" --profile test up -d
    npx playwright test
    docker compose -f "$SCRIPT_DIR/docker-compose.integration.yml" --profile test down
else
    echo "[4/4] Skipping tests (pass --test to run Playwright tests)."
fi

echo "Done! BetterWebUI running at http://localhost:8080"
