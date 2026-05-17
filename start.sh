#!/usr/bin/env bash
# BetterWebUI launcher for macOS / Linux.
# Pulls git submodules, starts sibling services if they are not already
# running, then starts BetterWebUI. Services started by this script are
# stopped automatically when the script exits (Ctrl-C or normal termination).

set -e
cd "$(dirname "${BASH_SOURCE[0]}")"

REPO_ROOT="$(pwd)"

CLK_PORT="${CLK_PORT:-8001}"
AUTOGUI_PORT="${AUTOGUI_PORT:-8002}"
OSSO_PORT="${OSSO_PORT:-5001}"
PORT="${PORT:-8765}"

CLK_DIR="$REPO_ROOT/CognitiveLoopKernel"
AUTOGUI_DIR="$REPO_ROOT/AutoGUI"
OSSO_DIR="$REPO_ROOT/OSScreenObserver"

STARTED_PIDS=()

cleanup() {
    if [[ ${#STARTED_PIDS[@]} -gt 0 ]]; then
        echo ""
        echo "Stopping services started by this script..."
        for pid in "${STARTED_PIDS[@]}"; do
            kill "$pid" 2>/dev/null || true
        done
        wait 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

is_up() { curl -sf "$1" >/dev/null 2>&1; }

setup_venv() {
    local dir="$1"
    if [[ ! -d "$dir/.venv" ]]; then
        python3 -m venv "$dir/.venv"
    fi
    local pip="$dir/.venv/bin/pip"
    if [[ -f "$dir/requirements.txt" ]]; then
        "$pip" install -q --upgrade pip
        "$pip" install -q -r "$dir/requirements.txt"
    elif [[ -f "$dir/pyproject.toml" ]]; then
        "$pip" install -q --upgrade pip
        "$pip" install -q -e "$dir"
    fi
}

# ── Pull submodules ───────────────────────────────────────────────────────────
echo "Updating git submodules..."
git submodule update --init --recursive

# ── BetterWebUI virtualenv ────────────────────────────────────────────────────
if [ ! -d ".venv" ]; then
  echo "First-time setup: creating a Python environment and installing packages..."
  python3 -m venv .venv
  ./.venv/bin/pip install --upgrade pip >/dev/null
  ./.venv/bin/pip install -r requirements.txt
fi

# ── CognitiveLoopKernel ───────────────────────────────────────────────────────
if is_up "http://localhost:$CLK_PORT/api/healthz"; then
    echo "CognitiveLoopKernel already running on port $CLK_PORT — skipping."
else
    echo "Starting CognitiveLoopKernel..."
    setup_venv "$CLK_DIR"
    (
        cd "$CLK_DIR"
        CLK_API_PORT=$CLK_PORT exec "$CLK_DIR/.venv/bin/python" -m clk_harness.api
    ) &
    STARTED_PIDS+=("$!")
fi

# ── AutoGUI ───────────────────────────────────────────────────────────────────
if is_up "http://localhost:$AUTOGUI_PORT/api/healthz"; then
    echo "AutoGUI already running on port $AUTOGUI_PORT — skipping."
else
    echo "Starting AutoGUI..."
    setup_venv "$AUTOGUI_DIR"
    (
        cd "$AUTOGUI_DIR"
        AUTOGUI_API_PORT=$AUTOGUI_PORT exec "$AUTOGUI_DIR/.venv/bin/python" api.py
    ) &
    STARTED_PIDS+=("$!")
fi

# ── OSScreenObserver ──────────────────────────────────────────────────────────
if is_up "http://localhost:$OSSO_PORT/api/healthz"; then
    echo "OSScreenObserver already running on port $OSSO_PORT — skipping."
else
    echo "Starting OSScreenObserver..."
    setup_venv "$OSSO_DIR"
    (
        cd "$OSSO_DIR"
        exec "$OSSO_DIR/.venv/bin/python" main.py
    ) &
    STARTED_PIDS+=("$!")
fi

echo ""
echo "BetterWebUI is starting on http://127.0.0.1:${PORT}"
echo "Open that link in your browser. Press Ctrl-C in this window to stop."
echo ""

# Run uvicorn as a child process (not exec) so Ctrl-C stops uvicorn
# and drops back to the shell rather than closing the terminal.
./.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port "$PORT"
