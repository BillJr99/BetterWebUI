#!/usr/bin/env bash
# run-all-tests.sh — Unified test runner.
#
# Drives the same setup_wizard.py used by the regular start scripts, then runs:
#   1) pytest (Python unit + service-integration)
#   2) Playwright integration suite (API-level)
#   3) Playwright UI suite (browser-driven)
#   4) Curl smoke tests
#
# Requirements: Python 3.10+, Node.js 18+, git, curl, and an OpenWebUI
# instance the wizard can reach. (For docker-based CI, see deploy/start.sh
# --test or the e2e-ui workflow.)
#
# Usage:
#   ./scripts/run-all-tests.sh
#   ./scripts/run-all-tests.sh --no-wizard       # CI: env already set
#   ./scripts/run-all-tests.sh --reconfigure     # force re-prompt
#   ./scripts/run-all-tests.sh --skip-ui         # skip browser UI tests
#   ./scripts/run-all-tests.sh --keep-going      # don't fail-fast
#   ./scripts/run-all-tests.sh --docker          # bring up + tear down deploy/docker-compose.e2e.yml
#   ./scripts/run-all-tests.sh --docker-compose deploy/docker-compose.e2e.yml
#                                                # tear down a specific test compose file on exit
#                                                # (also via BWUI_TEST_COMPOSE_FILE env var)
#   ./scripts/run-all-tests.sh -- --grep settings  # passes "--grep settings" to playwright

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PARENT_DIR="$(cd "$REPO_ROOT/.." && pwd)"
PLAYWRIGHT_DIR="$REPO_ROOT/tests/playwright"
ENV_FILE="$REPO_ROOT/deploy/.env"

CLK_DIR="$PARENT_DIR/cognitiveloopkernel"
AUTOGUI_DIR="$PARENT_DIR/autogui"
OSSO_DIR="$PARENT_DIR/osscreenobserver"

BWUI_PORT=8765
CLK_PORT=8001
AUTOGUI_PORT=8002
OSSO_PORT=5001

# ── Flag parsing ──────────────────────────────────────────────────────────────
NO_WIZARD=0
RECONFIGURE=0
SKIP_PYTHON=0
SKIP_PLAYWRIGHT=0
SKIP_UI=0
SKIP_SMOKE=0
KEEP_GOING=0
DOCKER_UP=0
DOCKER_COMPOSE_FILE="${BWUI_TEST_COMPOSE_FILE:-}"
PLAYWRIGHT_EXTRA=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-wizard)      NO_WIZARD=1; shift ;;
        --reconfigure)    RECONFIGURE=1; shift ;;
        --skip-python)    SKIP_PYTHON=1; shift ;;
        --skip-playwright)SKIP_PLAYWRIGHT=1; shift ;;
        --skip-ui)        SKIP_UI=1; shift ;;
        --skip-smoke)     SKIP_SMOKE=1; shift ;;
        --keep-going)     KEEP_GOING=1; shift ;;
        --docker)
            DOCKER_UP=1
            DOCKER_COMPOSE_FILE="$REPO_ROOT/deploy/docker-compose.e2e.yml"
            shift
            ;;
        --docker-compose)
            DOCKER_COMPOSE_FILE="$2"
            shift 2
            ;;
        --) shift; PLAYWRIGHT_EXTRA=("$@"); break ;;
        -h|--help)
            sed -n '2,25p' "$0"
            exit 0
            ;;
        *) echo "Unknown flag: $1" >&2; exit 1 ;;
    esac
done

# ── Helpers ───────────────────────────────────────────────────────────────────
PIDS=()
STAGE_FAILURES=()
err()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "  $*"; }

cleanup() {
    echo ""
    echo "=== Stopping services started by this run ==="
    for pid in "${PIDS[@]:-}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true

    if [[ -n "$DOCKER_COMPOSE_FILE" ]]; then
        if [[ -f "$DOCKER_COMPOSE_FILE" ]] && command -v docker >/dev/null 2>&1; then
            echo "=== Tearing down docker stack ($DOCKER_COMPOSE_FILE) ==="
            docker compose -f "$DOCKER_COMPOSE_FILE" down -v --remove-orphans \
                2>/dev/null || true
        fi
    fi
}
trap cleanup EXIT INT TERM

