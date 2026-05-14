#!/usr/bin/env bash
# run-e2e-local.sh — clone sibling repos, start all services locally (no
# Docker), prompt for an OpenWebUI URL and API key, then run the full
# Playwright test suite including CLK, AutoGUI, and OSScreenObserver tests.
#
# Requirements: Python 3.10+, Node.js 18+, git, curl
# Usage:
#   ./scripts/run-e2e-local.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PARENT_DIR="$(cd "$REPO_ROOT/.." && pwd)"
PLAYWRIGHT_DIR="$REPO_ROOT/tests/playwright"

CLK_DIR="$PARENT_DIR/cognitiveloopkernel"
AUTOGUI_DIR="$PARENT_DIR/autogui"
OSSO_DIR="$PARENT_DIR/osscreenobserver"

BWUI_PORT=8765
CLK_PORT=8001
AUTOGUI_PORT=8002
OSSO_PORT=5001

PIDS=()

# ── Helpers ───────────────────────────────────────────────────────────────────
err()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "  $*"; }

cleanup() {
    echo ""
    echo "=== Stopping services ==="
    for pid in "${PIDS[@]:-}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
    echo "Done."
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
    exit 1
}

# ── Dependency checks ─────────────────────────────────────────────────────────
for cmd in python3 git node npm curl; do
    command -v "$cmd" >/dev/null 2>&1 || err "$cmd is required but not found in PATH"
done

PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
if [[ "$PY_MAJOR" -lt 3 || ("$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 10) ]]; then
    err "Python 3.10+ required (found $PY_VER)"
fi

NODE_MAJOR=$(node -e 'process.stdout.write(process.versions.node.split(".")[0])')
[[ "$NODE_MAJOR" -ge 18 ]] || err "Node.js 18+ required (found $(node --version))"

# ── Prompt for OpenWebUI config ───────────────────────────────────────────────
echo ""
echo "=== BetterWebUI End-to-End Test Runner (local) ==="
echo ""
echo "You need a running OpenWebUI instance with at least one model loaded."
echo ""

read -rp "OpenWebUI base URL [http://localhost:3000]: " OPENWEBUI_URL
OPENWEBUI_URL="${OPENWEBUI_URL:-http://localhost:3000}"

read -rsp "OpenWebUI API key: " OPENWEBUI_API_KEY
echo ""

read -rp "Model name for chat tests (leave blank to auto-select first available): " DEFAULT_MODEL
DEFAULT_MODEL="${DEFAULT_MODEL:-}"

echo ""

# ── Clone / update sibling repos ─────────────────────────────────────────────
echo "=== Setting up repositories ==="

clone_or_update() {
    local name="$1" url="$2" dir="$3"
    if [[ -d "$dir/.git" ]]; then
        info "Updating $name..."
        git -C "$dir" fetch origin --quiet
        git -C "$dir" merge --ff-only origin/main --quiet 2>/dev/null \
            || info "(could not fast-forward $name — skipping update)"
    else
        info "Cloning $name..."
        git clone "$url" "$dir" --quiet
    fi
}

clone_or_update "cognitiveloopkernel" \
    "https://github.com/billjr99/cognitiveloopkernel.git" "$CLK_DIR"
clone_or_update "autogui" \
    "https://github.com/billjr99/autogui.git" "$AUTOGUI_DIR"
clone_or_update "osscreenobserver" \
    "https://github.com/billjr99/osscreenobserver.git" "$OSSO_DIR"

# ── Set up Python virtual environments ───────────────────────────────────────
echo ""
echo "=== Installing Python dependencies ==="

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

info "BetterWebUI..."
setup_venv "$REPO_ROOT"
info "CognitiveLoopKernel..."
setup_venv "$CLK_DIR"
info "AutoGUI..."
setup_venv "$AUTOGUI_DIR"
info "OSScreenObserver..."
setup_venv "$OSSO_DIR"

# ── Start services ────────────────────────────────────────────────────────────
echo ""
echo "=== Starting services ==="

# CognitiveLoopKernel
(
    cd "$CLK_DIR"
    CLK_API_PORT=$CLK_PORT \
    CLK_WORKSPACES_DIR="${TMPDIR:-/tmp}/bwui-e2e-clk-workspaces" \
    "$CLK_DIR/.venv/bin/python" -m clk_harness.api \
        >"${TMPDIR:-/tmp}/bwui-e2e-clk.log" 2>&1
) &
PIDS+=($!)

# AutoGUI (dry-run so it doesn't touch the real desktop)
(
    cd "$AUTOGUI_DIR"
    AUTOGUI_DRY_RUN=true \
    AUTOGUI_API_PORT=$AUTOGUI_PORT \
    OPENWEBUI_BASE_URL="$OPENWEBUI_URL" \
    OPENWEBUI_API_KEY="$OPENWEBUI_API_KEY" \
    "$AUTOGUI_DIR/.venv/bin/python" api.py \
        >"${TMPDIR:-/tmp}/bwui-e2e-autogui.log" 2>&1
) &
PIDS+=($!)

# OSScreenObserver (mock mode — no real screen access needed)
(
    cd "$OSSO_DIR"
    "$OSSO_DIR/.venv/bin/python" main.py --mock --mode inspect \
        >"${TMPDIR:-/tmp}/bwui-e2e-osso.log" 2>&1
) &
PIDS+=($!)

# BetterWebUI
(
    cd "$REPO_ROOT"
    PORT=$BWUI_PORT \
    WORKSPACE_DIR="${TMPDIR:-/tmp}/bwui-e2e-workspace" \
    BWUI_DATA_DIR="${TMPDIR:-/tmp}/bwui-e2e-data" \
    CLK_BASE_URL="http://localhost:$CLK_PORT" \
    AUTOGUI_BASE_URL="http://localhost:$AUTOGUI_PORT" \
    OSSO_BASE_URL="http://localhost:$OSSO_PORT" \
    "$REPO_ROOT/.venv/bin/python" app.py \
        >"${TMPDIR:-/tmp}/bwui-e2e-bwui.log" 2>&1
) &
PIDS+=($!)

echo ""
echo "=== Waiting for services (this may take ~30s on first launch) ==="
wait_for "CognitiveLoopKernel" "http://localhost:$CLK_PORT/api/healthz" 60
wait_for "AutoGUI"             "http://localhost:$AUTOGUI_PORT/api/healthz" 60
wait_for "OSScreenObserver"    "http://localhost:$OSSO_PORT/api/healthz" 60
wait_for "BetterWebUI"         "http://localhost:$BWUI_PORT/api/health" 90

# ── Configure BetterWebUI ─────────────────────────────────────────────────────
echo ""
echo "=== Configuring BetterWebUI ==="
CONFIG_PAYLOAD="{\"base_url\":\"$OPENWEBUI_URL\",\"api_key\":\"$OPENWEBUI_API_KEY\""
[[ -n "$DEFAULT_MODEL" ]] && CONFIG_PAYLOAD+=",\"default_model\":\"$DEFAULT_MODEL\""
CONFIG_PAYLOAD+="}"

curl -sf -X POST "http://localhost:$BWUI_PORT/api/config" \
    -H "Content-Type: application/json" \
    -d "$CONFIG_PAYLOAD" >/dev/null
info "✓ BetterWebUI configured"

# ── Run Playwright tests ──────────────────────────────────────────────────────
echo ""
echo "=== Installing Playwright ==="
cd "$PLAYWRIGHT_DIR"
npm install --silent
npx playwright install chromium --with-deps

echo ""
echo "=== Running tests ==="
echo "    BetterWebUI : http://localhost:$BWUI_PORT"
echo "    OpenWebUI   : $OPENWEBUI_URL"
[[ -n "$DEFAULT_MODEL" ]] && echo "    Model       : $DEFAULT_MODEL"
echo ""

BETTERWEBUI_URL="http://localhost:$BWUI_PORT" \
OPENWEBUI_BASE_URL="$OPENWEBUI_URL" \
OPENWEBUI_API_KEY="$OPENWEBUI_API_KEY" \
DEFAULT_MODEL="$DEFAULT_MODEL" \
    npx playwright test --config local.config.ts

echo ""
echo "=== All tests passed ==="
