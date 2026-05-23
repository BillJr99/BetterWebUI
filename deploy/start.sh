#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Validate / prompt for OpenWebUI configuration before docker compose ───────
# Non-interactive validation: exit 2 if anything is missing → run the
# interactive wizard. Skips if python3 isn't available (e.g. on minimal CI).
if command -v python3 >/dev/null 2>&1; then
    if ! python3 "$REPO_ROOT/scripts/setup_wizard.py" \
            --non-interactive --env-file "$SCRIPT_DIR/.env" 2>/dev/null; then
        echo "OpenWebUI configuration incomplete — launching wizard..."
        python3 "$REPO_ROOT/scripts/setup_wizard.py" \
            --env-file "$SCRIPT_DIR/.env" || {
            echo "Setup wizard cancelled — aborting." >&2
            exit 1
        }
    fi
fi

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
