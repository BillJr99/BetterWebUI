#!/usr/bin/env python3
"""
mock-server.py — Combined mock server for local UI test runs.

Starts four FastAPI apps on different ports to simulate:
  - OpenWebUI  (port 11000)  — model list + streaming chat
  - CLK        (port 8001)   — workflow + research endpoints
  - AutoGUI    (port 8002)   — task endpoints
  - OSSO       (port 5001)   — screen observation endpoints

Usage:
  python3 scripts/mock-server.py
  # Then run BetterWebUI and Playwright against these mocks.
"""

import asyncio
import json
import threading
import time
import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

# ─── OpenWebUI mock (port 11000) ─────────────────────────────────────────────

ow = FastAPI(title="mock-openwebui")

_MODELS = [{"id": "mock-model", "name": "Mock Model"}]

@ow.get("/api/v1/models")
async def ow_models():
    return {"data": _MODELS}

@ow.get("/api/models")
async def ow_models_legacy():
    return {"data": _MODELS}

@ow.get("/openai/v1/models")
async def ow_models_openai():
    return {"data": _MODELS}

@ow.get("/v1/models")
async def ow_models_v1():
    return {"data": _MODELS}

@ow.get("/health")
async def ow_health():
    return {"status": True}

@ow.get("/api/health")
async def ow_health2():
    return {"status": True}

async def _fake_sse_chat():
    words = ["Hello", "!", " ", "I", " ", "am", " ", "a", " ", "mock", " ", "model", "."]
    yield "data: " + json.dumps({"choices": [{"delta": {"role": "assistant", "content": ""}}]}) + "\n\n"
    for w in words:
        await asyncio.sleep(0.05)
        chunk = {
            "id": "mock-id",
            "object": "chat.completion.chunk",
            "choices": [{"delta": {"content": w}, "finish_reason": None}]
        }
        yield "data: " + json.dumps(chunk) + "\n\n"
    yield "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}) + "\n\n"
    yield "data: [DONE]\n\n"

@ow.post("/api/chat/completions")
async def ow_chat(body: dict):
    if body.get("stream", True):
        return StreamingResponse(_fake_sse_chat(), media_type="text/event-stream")
    return {
        "id": "mock-id",
        "choices": [{"message": {"role": "assistant", "content": "Hello! I am a mock model."}, "finish_reason": "stop"}]
    }

@ow.post("/openai/v1/chat/completions")
async def ow_chat_openai(body: dict):
    return await ow_chat(body)

@ow.post("/v1/chat/completions")
async def ow_chat_v1(body: dict):
    return await ow_chat(body)

@ow.post("/api/v1/auths/signup")
async def ow_signup(body: dict):
    return {"id": "mock-user", "email": body.get("email", ""), "name": body.get("name", ""), "role": "admin", "token": "mock-jwt-token"}

@ow.post("/api/v1/auths/signin")
async def ow_signin(body: dict):
    return {"id": "mock-user", "email": body.get("email", ""), "name": "CI", "role": "admin", "token": "mock-jwt-token"}

@ow.post("/api/v1/auths/api_key")
async def ow_api_key():
    return {"api_key": "mock-api-key-1234"}

# ─── CLK mock (port 8001) ────────────────────────────────────────────────────

clk = FastAPI(title="mock-clk")

@clk.get("/api/healthz")
async def clk_health():
    return {"ok": True, "service": "CognitiveLoopKernel"}

@clk.get("/api/workflows")
async def clk_workflows():
    return {"ok": True, "workflows": [{"id": "research", "name": "Research Workflow"}]}

@clk.post("/api/research")
async def clk_start(body: dict):
    return {"ok": True, "task_id": "mock-task-1", "status": "queued"}

@clk.get("/api/research/{task_id}")
async def clk_get(task_id: str):
    return {"ok": True, "task_id": task_id, "status": "done", "result": "Mock research result."}

@clk.get("/api/research/{task_id}/artifacts")
async def clk_artifacts(task_id: str):
    return {"artifacts": []}

@clk.post("/api/research/{task_id}/cancel")
async def clk_cancel(task_id: str):
    return {"ok": True, "task_id": task_id, "status": "cancelled"}

async def _fake_clk_sse(task_id: str):
    yield "data: " + json.dumps({"kind": "text", "content": "Researching..."}) + "\n\n"
    await asyncio.sleep(0.1)
    yield "data: " + json.dumps({"kind": "done", "data": {"finish_reason": "done"}, "_done": True}) + "\n\n"

