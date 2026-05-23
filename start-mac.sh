#!/usr/bin/env bash
# BetterWebUI launcher for macOS.
# Installs prerequisites via Homebrew if needed (with prompts), pulls git
# submodules, installs Python packages, starts sibling services if not already
# running, then starts BetterWebUI. Services started by this script are stopped
# automatically when the script exits (Ctrl-C or normal termination).

set -e
cd "$(dirname "${BASH_SOURCE[0]}")"

REPO_ROOT="$(pwd)"
ENV_FILE="$REPO_ROOT/deploy/.env"

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

die()  { echo "ERROR: $*" >&2; exit 1; }
is_up() { curl -sf "$1" >/dev/null 2>&1; }

ask_yn() {
    local prompt="$1" ans
    read -rp "$prompt [Y/n]: " ans
    [[ "${ans:-y}" =~ ^[Yy] ]]
}

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

echo "================================="
echo "  BetterWebUI — macOS launcher"
echo "================================="
echo ""

# ── Homebrew ──────────────────────────────────────────────────────────────────
if ! command -v brew >/dev/null 2>&1; then
    echo "Homebrew is not installed. It is the recommended way to manage Python"
    echo "and Git on macOS."
    if ask_yn "Install Homebrew now?"; then
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        # Make brew available in this session (Apple Silicon default path)
        if [[ -x /opt/homebrew/bin/brew ]]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        elif [[ -x /usr/local/bin/brew ]]; then
            eval "$(/usr/local/bin/brew shellenv)"
        fi
    else
        echo "Skipping Homebrew. Ensure Python 3.10+ and git are installed manually."
    fi
fi

# ── Python 3.10+ ──────────────────────────────────────────────────────────────
NEED_PYTHON=false
if ! command -v python3 >/dev/null 2>&1; then
    NEED_PYTHON=true
else
    PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PY_MAJOR="${PY_VER%%.*}"
    PY_MINOR="${PY_VER##*.}"
    if [[ "$PY_MAJOR" -lt 3 || ( "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 10 ) ]]; then
        echo "Python $PY_VER found, but 3.10+ is required."
        NEED_PYTHON=true
    fi
fi

if $NEED_PYTHON; then
    if command -v brew >/dev/null 2>&1; then
        if ask_yn "Install Python 3 via Homebrew?"; then
            brew install python@3
            BREW_PY="$(brew --prefix python@3)/bin/python3"
            [[ -x "$BREW_PY" ]] && export PATH="$(brew --prefix python@3)/bin:$PATH"
        else
            die "Python 3.10+ is required. Install it and retry."
        fi
    else
        die "Python 3.10+ is required. Download from https://www.python.org/downloads/ and retry."
    fi
fi

# ── git ───────────────────────────────────────────────────────────────────────
if ! command -v git >/dev/null 2>&1; then
    if command -v brew >/dev/null 2>&1; then
        if ask_yn "git not found. Install via Homebrew?"; then
            brew install git
        else
            die "git is required. Install it and retry."
        fi
    else
        echo "git not found. You can install the Xcode Command Line Tools:"
        echo "  xcode-select --install"
        die "git is required. Install it and retry."
    fi
fi

# ── Pull submodules ───────────────────────────────────────────────────────────
echo ""
echo "Updating git submodules..."
git submodule update --init --recursive

# ── BetterWebUI virtualenv ────────────────────────────────────────────────────
if [ ! -d ".venv" ]; then
    echo "First-time setup: installing BetterWebUI Python packages..."
    python3 -m venv .venv
    ./.venv/bin/pip install --upgrade pip >/dev/null
    ./.venv/bin/pip install -r requirements.txt
fi

# ── Interactive setup wizard ───────────────────────────────────────────────────
# Validates deploy/.env, prompts for anything missing or broken, then saves.
python3 scripts/setup_wizard.py || exit 1

# Re-source deploy/.env so updated values take effect immediately.
if [[ -f "$ENV_FILE" ]]; then
    set -o allexport; source "$ENV_FILE"; set +o allexport
fi

# Apply port defaults (wizard may have written explicit values; these are fallbacks).
CLK_PORT="${CLK_PORT:-8001}"
AUTOGUI_PORT="${AUTOGUI_PORT:-8002}"
OSSO_PORT="${OSSO_PORT:-5001}"
PORT="${PORT:-8765}"

# Derive service base-URLs for BetterWebUI (reads these from its own environment).
export CLK_BASE_URL="${CLK_BASE_URL:-http://localhost:$CLK_PORT}"
export AUTOGUI_BASE_URL="${AUTOGUI_BASE_URL:-http://localhost:$AUTOGUI_PORT}"
export OSSO_BASE_URL="${OSSO_BASE_URL:-http://localhost:$OSSO_PORT}"

# Convenience aliases used by the service-launch blocks below.
OW_URL="$OPENWEBUI_BASE_URL"
OW_KEY="$OPENWEBUI_API_KEY"
OW_MODEL="${OPENWEBUI_MODEL:-}"
OW_PROVIDER="${LLM_PROVIDER:-openwebui}"

# ── CognitiveLoopKernel ───────────────────────────────────────────────────────
if is_up "http://localhost:$CLK_PORT/api/healthz"; then
    echo "CognitiveLoopKernel already running on port $CLK_PORT — skipping."
else
    echo "Starting CognitiveLoopKernel..."
    setup_venv "$CLK_DIR"
    (
        cd "$CLK_DIR"
        CLK_API_PORT=$CLK_PORT \
        CLK_WORKSPACES_DIR="${CLK_WORKSPACES_DIR:-./data/clk-workspaces}" \
        CLK_PROVIDER="$OW_PROVIDER" \
        CLK_OPENWEBUI_ENDPOINT="$OW_URL" \
        CLK_OPENWEBUI_API_KEY="$OW_KEY" \
        CLK_OPENWEBUI_MODEL="$OW_MODEL" \
        exec "$CLK_DIR/.venv/bin/python" -m clk_harness.api
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
        AUTOGUI_API_PORT=$AUTOGUI_PORT \
        OPENWEBUI_BASE_URL="$OW_URL" \
        OPENWEBUI_API_KEY="$OW_KEY" \
        OPENWEBUI_MODEL="$OW_MODEL" \
        exec "$AUTOGUI_DIR/.venv/bin/python" api.py
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
        CLK_PROVIDER="$OW_PROVIDER" \
        CLK_OPENWEBUI_ENDPOINT="$OW_URL" \
        CLK_OPENWEBUI_API_KEY="$OW_KEY" \
        CLK_OPENWEBUI_MODEL="$OW_MODEL" \
        exec "$OSSO_DIR/.venv/bin/python" main.py
    ) &
    STARTED_PIDS+=("$!")
fi

echo ""
echo "BetterWebUI is starting on http://127.0.0.1:${PORT}"
echo "Open that link in your browser. Press Ctrl-C in this window to stop."
echo ""

# Run as child process (not exec) so Ctrl-C returns to the shell.
./.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port "$PORT"
