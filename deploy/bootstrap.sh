#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
WORKSPACE_DIR="$(dirname "$ROOT_DIR")"

echo "Cloning sibling repositories..."

# Load refs from .env if present
if [ -f "$SCRIPT_DIR/.env" ]; then
    source "$SCRIPT_DIR/.env"
fi

clone_or_update() {
    local name=$1; local url=$2; local ref=${3:-main}
    local target="$WORKSPACE_DIR/$name"
    if [ -d "$target/.git" ]; then
        echo "  $name: updating..."
        git -C "$target" fetch origin
        git -C "$target" checkout "$ref" 2>/dev/null || git -C "$target" checkout -b "$ref" "origin/$ref"
    else
        echo "  $name: cloning..."
        git clone "$url" "$target"
        git -C "$target" checkout "$ref" 2>/dev/null || true
    fi
}

clone_or_update "cognitiveloopkernel" "git@github.com:billjr99/cognitiveloopkernel.git" "${CLK_REF:-main}"
clone_or_update "autogui" "git@github.com:billjr99/autogui.git" "${AUTOGUI_REF:-main}"
clone_or_update "osscreenobserver" "git@github.com:billjr99/osscreenobserver.git" "${OSSO_REF:-main}"

# ── Configure OpenWebUI URL / API key / model via the shared setup wizard ─────
# Pass --no-wizard to skip and fall back to the manual .env.example workflow.
if [[ "${1:-}" != "--no-wizard" ]] && command -v python3 >/dev/null 2>&1; then
    echo ""
    echo "Launching setup wizard to configure OpenWebUI..."
    if ! python3 "$ROOT_DIR/scripts/setup_wizard.py" --env-file "$SCRIPT_DIR/.env"; then
        echo ""
        echo "Setup wizard was cancelled or failed."
        echo "You can re-run it later with:"
        echo "  python3 $ROOT_DIR/scripts/setup_wizard.py --env-file $SCRIPT_DIR/.env"
        echo "Or copy deploy/.env.example to deploy/.env and edit it manually."
        exit 1
    fi
    echo ""
    echo "Done. Sibling repos are in: $WORKSPACE_DIR"
    echo "Next:"
    echo "  docker compose -f deploy/docker-compose.integration.yml up"
else
    echo ""
    echo "Done. Sibling repos are in: $WORKSPACE_DIR"
    echo "Next: copy deploy/.env.example to deploy/.env and edit it, then:"
    echo "  docker compose -f deploy/docker-compose.integration.yml up"
fi