wait_for() {
    local name="$1" url="$2" max="${3:-60}"
    for ((i=0; i<max; i++)); do
        if curl -sf "$url" >/dev/null 2>&1; then
            info "✓ $name"
            return 0
        fi
        sleep 2
    done
    echo "ERROR: Timed out waiting for $name at $url" >&2
    return 1
}

setup_venv() {
    local dir="$1"
    if [[ ! -d "$dir/.venv" ]]; then
        python3 -m venv "$dir/.venv"
    fi
    local pip="$dir/.venv/bin/pip"
    if [[ -f "$dir/requirements.txt" ]]; then
        "$pip" install -q -r "$dir/requirements.txt"
    elif [[ -f "$dir/pyproject.toml" ]]; then
        "$pip" install -q -e "$dir"
    fi
}

run_stage() {
    local label="$1"; shift
    echo ""
    echo "=================================================================="
    echo "  $label"
    echo "=================================================================="
    if "$@"; then
        echo "  ✓ $label"
    else
        STAGE_FAILURES+=("$label")
        if [[ $KEEP_GOING -eq 0 ]]; then
            echo "  ✗ $label — aborting (pass --keep-going to continue)"
            exit 1
        fi
        echo "  ✗ $label — continuing"
    fi
}

# ── Dependency checks ─────────────────────────────────────────────────────────
for cmd in python3 git node npm curl; do
    command -v "$cmd" >/dev/null 2>&1 || err "$cmd is required but not found in PATH"
done

# ── Optional: bring up the docker-based testing stack (Ollama + OpenWebUI) ────
if [[ $DOCKER_UP -eq 1 ]]; then
    command -v docker >/dev/null 2>&1 \
        || err "--docker requires the docker CLI in PATH"
    [[ -f "$DOCKER_COMPOSE_FILE" ]] \
        || err "Compose file not found: $DOCKER_COMPOSE_FILE"
    echo "=== Bringing up docker stack ($DOCKER_COMPOSE_FILE) ==="
    docker compose -f "$DOCKER_COMPOSE_FILE" up -d --build --wait \
        || err "docker compose up failed"
fi

# ── Stage 0: configuration via the shared wizard ──────────────────────────────
echo "=== BetterWebUI Unified Test Runner ==="

if [[ $NO_WIZARD -eq 0 ]]; then
    if [[ $RECONFIGURE -eq 1 ]]; then
        python3 "$SCRIPT_DIR/setup_wizard.py" --reconfigure --env-file "$ENV_FILE" \
            || err "Setup wizard cancelled"
    else
        # Validate first; fall back to interactive if anything's missing.
        if ! python3 "$SCRIPT_DIR/setup_wizard.py" --non-interactive \
                --env-file "$ENV_FILE" 2>/dev/null; then
            python3 "$SCRIPT_DIR/setup_wizard.py" --env-file "$ENV_FILE" \
                || err "Setup wizard cancelled"
        fi
    fi
fi

# Load the fanned-out env vars into this shell.
if ! eval "$(python3 "$SCRIPT_DIR/setup_wizard.py" \
                --print-env --env-file "$ENV_FILE" 2>/dev/null)"; then
    err "Could not load OpenWebUI configuration from $ENV_FILE — re-run without --no-wizard"
fi

# Aliases used by the launch blocks below.
OPENWEBUI_URL="$OPENWEBUI_BASE_URL"
DEFAULT_MODEL="${OPENWEBUI_MODEL:-}"

# ── Stage 1: ensure submodule directories exist ──────────────────────────────
clone_or_update() {
    local name="$1" url="$2" dir="$3"
    if [[ -d "$dir/.git" ]]; then
        info "Updating $name..."
        git -C "$dir" fetch origin --quiet || true
        git -C "$dir" merge --ff-only origin/main --quiet 2>/dev/null \
            || info "(could not fast-forward $name — using current HEAD)"
    else
        info "Cloning $name..."
        git clone "$url" "$dir" --quiet
    fi
}

