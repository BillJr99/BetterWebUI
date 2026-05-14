"""
tests/test_services.py — Unit tests for the services/ module.

Mocks httpx calls using unittest.mock.patch so tests run fully offline.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(coro):
    """Run a coroutine synchronously."""
    return asyncio.run(coro)


def _mock_response(json_data: dict, status_code: int = 200):
    """Return a mock httpx.Response-like object."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    return resp


def _async_client_ctx(mock_response):
    """
    Return a context-manager mock whose async-with body gives back a client
    whose .get() / .post() return the supplied mock_response.
    """
    client_mock = AsyncMock()
    client_mock.get = AsyncMock(return_value=mock_response)
    client_mock.post = AsyncMock(return_value=mock_response)

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client_mock)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client_mock


# ===========================================================================
# Registry
# ===========================================================================

class TestRegistry:
    def test_get_services_returns_expected_keys(self):
        from services.registry import get_services
        svcs = get_services()
        assert set(svcs.keys()) == {"clk", "autogui", "osso"}

    def test_clk_endpoint_fields(self):
        from services.registry import get_services
        clk = get_services()["clk"]
        assert clk.name == "CognitiveLoopKernel"
        assert clk.health_path == "/api/healthz"
        assert clk.timeout == 300.0
        assert "localhost" in clk.base_url or "CLK_BASE_URL" in str(clk)

    def test_autogui_endpoint_fields(self):
        from services.registry import get_services
        ep = get_services()["autogui"]
        assert ep.name == "AutoGUI"
        assert ep.timeout == 300.0

    def test_osso_endpoint_fields(self):
        from services.registry import get_services
        ep = get_services()["osso"]
        assert ep.name == "OSScreenObserver"
        assert ep.timeout == 30.0

    def test_env_override_clk(self, monkeypatch):
        monkeypatch.setenv("CLK_BASE_URL", "http://my-clk:9999")
        # Re-import to pick up env change
        import importlib, services.registry as reg
        importlib.reload(reg)
        svcs = reg.get_services()
        assert svcs["clk"].base_url == "http://my-clk:9999"

    def test_env_override_autogui(self, monkeypatch):
        monkeypatch.setenv("AUTOGUI_BASE_URL", "http://my-autogui:7777")
        import importlib, services.registry as reg
        importlib.reload(reg)
        assert reg.get_services()["autogui"].base_url == "http://my-autogui:7777"

    def test_env_override_osso(self, monkeypatch):
        monkeypatch.setenv("OSSO_BASE_URL", "http://my-osso:6666")
        import importlib, services.registry as reg
        importlib.reload(reg)
        assert reg.get_services()["osso"].base_url == "http://my-osso:6666"


# ===========================================================================
# ServiceClient (base)
# ===========================================================================

class TestServiceClientHealth:
    def test_health_returns_json(self):
        from services.registry import get_services
        from services.clients import ServiceClient

        ep = get_services()["clk"]
        client = ServiceClient(ep)

        mock_resp = _mock_response({"status": "ok"})
        ctx, _ = _async_client_ctx(mock_resp)

        with patch.object(client, "_client", return_value=ctx):
            result = run(client.health())

        assert result == {"status": "ok"}

    def test_health_calls_health_path(self):
        from services.registry import get_services
        from services.clients import ServiceClient

        ep = get_services()["clk"]
        client = ServiceClient(ep)

        mock_resp = _mock_response({"status": "ok"})
        ctx, inner = _async_client_ctx(mock_resp)

        with patch.object(client, "_client", return_value=ctx):
            run(client.health())

        inner.get.assert_awaited_once_with("/api/healthz")


# ===========================================================================
# CLKClient
# ===========================================================================