@clk.get("/api/research/{task_id}/stream")
async def clk_stream(task_id: str):
    return StreamingResponse(_fake_clk_sse(task_id), media_type="text/event-stream")

# ─── AutoGUI mock (port 8002) ────────────────────────────────────────────────

ag = FastAPI(title="mock-autogui")

@ag.get("/api/healthz")
async def ag_health():
    return {"ok": True, "service": "AutoGUI"}

@ag.get("/api/tools")
async def ag_tools():
    return {"tools": [{"name": "click", "description": "Click at coordinates"}]}

@ag.post("/api/task")
async def ag_start(body: dict):
    return {"ok": True, "task_id": "mock-ag-task-1"}

@ag.get("/api/task/{task_id}")
async def ag_get(task_id: str):
    return {"ok": True, "task_id": task_id, "status": "done", "summary": "Mock task done."}

@ag.post("/api/task/{task_id}/cancel")
async def ag_cancel(task_id: str):
    return {"ok": True, "task_id": task_id, "status": "cancelled"}

async def _fake_ag_sse(task_id: str):
    yield "data: " + json.dumps({"kind": "plan", "content": "Mock plan: take screenshot"}) + "\n\n"
    await asyncio.sleep(0.05)
    yield "data: " + json.dumps({"kind": "text", "content": "Automating..."}) + "\n\n"
    await asyncio.sleep(0.05)
    yield "data: " + json.dumps({"kind": "done", "finished": True}) + "\n\n"

@ag.get("/api/task/{task_id}/stream")
async def ag_stream(task_id: str):
    return StreamingResponse(_fake_ag_sse(task_id), media_type="text/event-stream")

# ─── OSScreenObserver mock (port 5001) ───────────────────────────────────────

osso = FastAPI(title="mock-osso")

@osso.get("/api/healthz")
async def osso_health():
    return {"ok": True, "service": "OSScreenObserver"}

@osso.get("/api/windows")
async def osso_windows():
    return {"count": 1, "windows": [{"index": 0, "title": "Mock Window", "app": "MockApp", "pid": 12345}]}

@osso.get("/api/description")
async def osso_description(window_index: int = None, mode: str = "accessibility"):
    return {
        "mode": mode,
        "description": "A mock screen showing a desktop with some windows open.",
        "window_index": window_index,
    }

@osso.get("/api/structure")
async def osso_structure(window_index: int = None):
    return {
        "structure": {"type": "window", "title": "Mock Window", "children": []},
        "window_index": window_index,
    }

@osso.get("/api/screenshot")
async def osso_screenshot(window_index: int = None):
    return {"ok": True, "format": "png", "data": "", "window_index": window_index}

@osso.post("/api/action")
async def osso_action(body: dict):
    return {"ok": True, "action": body.get("action"), "result": "Action completed (mock)."}

@osso.get("/api/capabilities")
async def osso_capabilities():
    return {"ok": True, "capabilities": ["windows", "description", "structure", "screenshot", "action"]}

# ─── Runner ──────────────────────────────────────────────────────────────────

def _run(app, port):
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="error")

if __name__ == "__main__":
    print("Starting mock services:")
    print("  OpenWebUI  → http://localhost:11000")
    print("  CLK        → http://localhost:8001")
    print("  AutoGUI    → http://localhost:8002")
    print("  OSSO       → http://localhost:5001")

    threads = [
        threading.Thread(target=_run, args=(ow,   11000), daemon=True),
        threading.Thread(target=_run, args=(clk,   8001), daemon=True),
        threading.Thread(target=_run, args=(ag,    8002), daemon=True),
        threading.Thread(target=_run, args=(osso,  5001), daemon=True),
    ]
    for t in threads:
        t.start()

    # Wait for all servers to come up
    import urllib.request, urllib.error
    for name, url in [
        ("OpenWebUI",  "http://localhost:11000/health"),
        ("CLK",        "http://localhost:8001/api/healthz"),
        ("AutoGUI",    "http://localhost:8002/api/healthz"),
        ("OSSO",       "http://localhost:5001/api/healthz"),
    ]:
        for _ in range(20):
            try:
                urllib.request.urlopen(url, timeout=1)
                print(f"  ✓ {name} ready")
                break
            except Exception:
                time.sleep(0.5)
        else:
            print(f"  ✗ {name} failed to start")

    print("\nAll mock services running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopped.")
