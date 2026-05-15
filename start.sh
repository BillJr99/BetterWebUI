#!/usr/bin/env bash
# BetterWebUI launcher for macOS / Linux.
# First run installs Python deps in a local virtualenv. After that it just starts.

set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "First-time setup: creating a Python environment and installing packages..."
  python3 -m venv .venv
  ./.venv/bin/pip install --upgrade pip >/dev/null
  ./.venv/bin/pip install -r requirements.txt
fi

PORT="${PORT:-8765}"
echo ""
echo "BetterWebUI is starting on http://127.0.0.1:${PORT}"
echo "Open that link in your browser. Press Ctrl-C in this window to stop."
echo ""

# Run uvicorn as a child process (not exec) so Ctrl-C stops uvicorn
# and drops back to the shell rather than closing the terminal.
./.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port "$PORT"
