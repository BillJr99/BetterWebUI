import httpx
from .registry import get_services, ServiceEndpoint


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

    async def start_task(self, task: str, allow: dict | None = None, dry_run: bool = False) -> dict:
        body: dict = {"task": task, "dry_run": dry_run}
        if allow is not None:
            body["allow"] = allow
        async with self._client() as c:
            r = await c.post("/api/task", json=body)
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
