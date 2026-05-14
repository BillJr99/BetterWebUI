# Integration Deployment Guide

This directory contains everything needed to run BetterWebUI together with
CognitiveLoopKernel (CLK), AutoGUI, and OSScreenObserver.

## Prerequisites

- **Docker** and **Docker Compose** v2+ (`docker compose` command)
- **Node.js 18+** and **npm** — for running the Playwright test suite
- **Git** — for `bootstrap.sh` to clone sibling repos

## Quick start

### 1. Bootstrap sibling repositories

```bash
bash deploy/bootstrap.sh
```

This clones (or updates) `cognitiveloopkernel`, `autogui`, and `osscreenobserver`
as siblings of the `betterwebui` directory, e.g.:

```
workspace/
├── betterwebui/
├── cognitiveloopkernel/
├── autogui/
└── osscreenobserver/
```

### 2. Configure environment

```bash
cp deploy/.env.example deploy/.env
# edit deploy/.env — set your OpenWebUI URL, API key, and any ref pins
```

### 3. Start the stack

```bash
# Default (BetterWebUI + CLK only; AutoGUI and OSScreenObserver on host):
docker compose -f deploy/docker-compose.integration.yml up

# With containerised test stubs for AutoGUI and OSScreenObserver:
docker compose -f deploy/docker-compose.integration.yml --profile test up
```

BetterWebUI will be available at <http://localhost:8080>.

## Running with host services

AutoGUI and OSScreenObserver often need access to the host desktop (screen,
accessibility APIs). Run them directly on the host:

```bash
# In the autogui directory:
python api.py

# In the osscreenobserver directory:
python main.py --mode inspect
```

The default `AUTOGUI_BASE_URL` and `OSSO_BASE_URL` in `.env.example` point to
`host.docker.internal`, so the container can reach host processes automatically.

## Docker Compose reference

| Service | Profile | Default port | Notes |
|---|---|---|---|
| `betterwebui` | (always) | 8080 | Proxies to all three services |
| `clk` | (always) | 8001 | Runs containerised |
| `autogui-test` | `test` | 8002 | Dry-run mode; for CI/testing only |
| `osso-test` | `test` | 5001 | Mock mode; for CI/testing only |

## Running Playwright integration tests

```bash
cd tests/playwright
npm install
npx playwright install --with-deps

# Bring up the test stack first:
docker compose -f ../../deploy/docker-compose.integration.yml --profile test up -d

# Run tests:
npm test

# Or let globalSetup/Teardown manage the stack automatically:
npm test  # globalSetup starts the stack; globalTeardown stops it
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `CLK_BASE_URL` | `http://clk:8001` | CLK service URL (inside Docker) |
| `AUTOGUI_BASE_URL` | `http://host.docker.internal:8002` | AutoGUI URL |
| `OSSO_BASE_URL` | `http://host.docker.internal:5001` | OSScreenObserver URL |
| `OPENWEBUI_BASE_URL` | — | Your OpenWebUI instance |
| `OPENWEBUI_API_KEY` | — | OpenWebUI API key |
| `CLK_WORKSPACES_DIR` | `./data/clk-workspaces` | CLK workspace mount |
| `CLK_REF` | `main` | Git ref for cognitiveloopkernel |
| `AUTOGUI_REF` | `main` | Git ref for autogui |
| `OSSO_REF` | `main` | Git ref for osscreenobserver |