class TestCLKClient:
    def _make_client(self, mock_resp):
        from services.clients import get_clk_client
        client = get_clk_client()
        ctx, inner = _async_client_ctx(mock_resp)
        return client, ctx, inner

    def test_list_workflows_calls_correct_path(self):
        mock_resp = _mock_response({"workflows": ["research", "summarize"]})
        client, ctx, inner = self._make_client(mock_resp)
        with patch.object(client, "_client", return_value=ctx):
            result = run(client.list_workflows())
        inner.get.assert_awaited_once_with("/api/workflows")
        assert result == {"workflows": ["research", "summarize"]}

    def test_start_research_posts_body(self):
        mock_resp = _mock_response({"task_id": "abc123"})
        client, ctx, inner = self._make_client(mock_resp)
        with patch.object(client, "_client", return_value=ctx):
            result = run(client.start_research("run", ["--query", "test"], workspace_id="ws1", workflow="research"))
        inner.post.assert_awaited_once_with("/api/research", json={
            "command": "run",
            "args": ["--query", "test"],
            "workspace_id": "ws1",
            "workflow": "research",
        })
        assert result["task_id"] == "abc123"

    def test_get_task_calls_correct_path(self):
        mock_resp = _mock_response({"status": "running"})
        client, ctx, inner = self._make_client(mock_resp)
        with patch.object(client, "_client", return_value=ctx):
            result = run(client.get_task("t-999"))
        inner.get.assert_awaited_once_with("/api/research/t-999")
        assert result == {"status": "running"}

    def test_list_artifacts_calls_correct_path(self):
        mock_resp = _mock_response({"artifacts": []})
        client, ctx, inner = self._make_client(mock_resp)
        with patch.object(client, "_client", return_value=ctx):
            result = run(client.list_artifacts("t-999"))
        inner.get.assert_awaited_once_with("/api/research/t-999/artifacts")

    def test_cancel_task_calls_correct_path(self):
        mock_resp = _mock_response({"cancelled": True})
        client, ctx, inner = self._make_client(mock_resp)
        with patch.object(client, "_client", return_value=ctx):
            result = run(client.cancel_task("t-999"))
        inner.post.assert_awaited_once_with("/api/research/t-999/cancel")
        assert result["cancelled"] is True


# ===========================================================================
# AutoGUIClient
# ===========================================================================

class TestAutoGUIClient:
    def _make_client(self, mock_resp):
        from services.clients import get_autogui_client
        client = get_autogui_client()
        ctx, inner = _async_client_ctx(mock_resp)
        return client, ctx, inner

    def test_list_tools_calls_correct_path(self):
        mock_resp = _mock_response({"tools": ["click", "type"]})
        client, ctx, inner = self._make_client(mock_resp)
        with patch.object(client, "_client", return_value=ctx):
            result = run(client.list_tools())
        inner.get.assert_awaited_once_with("/api/tools")
        assert result == {"tools": ["click", "type"]}

    def test_start_task_posts_body(self):
        mock_resp = _mock_response({"task_id": "gui-1"})
        client, ctx, inner = self._make_client(mock_resp)
        with patch.object(client, "_client", return_value=ctx):
            result = run(client.start_task("Open Notepad", model="gpt-4", allow={"apps": ["notepad"]}, dry_run=False))
        inner.post.assert_awaited_once_with("/api/task", json={
            "task": "Open Notepad",
            "model": "gpt-4",
            "allow": {"apps": ["notepad"]},
            "dry_run": False,
        })
        assert result["task_id"] == "gui-1"

    def test_start_task_defaults(self):
        mock_resp = _mock_response({"task_id": "gui-2"})
        client, ctx, inner = self._make_client(mock_resp)
        with patch.object(client, "_client", return_value=ctx):
            run(client.start_task("Close window"))
        _, kwargs = inner.post.await_args
        body = kwargs["json"]
        assert body["model"] is None
        assert body["allow"] is None
        assert body["dry_run"] is False

    def test_get_task_calls_correct_path(self):
        mock_resp = _mock_response({"status": "done"})
        client, ctx, inner = self._make_client(mock_resp)
        with patch.object(client, "_client", return_value=ctx):
            result = run(client.get_task("gui-1"))
        inner.get.assert_awaited_once_with("/api/task/gui-1")
        assert result["status"] == "done"

    def test_cancel_task_calls_correct_path(self):
        mock_resp = _mock_response({"cancelled": True})
        client, ctx, inner = self._make_client(mock_resp)
        with patch.object(client, "_client", return_value=ctx):
            result = run(client.cancel_task("gui-1"))
        inner.post.assert_awaited_once_with("/api/task/gui-1/cancel")
        assert result["cancelled"] is True


# ===========================================================================
# OSSOClient
# ===========================================================================