echo ""
echo "=== Ensuring submodule repos exist ==="
clone_or_update "cognitiveloopkernel" \
    "https://github.com/billjr99/cognitiveloopkernel.git" "$CLK_DIR"
clone_or_update "autogui" \
    "https://github.com/billjr99/autogui.git" "$AUTOGUI_DIR"
clone_or_update "osscreenobserver" \
    "https://github.com/billjr99/osscreenobserver.git" "$OSSO_DIR"

# ── Stage 2: install Python deps ─────────────────────────────────────────────
echo ""
echo "=== Installing Python dependencies ==="
info "BetterWebUI..."
setup_venv "$REPO_ROOT"
"$REPO_ROOT/.venv/bin/pip" install -q pytest pytest-asyncio python-frontmatter
info "CognitiveLoopKernel..."
setup_venv "$CLK_DIR"
info "AutoGUI..."
setup_venv "$AUTOGUI_DIR"
info "OSScreenObserver..."
setup_venv "$OSSO_DIR"

# ── Stage 3: start services with BWUI_TEST_MODE=1 ────────────────────────────
echo ""
echo "=== Starting services ==="

# CognitiveLoopKernel
(
    cd "$CLK_DIR"
    CLK_API_PORT=$CLK_PORT \
    CLK_WORKSPACES_DIR="${TMPDIR:-/tmp}/bwui-runall-clk-workspaces" \
    CLK_PROVIDER=openwebui \
    CLK_OPENWEBUI_ENDPOINT="$OPENWEBUI_URL" \
    CLK_OPENWEBUI_API_KEY="$OPENWEBUI_API_KEY" \
    CLK_OPENWEBUI_MODEL="$DEFAULT_MODEL" \
    "$CLK_DIR/.venv/bin/python" -m clk_harness.api \
        >"${TMPDIR:-/tmp}/bwui-runall-clk.log" 2>&1
) &
PIDS+=($!)

# AutoGUI (dry-run)
(
    cd "$AUTOGUI_DIR"
    AUTOGUI_DRY_RUN=true \
    AUTOGUI_API_PORT=$AUTOGUI_PORT \
    OPENWEBUI_BASE_URL="$OPENWEBUI_URL" \
    OPENWEBUI_API_KEY="$OPENWEBUI_API_KEY" \
    OPENWEBUI_MODEL="$DEFAULT_MODEL" \
    "$AUTOGUI_DIR/.venv/bin/python" api.py \
        >"${TMPDIR:-/tmp}/bwui-runall-autogui.log" 2>&1
) &
PIDS+=($!)

# OSScreenObserver (mock)
(
    cd "$OSSO_DIR"
    "$OSSO_DIR/.venv/bin/python" main.py --mock --mode inspect \
        >"${TMPDIR:-/tmp}/bwui-runall-osso.log" 2>&1
) &
PIDS+=($!)

# BetterWebUI — test mode on so /api/test/reset is available
(
    cd "$REPO_ROOT"
    PORT=$BWUI_PORT \
    BWUI_TEST_MODE=1 \
    BWUI_DATA_DIR="${TMPDIR:-/tmp}/bwui-runall-data" \
    CLK_BASE_URL="http://localhost:$CLK_PORT" \
    AUTOGUI_BASE_URL="http://localhost:$AUTOGUI_PORT" \
    OSSO_BASE_URL="http://localhost:$OSSO_PORT" \
    "$REPO_ROOT/.venv/bin/python" app.py \
        >"${TMPDIR:-/tmp}/bwui-runall-bwui.log" 2>&1
) &
PIDS+=($!)

echo ""
echo "=== Waiting for services ==="
wait_for "CognitiveLoopKernel" "http://localhost:$CLK_PORT/api/healthz" 60 \
    || err "CLK never came up — see ${TMPDIR:-/tmp}/bwui-runall-clk.log"
wait_for "AutoGUI"             "http://localhost:$AUTOGUI_PORT/api/healthz" 60 \
    || err "AutoGUI never came up — see ${TMPDIR:-/tmp}/bwui-runall-autogui.log"
