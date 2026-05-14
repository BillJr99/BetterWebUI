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

clone_or_update "cognitiveloopkernel" "https://github.com/billjr99/cognitiveloopkernel.git" "${CLK_REF:-main}"
clone_or_update "autogui" "https://github.com/billjr99/autogui.git" "${AUTOGUI_REF:-main}"
clone_or_update "osscreenobserver" "https://github.com/billjr99/osscreenobserver.git" "${OSSO_REF:-main}"

echo ""
echo "Done. Sibling repos are in: $WORKSPACE_DIR"
echo "Next: copy deploy/.env.example to deploy/.env and edit it, then:"
echo "  docker compose -f deploy/docker-compose.integration.yml up"