class TestOSSOClient:
    def _make_client(self, mock_resp):
        from services.clients import get_osso_client
        client = get_osso_client()
        ctx, inner = _async_client_ctx(mock_resp)
        return client, ctx, inner

    def test_windows_calls_correct_path(self):
        mock_resp = _mock_response({"windows": [{"index": 0, "title": "Desktop"}]})
        client, ctx, inner = self._make_client(mock_resp)
        with patch.object(client, "_client", return_value=ctx):
            result = run(client.windows())
        inner.get.assert_awaited_once_with("/api/windows")
        assert result["windows"][0]["title"] == "Desktop"

    def test_description_default_params(self):
        mock_resp = _mock_response({"description": "A blank desktop"})
        client, ctx, inner = self._make_client(mock_resp)
        with patch.object(client, "_client", return_value=ctx):
            result = run(client.description())
        inner.get.assert_awaited_once_with("/api/description", params={"mode": "accessibility"})

    def test_description_with_window_index(self):
        mock_resp = _mock_response({"description": "Notepad"})
        client, ctx, inner = self._make_client(mock_resp)
        with patch.object(client, "_client", return_value=ctx):
            run(client.description(window_index=2, mode="vision"))
        inner.get.assert_awaited_once_with("/api/description", params={"mode": "vision", "window_index": 2})

    def test_structure_no_window(self):
        mock_resp = _mock_response({"elements": []})
        client, ctx, inner = self._make_client(mock_resp)
        with patch.object(client, "_client", return_value=ctx):
            run(client.structure())
        inner.get.assert_awaited_once_with("/api/structure", params={})

    def test_structure_with_window(self):
        mock_resp = _mock_response({"elements": []})
        client, ctx, inner = self._make_client(mock_resp)
        with patch.object(client, "_client", return_value=ctx):
            run(client.structure(window_index=1))
        inner.get.assert_awaited_once_with("/api/structure", params={"window_index": 1})

    def test_screenshot_no_window(self):
        mock_resp = _mock_response({"image": "base64data"})
        client, ctx, inner = self._make_client(mock_resp)
        with patch.object(client, "_client", return_value=ctx):
            result = run(client.screenshot())
        inner.get.assert_awaited_once_with("/api/screenshot", params={})
        assert result["image"] == "base64data"

    def test_screenshot_with_window(self):
        mock_resp = _mock_response({"image": "base64data"})
        client, ctx, inner = self._make_client(mock_resp)
        with patch.object(client, "_client", return_value=ctx):
            run(client.screenshot(window_index=3))
        inner.get.assert_awaited_once_with("/api/screenshot", params={"window_index": 3})

    def test_action_posts_body(self):
        mock_resp = _mock_response({"ok": True})
        client, ctx, inner = self._make_client(mock_resp)
        action_data = {"action": "click", "x": 100, "y": 200}
        with patch.object(client, "_client", return_value=ctx):
            result = run(client.action(action_data))
        inner.post.assert_awaited_once_with("/api/action", json=action_data)
        assert result["ok"] is True

    def test_capabilities_calls_correct_path(self):
        mock_resp = _mock_response({"capabilities": ["screenshot", "click"]})
        client, ctx, inner = self._make_client(mock_resp)
        with patch.object(client, "_client", return_value=ctx):
            result = run(client.capabilities())
        inner.get.assert_awaited_once_with("/api/capabilities")
        assert "screenshot" in result["capabilities"]


# ===========================================================================
# Health check logic
# ===========================================================================

class TestHealthCheck:
    def test_all_services_up(self):
        healthy_response = {"status": "ok"}

        async def fake_health(self):
            return healthy_response

        with patch("services.clients.ServiceClient.health", new=fake_health):
            from services.health import check_all_services
            results = run(check_all_services())

        assert results["clk"]["ok"] is True
        assert results["autogui"]["ok"] is True
        assert results["osso"]["ok"] is True
        assert results["clk"]["detail"] == healthy_response

    def test_one_service_down(self):
        call_count = {"n": 0}

        async def fake_health(self):
            call_count["n"] += 1
            if call_count["n"] == 1:  # first call (clk) fails
                raise ConnectionRefusedError("connection refused")
            return {"status": "ok"}

        with patch("services.clients.ServiceClient.health", new=fake_health):
            from services.health import check_all_services
            import importlib
            import services.health as health_mod
            importlib.reload(health_mod)
            results = run(health_mod.check_all_services())

        # At least one service should be down
        statuses = [v["ok"] for v in results.values()]
        assert False in statuses

    def test_all_services_down(self):
        async def fake_health(self):
            raise ConnectionRefusedError("connection refused")

        with patch("services.clients.ServiceClient.health", new=fake_health):
            import importlib
            import services.health as health_mod
            importlib.reload(health_mod)
            results = run(health_mod.check_all_services())

        for name, val in results.items():
            assert val["ok"] is False, f"{name} should be down"
            assert "connection refused" in val["error"]

    def test_partial_service_down_error_message(self):
        """error field must contain the exception message when a service fails."""
        async def fake_health(self):
            raise RuntimeError("timeout talking to service")

        with patch("services.clients.ServiceClient.health", new=fake_health):
            import importlib
            import services.health as health_mod
            importlib.reload(health_mod)
            results = run(health_mod.check_all_services())

        for val in results.values():
            assert "timeout talking to service" in val["error"]


