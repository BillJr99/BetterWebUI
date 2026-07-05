"""MCP server and CLI shortcut configuration routes.

Extracted from app.py (Phase 3). Route paths, request/response shapes, and
behavior are unchanged. Handlers reach shared state through the services
package (module-attribute access for anything tests monkeypatch via app.py's
forwarding properties).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from services import catalog, mcp, storage

router = APIRouter()

# --- MCP servers ---

class MCPServerIn(BaseModel):
    name: str
    command: str
    args: Optional[list[str]] = None
    env: Optional[dict] = None
    description: Optional[str] = ""
    enabled: Optional[bool] = True


@router.get("/api/mcp/registry")
async def mcp_registry() -> dict:
    return {"registry": catalog.MCP_REGISTRY}


@router.get("/api/mcp/servers")
async def list_mcp_servers_endpoint() -> dict:
    cfg = storage.load_mcp_servers()
    status_by_name = {s["name"]: s for s in mcp.mcp_manager.status()}
    out = []
    for s in cfg.get("servers", []):
        st = status_by_name.get(s["name"])
        out.append({
            **s,
            "running": (st or {}).get("running", False),
            "error": (st or {}).get("error"),
            "tool_count": (st or {}).get("tool_count", 0),
            "tools": (st or {}).get("tools", []),
        })
    return {"servers": out}


@router.post("/api/mcp/servers")
async def upsert_mcp_server(s: MCPServerIn) -> dict:
    data = storage.load_mcp_servers()
    payload = s.model_dump(exclude_none=True)
    existing = next((i for i, x in enumerate(data["servers"]) if x["name"] == s.name), None)
    if existing is not None:
        data["servers"][existing] = {**data["servers"][existing], **payload}
    else:
        data["servers"].append(payload)
    storage.save_json(storage.MCP_PATH, data)
    await mcp.mcp_manager.reconcile()
    return {"name": s.name}


@router.delete("/api/mcp/servers/{name}")
async def delete_mcp_server(name: str) -> dict:
    data = storage.load_mcp_servers()
    data["servers"] = [x for x in data["servers"] if x["name"] != name]
    storage.save_json(storage.MCP_PATH, data)
    await mcp.mcp_manager.reconcile()
    return {"ok": True}


@router.post("/api/mcp/reconcile")
async def mcp_reconcile_endpoint() -> dict:
    await mcp.mcp_manager.reconcile()
    return {"servers": mcp.mcp_manager.status()}


# --- CLI shortcuts ---

class CliToolIn(BaseModel):
    id: str
    name: str
    description: Optional[str] = ""
    command_template: str
    examples: Optional[list[str]] = None
    approval_policy: Optional[str] = "ask"


@router.get("/api/cli/registry")
async def cli_registry() -> dict:
    return {"registry": catalog.CLI_REGISTRY}


@router.get("/api/cli/tools")
async def list_cli_tools_endpoint() -> dict:
    return storage.load_cli_tools()


@router.post("/api/cli/tools")
async def upsert_cli_tool(t: CliToolIn) -> dict:
    data = storage.load_cli_tools()
    payload = t.model_dump(exclude_none=True)
    existing = next((i for i, x in enumerate(data["tools"]) if x["id"] == t.id), None)
    if existing is not None:
        data["tools"][existing] = {**data["tools"][existing], **payload}
    else:
        data["tools"].append(payload)
    storage.save_json(storage.CLI_PATH, data)
    return {"id": t.id}


@router.delete("/api/cli/tools/{tid}")
async def delete_cli_tool(tid: str) -> dict:
    data = storage.load_cli_tools()
    data["tools"] = [x for x in data["tools"] if x["id"] != tid]
    storage.save_json(storage.CLI_PATH, data)
    return {"ok": True}
