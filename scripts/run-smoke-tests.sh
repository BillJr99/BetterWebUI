#!/usr/bin/env bash
# run-smoke-tests.sh — Curl-based smoke tests extracted from
# .github/workflows/ci.yml so they can be invoked from both CI and the unified
# run-all-tests.sh runner.
#
# Usage:
#   ./scripts/run-smoke-tests.sh                 # against http://127.0.0.1:8765
#   BWUI_URL=http://localhost:8080 ./scripts/run-smoke-tests.sh

set -euo pipefail
BASE="${BWUI_URL:-http://127.0.0.1:8765}"

ok()   { echo "  ✓ $*"; }
fail() { echo "  ✗ $*" >&2; exit 1; }

curl_ok() {
    local path="$1"
    curl -sf "$BASE$path" >/dev/null || fail "GET $path"
    ok "GET $path"
}

curl_status() {
    local path="$1" expected="$2"
    local got
    got=$(curl -so /dev/null -w "%{http_code}" "$BASE$path")
    [[ "$got" == "$expected" ]] || fail "GET $path: expected $expected, got $got"
    ok "GET $path → $got"
}

echo "Smoke tests against $BASE"

# Static
curl_status "/"                            200
curl_status "/static/app.js"               200
curl_status "/static/style.css"            200

# Read-only API
curl_ok "/api/health"
curl_ok "/api/config"
curl_ok "/api/skills"
curl_ok "/api/workspaces"
curl_ok "/api/onboarding/templates"
curl_ok "/api/lint"
curl_ok "/api/branding"
curl_ok "/api/conversations"
curl_ok "/api/conversations/search?q=test"
curl_ok "/api/session/trust"
curl_ok "/api/mcp/registry"
curl_ok "/api/cli/registry"
curl_ok "/api/system-prompts"

# Skill CRUD round-trip
curl -sf -X POST "$BASE/api/skills" \
    -H "Content-Type: application/json" \
    -d '{"id":"smoke-skill","name":"Smoke","description":"smoke test","content":"Do smoke things."}' \
    >/dev/null || fail "POST /api/skills"
ok "POST /api/skills"
curl_ok "/api/skills/smoke-skill"
curl -sf -X DELETE "$BASE/api/skills/smoke-skill" >/dev/null || fail "DELETE /api/skills/smoke-skill"
ok "DELETE /api/skills/smoke-skill"

# Workspace CRUD round-trip
WID=$(curl -sf -X POST "$BASE/api/workspaces" \
    -H "Content-Type: application/json" \
    -d '{"name":"Smoke WS","description":"smoke"}' \
  | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")
ok "POST /api/workspaces ($WID)"
curl_ok "/api/workspaces/$WID"
curl -sf -X DELETE "$BASE/api/workspaces/$WID" >/dev/null || fail "DELETE /api/workspaces/$WID"
ok "DELETE /api/workspaces/$WID"

echo ""
echo "All smoke tests passed."
