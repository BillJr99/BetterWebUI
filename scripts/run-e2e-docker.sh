#!/usr/bin/env bash
# run-e2e-docker.sh — deploy the full e2e stack (Ollama + OpenWebUI + all
# services) using Docker Compose and run the Playwright end-to-end tests.
#
# Requirements: Docker Desktop (running), Node.js 18+
# Usage:
#   ./scripts/run-e2e-docker.sh
#   OLLAMA_MODEL=phi3:mini ./scripts/run-e2e-docker.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PLAYWRIGHT_DIR="$REPO_ROOT/tests/playwright"

# ── Dependency checks ─────────────────────────────────────────────────────────
err() { echo "ERROR: $*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || err "Docker is required (https://www.docker.com/products/docker-desktop/)"
docker info >/dev/null 2>&1      || err "Docker daemon is not running — start Docker Desktop first."
command -v node >/dev/null 2>&1  || err "Node.js 18+ is required (https://nodejs.org/)"
command -v npm  >/dev/null 2>&1  || err "npm is required (comes with Node.js)"

NODE_MAJOR=$(node -e 'process.stdout.write(process.versions.node.split(".")[0])')
[[ "$NODE_MAJOR" -ge 18 ]] || err "Node.js 18+ required (found $(node --version))"

# ── Playwright setup ──────────────────────────────────────────────────────────
echo "=== Installing Playwright dependencies ==="
cd "$PLAYWRIGHT_DIR"
npm install --silent
npx playwright install chromium --with-deps

# ── Run ───────────────────────────────────────────────────────────────────────
OLLAMA_MODEL="${OLLAMA_MODEL:-tinyllama:1.1b}"
echo ""
echo "=== Starting e2e stack and running tests ==="
echo "    Model : $OLLAMA_MODEL"
echo "    (First run pulls the model — this may take several minutes)"
echo ""

OLLAMA_MODEL="$OLLAMA_MODEL" npm run test:e2e

echo ""
echo "=== Done ==="