wait_for "OSScreenObserver"    "http://localhost:$OSSO_PORT/api/healthz" 60 \
    || err "OSSO never came up — see ${TMPDIR:-/tmp}/bwui-runall-osso.log"
wait_for "BetterWebUI"         "http://localhost:$BWUI_PORT/api/health" 90 \
    || err "BetterWebUI never came up — see ${TMPDIR:-/tmp}/bwui-runall-bwui.log"

# Pre-configure BetterWebUI via /api/config so onboarding doesn't appear.
echo ""
echo "=== Configuring BetterWebUI ==="
CONFIG_PAYLOAD=$(python3 -c "
import json, os
print(json.dumps({
    'base_url': os.environ['OPENWEBUI_BASE_URL'],
    'api_key':  os.environ['OPENWEBUI_API_KEY'],
    **({'default_model': os.environ['OPENWEBUI_MODEL']} if os.environ.get('OPENWEBUI_MODEL') else {}),
}))
")
curl -sf -X POST "http://localhost:$BWUI_PORT/api/config" \
    -H "Content-Type: application/json" \
    -d "$CONFIG_PAYLOAD" >/dev/null
info "✓ BetterWebUI configured"

# ── Stage 4: Python tests ────────────────────────────────────────────────────
if [[ $SKIP_PYTHON -eq 0 ]]; then
    run_stage "[1/4] Python tests (pytest)" \
        "$REPO_ROOT/.venv/bin/pytest" tests/ --ignore=tests/playwright -q
fi

# ── Stage 5: Playwright deps (one-shot) ──────────────────────────────────────
if [[ $SKIP_PLAYWRIGHT -eq 0 || $SKIP_UI -eq 0 ]]; then
    (
        cd "$PLAYWRIGHT_DIR"
        echo ""
        echo "=== Installing Playwright dependencies ==="
        npm install --silent
        npx playwright install chromium --with-deps
    ) || err "Failed to install Playwright"
fi

# ── Stage 6: existing Playwright integration suite ───────────────────────────
if [[ $SKIP_PLAYWRIGHT -eq 0 ]]; then
    run_stage "[2/4] Playwright integration suite" bash -c "
        cd '$PLAYWRIGHT_DIR' && \
        BETTERWEBUI_URL=http://localhost:$BWUI_PORT \
        OPENWEBUI_BASE_URL='$OPENWEBUI_URL' \
        OPENWEBUI_API_KEY='$OPENWEBUI_API_KEY' \
        DEFAULT_MODEL='$DEFAULT_MODEL' \
        npx playwright test --config local.config.ts ${PLAYWRIGHT_EXTRA[*]:-}
    "
fi

# ── Stage 7: new UI suite ────────────────────────────────────────────────────
if [[ $SKIP_UI -eq 0 ]]; then
    run_stage "[3/4] Playwright UI suite (browser-driven)" bash -c "
        cd '$PLAYWRIGHT_DIR' && \
        BETTERWEBUI_URL=http://localhost:$BWUI_PORT \
        OPENWEBUI_BASE_URL='$OPENWEBUI_URL' \
        OPENWEBUI_API_KEY='$OPENWEBUI_API_KEY' \
        DEFAULT_MODEL='$DEFAULT_MODEL' \
        npx playwright test --config ui.config.ts ${PLAYWRIGHT_EXTRA[*]:-}
    "
fi

# ── Stage 8: smoke tests ─────────────────────────────────────────────────────
if [[ $SKIP_SMOKE -eq 0 ]]; then
    run_stage "[4/4] Smoke tests" bash -c \
        "BWUI_URL=http://localhost:$BWUI_PORT $SCRIPT_DIR/run-smoke-tests.sh"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "=================================================================="
if [[ ${#STAGE_FAILURES[@]} -eq 0 ]]; then
    echo "  ✓ All test stages passed."
    echo "  UI report: $PLAYWRIGHT_DIR/ui-report/index.html"
    exit 0
else
    echo "  ✗ ${#STAGE_FAILURES[@]} stage(s) failed:"
    for s in "${STAGE_FAILURES[@]}"; do echo "    - $s"; done
    echo "  UI report: $PLAYWRIGHT_DIR/ui-report/index.html"
    exit 1
fi
