"""
services/routes.py — FastAPI route handlers for CLK, AutoGUI, and OSScreenObserver.

Call register_routes(app) once during app startup (or at module import time)
to mount all /api/services/* endpoints onto the existing FastAPI app.
"""
from __future__ import annotations

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from .clients import get_clk_client, get_autogui_client, get_osso_client
from .health import check_all_services
from .sse_proxy import proxy_sse
from . import state as svc_state

_VALID_SERVICE_NAMES = frozenset({"clk", "autogui", "osso"})

_SERVICE_LABELS = {"clk": "CognitiveLoopKernel", "autogui": "AutoGUI", "osso": "OSScreenObserver"}


def _require_enabled(name: str) -> None:
    """Raise 503 if the named service is currently disabled."""
    if not svc_state.is_enabled(name):
        raise HTTPException(status_code=503, detail=f"Service '{name}' is disabled")


def _unreachable(name: str, exc: Exception) -> HTTPException:
    label = _SERVICE_LABELS.get(name, name)
    return HTTPException(
        status_code=503,
        detail=f"{label} is enabled but could not be reached. Check that it is running. ({exc})",
    )


def register_routes(app: FastAPI) -> None:  # noqa: C901
    """Register all /api/services/* routes on the given FastAPI app instance."""

    # ── Health ───────────────────────────────────────────────────────────────────────

    @app.get("/api/services/health")
    async def services_health():
        results = await check_all_services()
        all_ok = all(v["ok"] for v in results.values())
        return {"ok": all_ok, "services": results}

    # ── Enable / Disable ───────────────────────────────────────────────────────────

    @app.get("/api/services/status")
    async def services_status():
        """Return the enabled/disabled state for each service."""
        enabled_map = svc_state.get_all()
        return {"services": {name: {"enabled": enabled} for name, enabled in enabled_map.items()}}

    @app.post("/api/services/{name}/enable")
    async def enable_service(name: str):
        if name not in _VALID_SERVICE_NAMES:
            raise HTTPException(status_code=404, detail=f"Unknown service: {name}")
        svc_state.set_enabled(name, True)
        return {"ok": True, "service": name, "enabled": True}

    @app.post("/api/services/{name}/disable")
    async def disable_service(name: str):
        if name not in _VALID_SERVICE_NAMES:
            raise HTTPException(status_code=404, detail=f"Unknown service: {name}")
        svc_state.set_enabled(name, False)
        return {"ok": True, "service": name, "enabled": False}

    # ── CognitiveLoopKernel ─────────────────────────────────────────────────────────

    @app.get("/api/services/clk/workflows")
    async def clk_list_workflows():
        _require_enabled("clk")
        client = get_clk_client()
        try:
            return await client.list_workflows()
        except (httpx.ConnectError, httpx.TimeoutException, httpx.TransportError) as e:
            raise _unreachable("clk", e) from e

    @app.post("/api/services/clk/research")
    async def clk_start_research(body: dict):
        _require_enabled("clk")
        client = get_clk_client()
        try:
            return await client.start_research(
                command=body.get("command", "run"),
                args=body.get("args", []),
                workspace_id=body.get("workspace_id"),
                workflow=body.get("workflow"),
            )
        except (httpx.ConnectError, httpx.TimeoutException, httpx.TransportError) as e:
            raise _unreachable("clk", e) from e

    @app.get("/api/services/clk/research/{task_id}")
    async def clk_get_task(task_id: str):
        _require_enabled("clk")
        client = get_clk_client()
        try:
            return await client.get_task(task_id)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.TransportError) as e:
            raise _unreachable("clk", e) from e

    @app.get("/api/services/clk/research/{task_id}/stream")
    async def clk_stream_task(task_id: str):
        _require_enabled("clk")
        client = get_clk_client()
        return StreamingResponse(
            proxy_sse(client.stream_task(task_id)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/services/clk/research/{task_id}/artifacts")
    async def clk_list_artifacts(task_id: str):
        _require_enabled("clk")
        client = get_clk_client()
        try:
            return await client.list_artifacts(task_id)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.TransportError) as e:
            raise _unreachable("clk", e) from e

    @app.post("/api/services/clk/research/{task_id}/cancel")
    async def clk_cancel_task(task_id: str):
        _require_enabled("clk")
        client = get_clk_client()
        try:
            return await client.cancel_task(task_id)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.TransportError) as e:
            raise _unreachable("clk", e) from e

    # ── AutoGUI ───────────────────────────────────────────────────────────────────

    @app.post("/api/services/autogui/task")
    async def autogui_start_task(body: dict):
        _require_enabled("autogui")
        client = get_autogui_client()
        try:
            return await client.start_task(
                task=body["task"],
                model=body.get("model"),
                allow=body.get("allow"),
                dry_run=body.get("dry_run", False),
            )
        except (httpx.ConnectError, httpx.TimeoutException, httpx.TransportError) as e:
            raise _unreachable("autogui", e) from e

    @app.get("/api/services/autogui/task/{task_id}")
    async def autogui_get_task(task_id: str):
        _require_enabled("autogui")
        client = get_autogui_client()
        try:
            return await client.get_task(task_id)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.TransportError) as e:
            raise _unreachable("autogui", e) from e

    @app.get("/api/services/autogui/task/{task_id}/stream")
    async def autogui_stream_task(task_id: str):
        _require_enabled("autogui")
        client = get_autogui_client()
        return StreamingResponse(
            proxy_sse(client.stream_task(task_id)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/services/autogui/task/{task_id}/cancel")
    async def autogui_cancel_task(task_id: str):
        _require_enabled("autogui")
        client = get_autogui_client()
        try:
            return await client.cancel_task(task_id)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.TransportError) as e:
            raise _unreachable("autogui", e) from e

    @app.get("/api/services/autogui/tools")
    async def autogui_list_tools():
        _require_enabled("autogui")
        client = get_autogui_client()
        try:
            return await client.list_tools()
        except (httpx.ConnectError, httpx.TimeoutException, httpx.TransportError) as e:
            raise _unreachable("autogui", e) from e

    # ── OSScreenObserver ────────────────────────────────────────────────────────────────

    @app.get("/api/services/osso/windows")
    async def osso_windows():
        _require_enabled("osso")
        client = get_osso_client()
        try:
            return await client.windows()
        except (httpx.ConnectError, httpx.TimeoutException, httpx.TransportError) as e:
            raise _unreachable("osso", e) from e

    @app.get("/api/services/osso/description")
    async def osso_description(window_index: int | None = None, mode: str = "accessibility"):
        _require_enabled("osso")
        client = get_osso_client()
        try:
            return await client.description(window_index, mode)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.TransportError) as e:
            raise _unreachable("osso", e) from e

    @app.get("/api/services/osso/structure")
    async def osso_structure(window_index: int | None = None):
        _require_enabled("osso")
        client = get_osso_client()
        try:
            return await client.structure(window_index)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.TransportError) as e:
            raise _unreachable("osso", e) from e

    @app.get("/api/services/osso/screenshot")
    async def osso_screenshot(window_index: int | None = None):
        _require_enabled("osso")
        client = get_osso_client()
        try:
            return await client.screenshot(window_index)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.TransportError) as e:
            raise _unreachable("osso", e) from e

    @app.post("/api/services/osso/action")
    async def osso_action(body: dict):
        _require_enabled("osso")
        client = get_osso_client()
        try:
            return await client.action(body)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.TransportError) as e:
            raise _unreachable("osso", e) from e

    @app.get("/api/services/osso/capabilities")
    async def osso_capabilities():
        _require_enabled("osso")
        client = get_osso_client()
        try:
            return await client.capabilities()
        except (httpx.ConnectError, httpx.TimeoutException, httpx.TransportError) as e:
            raise _unreachable("osso", e) from e

    # ── LLM Tool Specs ────────────────────────────────────────────────────────────────

    @app.get("/api/services/tools")
    async def services_tool_specs():
        """Return OpenAI function-calling tool specs for all integrated services."""
        return {
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "clk_research",
                        "description": "Start a CognitiveLoopKernel research task. Use for deep research, multi-step analysis, or tasks requiring a workflow.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "command": {"type": "string", "enum": ["run"], "description": "CLK command to execute"},
                                "args": {"type": "array", "items": {"type": "string"}, "description": "Additional CLI args for CLK"},
                                "workflow": {"type": "string", "description": "Workflow name to run (e.g. 'research', 'summarize')"},
                                "workspace_id": {"type": "string", "description": "Optional CLK workspace ID"},
                            },
                            "required": ["command"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "autogui_task",
                        "description": "Instruct AutoGUI to perform a desktop GUI automation task using a ReAct loop.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "task": {"type": "string", "description": "Natural-language description of the desktop task"},
                                "dry_run": {"type": "boolean", "description": "If true, plan the task but do not execute actions"},
                                "model": {"type": "string", "description": "Override the LLM model used by AutoGUI"},
                            },
                            "required": ["task"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "screen_windows",
                        "description": "List all currently open windows on the screen via OSScreenObserver.",
                        "parameters": {"type": "object", "properties": {}, "required": []},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "screen_description",
                        "description": "Get a natural-language description of the screen or a specific window.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "window_index": {"type": "integer", "description": "Index of the window to describe (omit for frontmost)"},
                                "mode": {"type": "string", "enum": ["accessibility", "vision"], "description": "Description mode"},
                            },
                            "required": [],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "screen_screenshot",
                        "description": "Capture a screenshot of the screen or a specific window.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "window_index": {"type": "integer", "description": "Window index to capture (omit for full screen)"},
                            },
                            "required": [],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "screen_action",
                        "description": "Perform a screen action (click, type, key press) via OSScreenObserver.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "action": {"type": "string", "enum": ["click", "double_click", "right_click", "type", "key", "scroll"], "description": "Action type"},
                                "x": {"type": "number", "description": "Screen X coordinate (for click/scroll)"},
                                "y": {"type": "number", "description": "Screen Y coordinate (for click/scroll)"},
                                "text": {"type": "string", "description": "Text to type or key name to press"},
                            },
                            "required": ["action"],
                        },
                    },
                },
            ]
        }