# ===========================================================================
# Route handlers (via FastAPI TestClient)
# ===========================================================================

class TestServicesRoutes:
    """
    Mount the services routes onto a minimal FastAPI app and exercise them
    with a synchronous TestClient, mocking out all real httpx calls.
    """

    @pytest.fixture()
    def app_client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from services.routes import register_routes

        app = FastAPI()
        register_routes(app)
        return TestClient(app, raise_server_exceptions=True)

    # -- Health route --

    def test_services_health_all_ok(self, app_client):
        healthy = {"clk": {"ok": True, "detail": {}}, "autogui": {"ok": True, "detail": {}}, "osso": {"ok": True, "detail": {}}}
        with patch("services.routes.check_all_services", new=AsyncMock(return_value=healthy)):
            r = app_client.get("/api/services/health")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert "clk" in data["services"]

    def test_services_health_one_down(self, app_client):
        mixed = {
            "clk": {"ok": False, "error": "refused"},
            "autogui": {"ok": True, "detail": {}},
            "osso": {"ok": True, "detail": {}},
        }
        with patch("services.routes.check_all_services", new=AsyncMock(return_value=mixed)):
            r = app_client.get("/api/services/health")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is False

    # -- CLK routes --

    def test_clk_list_workflows(self, app_client):
        workflows = {"workflows": ["research", "summarize"]}
        with patch("services.routes.get_clk_client") as mock_factory:
            mock_client = AsyncMock()
            mock_client.list_workflows.return_value = workflows
            mock_factory.return_value = mock_client
            r = app_client.get("/api/services/clk/workflows")
        assert r.status_code == 200
        assert r.json() == workflows

    def test_clk_start_research(self, app_client):
        task_resp = {"task_id": "t-abc"}
        with patch("services.routes.get_clk_client") as mock_factory:
            mock_client = AsyncMock()
            mock_client.start_research.return_value = task_resp
            mock_factory.return_value = mock_client
            r = app_client.post("/api/services/clk/research", json={
                "command": "run",
                "args": ["--query", "AI trends"],
                "workflow": "research",
            })
        assert r.status_code == 200
        assert r.json()["task_id"] == "t-abc"
        mock_client.start_research.assert_awaited_once_with(
            command="run",
            args=["--query", "AI trends"],
            workspace_id=None,
            workflow="research",
        )

    def test_clk_get_task(self, app_client):
        task_data = {"status": "running", "progress": 0.5}
        with patch("services.routes.get_clk_client") as mock_factory:
            mock_client = AsyncMock()
            mock_client.get_task.return_value = task_data
            mock_factory.return_value = mock_client
            r = app_client.get("/api/services/clk/research/t-abc")
        assert r.status_code == 200
        assert r.json() == task_data

    def test_clk_list_artifacts(self, app_client):
        artifacts = {"artifacts": [{"name": "report.md"}]}
        with patch("services.routes.get_clk_client") as mock_factory:
            mock_client = AsyncMock()
            mock_client.list_artifacts.return_value = artifacts
            mock_factory.return_value = mock_client
            r = app_client.get("/api/services/clk/research/t-abc/artifacts")
        assert r.status_code == 200
        assert r.json() == artifacts

    def test_clk_cancel_task(self, app_client):
        with patch("services.routes.get_clk_client") as mock_factory:
            mock_client = AsyncMock()
            mock_client.cancel_task.return_value = {"cancelled": True}
            mock_factory.return_value = mock_client
            r = app_client.post("/api/services/clk/research/t-abc/cancel")
        assert r.status_code == 200
        assert r.json()["cancelled"] is True

    # -- AutoGUI routes --

    def test_autogui_start_task(self, app_client):
        with patch("services.routes.get_autogui_client") as mock_factory:
            mock_client = AsyncMock()
            mock_client.start_task.return_value = {"task_id": "gui-1"}
            mock_factory.return_value = mock_client
            r = app_client.post("/api/services/autogui/task", json={"task": "Open Notepad"})
        assert r.status_code == 200
        assert r.json()["task_id"] == "gui-1"

    def test_autogui_get_task(self, app_client):
        with patch("services.routes.get_autogui_client") as mock_factory:
            mock_client = AsyncMock()
            mock_client.get_task.return_value = {"status": "done"}
            mock_factory.return_value = mock_client
            r = app_client.get("/api/services/autogui/task/gui-1")
        assert r.status_code == 200
        assert r.json()["status"] == "done"

    def test_autogui_cancel_task(self, app_client):
        with patch("services.routes.get_autogui_client") as mock_factory:
            mock_client = AsyncMock()
            mock_client.cancel_task.return_value = {"cancelled": True}
            mock_factory.return_value = mock_client
            r = app_client.post("/api/services/autogui/task/gui-1/cancel")
        assert r.status_code == 200

    def test_autogui_list_tools(self, app_client):
        with patch("services.routes.get_autogui_client") as mock_factory:
            mock_client = AsyncMock()
            mock_client.list_tools.return_value = {"tools": ["click", "type"]}
            mock_factory.return_value = mock_client
            r = app_client.get("/api/services/autogui/tools")
        assert r.status_code == 200
        assert "tools" in r.json()

    # -- OSSO routes --

    def test_osso_windows(self, app_client):
        windows_data = {"windows": [{"index": 0, "title": "Desktop"}]}
        with patch("services.routes.get_osso_client") as mock_factory:
            mock_client = AsyncMock()
            mock_client.windows.return_value = windows_data
            mock_factory.return_value = mock_client
            r = app_client.get("/api/services/osso/windows")
        assert r.status_code == 200
        assert r.json() == windows_data

    def test_osso_description_default_mode(self, app_client):
        desc_data = {"description": "A desktop"}
        with patch("services.routes.get_osso_client") as mock_factory:
            mock_client = AsyncMock()
            mock_client.description.return_value = desc_data
            mock_factory.return_value = mock_client
            r = app_client.get("/api/services/osso/description")
        assert r.status_code == 200
        mock_client.description.assert_awaited_once_with(None, "accessibility")

    def test_osso_description_with_params(self, app_client):
        with patch("services.routes.get_osso_client") as mock_factory:
            mock_client = AsyncMock()
            mock_client.description.return_value = {"description": "Notepad"}
            mock_factory.return_value = mock_client
            r = app_client.get("/api/services/osso/description?window_index=1&mode=vision")
        assert r.status_code == 200
        mock_client.description.assert_awaited_once_with(1, "vision")

    def test_osso_structure(self, app_client):
        with patch("services.routes.get_osso_client") as mock_factory:
            mock_client = AsyncMock()
            mock_client.structure.return_value = {"elements": []}
            mock_factory.return_value = mock_client
            r = app_client.get("/api/services/osso/structure")
        assert r.status_code == 200

    def test_osso_screenshot(self, app_client):
        with patch("services.routes.get_osso_client") as mock_factory:
            mock_client = AsyncMock()
            mock_client.screenshot.return_value = {"image": "base64data"}
            mock_factory.return_value = mock_client
            r = app_client.get("/api/services/osso/screenshot")
        assert r.status_code == 200
        assert r.json()["image"] == "base64data"

    def test_osso_action(self, app_client):
        with patch("services.routes.get_osso_client") as mock_factory:
            mock_client = AsyncMock()
            mock_client.action.return_value = {"ok": True}
            mock_factory.return_value = mock_client
            r = app_client.post("/api/services/osso/action", json={"action": "click", "x": 10, "y": 20})
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_osso_capabilities(self, app_client):
        with patch("services.routes.get_osso_client") as mock_factory:
            mock_client = AsyncMock()
            mock_client.capabilities.return_value = {"capabilities": ["screenshot"]}
            mock_factory.return_value = mock_client
            r = app_client.get("/api/services/osso/capabilities")
        assert r.status_code == 200

    # -- Tool specs route --

    def test_services_tools_returns_tool_specs(self, app_client):
        r = app_client.get("/api/services/tools")
        assert r.status_code == 200
        data = r.json()
        assert "tools" in data
        tool_names = [t["function"]["name"] for t in data["tools"]]
        assert "clk_research" in tool_names
        assert "autogui_task" in tool_names
        assert "screen_windows" in tool_names
        assert "screen_description" in tool_names
        assert "screen_screenshot" in tool_names
        assert "screen_action" in tool_names

    def test_services_tools_all_have_type_function(self, app_client):
        r = app_client.get("/api/services/tools")
        for tool in r.json()["tools"]:
            assert tool["type"] == "function"
            assert "function" in tool
            assert "name" in tool["function"]
            assert "description" in tool["function"]
