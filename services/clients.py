import json
import logging

import httpx
from .registry import get_services, ServiceEndpoint

logger = logging.getLogger("betterwebui.services.clients")


class ServiceClient:
    def __init__(self, endpoint: ServiceEndpoint):
        self._ep = endpoint

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self._ep.base_url, timeout=self._ep.timeout)

    async def health(self) -> dict:
        async with self._client() as c:
            r = await c.get(self._ep.health_path)
            return r.json()


class CLKClient(ServiceClient):
    async def list_workflows(self) -> dict:
        async with self._client() as c:
            r = await c.get("/api/workflows")
            return r.json()

    async def start_research(self, command: str, args: list, workspace_id: str | None = None, workflow: str | None = None) -> dict:
        body = {"command": command, "args": args, "workspace_id": workspace_id, "workflow": workflow}
        async with self._client() as c:
            r = await c.post("/api/research", json=body)
            return r.json()

    async def get_task(self, task_id: str) -> dict:
        async with self._client() as c:
            r = await c.get(f"/api/research/{task_id}")
            return r.json()

    async def stream_task(self, task_id: str):
        """Async generator yielding raw SSE lines from CLK."""
        ep = get_services()["clk"]
        async with httpx.AsyncClient(base_url=ep.base_url, timeout=ep.timeout) as c:
            async with c.stream("GET", f"/api/research/{task_id}/stream") as r:
                async for line in r.aiter_lines():
                    if line.startswith("data: "):
                        yield line[6:]

    async def list_artifacts(self, task_id: str) -> dict:
        async with self._client() as c:
            r = await c.get(f"/api/research/{task_id}/artifacts")
            return r.json()

    async def cancel_task(self, task_id: str) -> dict:
        async with self._client() as c:
            r = await c.post(f"/api/research/{task_id}/cancel")
            return r.json()


class AutoGUIClient(ServiceClient):
    async def list_tools(self) -> dict:
        async with self._client() as c:
            r = await c.get("/api/tools")
            return r.json()

    async def start_task(self, task: str, model: str | None = None, allow: dict | None = None, dry_run: bool = False) -> dict:
        body: dict = {"task": task, "dry_run": dry_run}
        if model:
            body["model"] = model
        if allow is not None:
            body["allow"] = allow
        logger.info("AutoGUI POST /api/task body: %s", json.dumps(body))
        async with self._client() as c:
            r = await c.post("/api/task", json=body)
            logger.info("AutoGUI POST /api/task response %d: %s", r.status_code, r.text[:500])
            return r.json()

    async def get_task(self, task_id: str) -> dict:
        async with self._client() as c:
            r = await c.get(f"/api/task/{task_id}")
            return r.json()

    async def stream_task(self, task_id: str):
        ep = get_services()["autogui"]
        async with httpx.AsyncClient(base_url=ep.base_url, timeout=ep.timeout) as c:
            async with c.stream("GET", f"/api/task/{task_id}/stream") as r:
                async for line in r.aiter_lines():
                    if line.startswith("data: "):
                        yield line[6:]

    async def await_task(self, task_id: str, timeout: float = 600.0) -> dict:
        """Stream task events until completion; return a result summary."""
        ep = get_services()["autogui"]
        events: list[dict] = []
        try:
            async with httpx.AsyncClient(
                base_url=ep.base_url,
                timeout=httpx.Timeout(timeout, connect=10.0),
            ) as c:
                async with c.stream("GET", f"/api/task/{task_id}/stream") as r:
                    async for line in r.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        try:
                            data = json.loads(line[6:])
                        except Exception:
                            continue
                        events.append(data)
                        if data.get("finished") or data.get("kind") == "done":
                            break
        except Exception as e:
            return {"ok": False, "task_id": task_id, "status": "error", "error": str(e)}

        text_outputs = [e["content"] for e in events if e.get("kind") == "text"]
        errors = [e["content"] for e in events if e.get("kind") == "error"]
        done_events = [e for e in events if e.get("kind") == "done"]
        finish_reason = (done_events[-1].get("data") or {}).get("finish_reason", "done") if done_events else "unknown"
        status = "error" if errors else finish_reason

        return {
            "ok": True,
            "task_id": task_id,
            "status": status,
            "summary": "\n".join(text_outputs) if text_outputs else "Task completed.",
            "errors": errors if errors else None,
        }

    async def cancel_task(self, task_id: str) -> dict:
        async with self._client() as c:
            r = await c.post(f"/api/task/{task_id}/cancel")
            return r.json()


class OSSOClient(ServiceClient):
    async def windows(self) -> dict:
        async with self._client() as c:
            r = await c.get("/api/windows")
            return r.json()

    async def description(self, window_index: int | None = None, mode: str = "accessibility") -> dict:
        params = {"mode": mode}
        if window_index is not None:
            params["window_index"] = window_index
        async with self._client() as c:
            r = await c.get("/api/description", params=params)
            return r.json()

    async def structure(self, window_index: int | None = None) -> dict:
        params = {}
        if window_index is not None:
            params["window_index"] = window_index
        async with self._client() as c:
            r = await c.get("/api/structure", params=params)
            return r.json()

    async def screenshot(self, window_index: int | None = None) -> dict:
        params = {}
        if window_index is not None:
            params["window_index"] = window_index
        async with self._client() as c:
            r = await c.get("/api/screenshot", params=params)
            return r.json()

    async def action(self, action_data: dict) -> dict:
        async with self._client() as c:
            r = await c.post("/api/action", json=action_data)
            return r.json()

    async def capabilities(self) -> dict:
        async with self._client() as c:
            r = await c.get("/api/capabilities")
            return r.json()


def get_clk_client() -> CLKClient:
    return CLKClient(get_services()["clk"])


def get_autogui_client() -> AutoGUIClient:
    return AutoGUIClient(get_services()["autogui"])


def get_osso_client() -> OSSOClient:
    return OSSOClient(get_services()["osso"])
