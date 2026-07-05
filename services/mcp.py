"""MCP stdio client and lifecycle manager (Phase 3 extraction from app.py).

`mcp_manager` is the shared singleton; app.py re-exports it by identity so
`patch("app.mcp_manager.reconcile")` in the test suite keeps patching the
same object the routes use.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Optional

from . import storage
from .storage import load_mcp_servers


class MCPStdioClient:
    def __init__(self, name: str, command: str, args: list[str], env: dict | None = None):
        self.name = name
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.tools: list[dict] = []
        self._next_id = 0
        self._read_lock = asyncio.Lock()
        self.error: Optional[str] = None

    async def start(self, timeout: float = 30.0) -> None:
        env = {**os.environ, **self.env}
        try:
            self.proc = await asyncio.create_subprocess_exec(
                self.command,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as exc:
            self.error = f"Command not found: {self.command}. {exc}"
            return
        try:
            await asyncio.wait_for(self._handshake(), timeout=timeout)
        except asyncio.TimeoutError:
            self.error = "MCP server did not respond to initialize within 30s."
            await self.stop()
        except Exception as exc:
            self.error = f"MCP handshake failed: {exc}"
            await self.stop()

    async def _handshake(self) -> None:
        await self._call("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "BetterWebUI", "version": "0.2"},
        })
        await self._notify("notifications/initialized", {})
        result = await self._call("tools/list", {})
        self.tools = result.get("tools", []) if isinstance(result, dict) else []

    async def _send(self, message: dict) -> None:
        if not self.proc or not self.proc.stdin:
            raise RuntimeError("MCP server is not running.")
        line = (json.dumps(message) + "\n").encode("utf-8")
        self.proc.stdin.write(line)
        await self.proc.stdin.drain()

    async def _read_one(self) -> dict:
        async with self._read_lock:
            assert self.proc and self.proc.stdout
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    raise RuntimeError("MCP server closed unexpectedly.")
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue

    async def _call(self, method: str, params: dict) -> dict:
        self._next_id += 1
        req_id = self._next_id
        await self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        while True:
            msg = await self._read_one()
            if msg.get("id") == req_id:
                if "error" in msg:
                    err = msg["error"]
                    raise RuntimeError(err.get("message") if isinstance(err, dict) else str(err))
                return msg.get("result") or {}

    async def _notify(self, method: str, params: dict) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        return await self._call("tools/call", {"name": tool_name, "arguments": arguments})

    async def stop(self) -> None:
        if not self.proc:
            return
        try:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                self.proc.kill()
                await self.proc.wait()
        except ProcessLookupError:
            pass
        self.proc = None


class MCPManager:
    def __init__(self) -> None:
        self.clients: dict[str, MCPStdioClient] = {}

    async def reconcile(self) -> None:
        cfg = load_mcp_servers()
        wanted = {s["name"]: s for s in cfg.get("servers", []) if s.get("enabled", True)}
        for name in list(self.clients):
            if name not in wanted:
                await self.clients[name].stop()
                del self.clients[name]
        for name, s in wanted.items():
            if name in self.clients:
                continue
            env = dict(s.get("env") or {})
            # Substitute OAuth access tokens: {oauth.google.access_token} etc.
            try:
                from services.oauth import get_oauth_token as _get_oauth_tok
                for k, v in list(env.items()):
                    if "{oauth." in str(v):
                        for provider in ("google", "microsoft"):
                            placeholder = f"{{oauth.{provider}.access_token}}"
                            if placeholder in str(v):
                                tok = _get_oauth_tok(provider, storage.DATA_DIR)
                                if tok and tok.get("access_token"):
                                    env[k] = str(v).replace(placeholder, tok["access_token"])
            except Exception as exc:
                # Non-fatal: the server starts without the substituted token,
                # but the user should be able to see why their OAuth-backed
                # MCP server is misbehaving.
                logging.getLogger("betterwebui.mcp").warning(
                    "OAuth token substitution failed for MCP server '%s': %s", name, exc,
                )
            client = MCPStdioClient(
                name=name,
                command=s.get("command", ""),
                args=s.get("args", []),
                env=env,
            )
            await client.start()
            self.clients[name] = client

    def status(self) -> list[dict]:
        out = []
        for name, client in self.clients.items():
            out.append({
                "name": name,
                "running": client.proc is not None and client.error is None,
                "error": client.error,
                "tool_count": len(client.tools),
                "tools": [
                    {"name": t.get("name"), "description": t.get("description", "")}
                    for t in client.tools
                ],
            })
        return out

    def list_all_tools(self, allowed_servers: Optional[list[str]] = None) -> list[dict]:
        out = []
        for name, client in self.clients.items():
            if allowed_servers is not None and name not in allowed_servers:
                continue
            for t in client.tools:
                out.append({
                    "server": name,
                    "name": t.get("name"),
                    "description": t.get("description", ""),
                })
        return out

    async def call(self, server_name: str, tool_name: str, args: dict) -> dict:
        client = self.clients.get(server_name)
        if not client:
            return {"error": f"MCP server '{server_name}' is not running."}
        if client.error:
            return {"error": f"MCP server '{server_name}' error: {client.error}"}
        try:
            result = await client.call_tool(tool_name, args)
        except Exception as exc:
            return {"error": f"MCP call failed: {exc}"}
        return result if isinstance(result, dict) else {"result": result}


mcp_manager = MCPManager()
