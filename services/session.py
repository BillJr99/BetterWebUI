"""In-memory per-server-session state and the local-caller gate.

Extracted from app.py (Phase 3). The objects here (approvals, file_responses,
_session_trusted_commands, _command_explanation_cache) are shared singletons:
app.py re-exports them by identity so existing `app_module.approvals` style
test access keeps working. They are only ever mutated in place, never rebound.
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
import uuid

from fastapi import HTTPException, Request

# ---------------------------------------------------------------------------
# Session-level in-memory stores
# ---------------------------------------------------------------------------

# Commands trusted for the duration of this server session
_session_trusted_commands: set[str] = set()

# Explanation cache keyed by command hash
_command_explanation_cache: dict[str, str] = {}
# ---------------------------------------------------------------------------
# Approval / file-response state
# ---------------------------------------------------------------------------

class ApprovalState:
    def __init__(self) -> None:
        self.events: dict[str, asyncio.Event] = {}
        self.results: dict[str, bool] = {}

    def new(self) -> str:
        aid = uuid.uuid4().hex
        self.events[aid] = asyncio.Event()
        return aid

    async def wait(self, aid: str, timeout: float = 600.0) -> bool:
        try:
            await asyncio.wait_for(self.events[aid].wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        return self.results.get(aid, False)

    def resolve(self, aid: str, approved: bool) -> bool:
        if aid not in self.events:
            return False
        self.results[aid] = approved
        self.events[aid].set()
        return True


approvals = ApprovalState()


class FileResponseStore:
    def __init__(self) -> None:
        self.events: dict[str, asyncio.Event] = {}
        self.results: dict[str, list[dict]] = {}

    def new(self) -> str:
        rid = uuid.uuid4().hex
        self.events[rid] = asyncio.Event()
        return rid

    async def wait(self, rid: str, timeout: float = 600.0) -> list[dict]:
        try:
            await asyncio.wait_for(self.events[rid].wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return []
        return self.results.get(rid, [])

    def resolve(self, rid: str, files: list[dict]) -> bool:
        if rid not in self.events:
            return False
        self.results[rid] = files
        self.events[rid].set()
        return True


file_responses = FileResponseStore()
_DOCKER_BRIDGE_RANGE = ipaddress.ip_network("172.16.0.0/12")


def _require_local_caller(request: Request) -> None:
    """Reject requests that don't come from a local client.

    Session-trust state and the project-file endpoints can affect on-disk
    state, so they shouldn't be reachable from arbitrary remote hosts when
    the server is bound to 0.0.0.0. Default: loopback only. Docker bridge
    and broader LAN access are opt-in via env vars:
      BETTERWEBUI_ALLOW_DOCKER=true → also accept 172.16.0.0/12 (the
                                      Docker bridge range). docker-compose.yml
                                      sets this by default so the
                                      containerized UI just works.
      BETTERWEBUI_ALLOW_LAN=true    → accept any RFC1918 private IP
      BETTERWEBUI_REQUIRE_LOCAL=strict → loopback only (overrides both above)

    The narrower default protects bare-metal deployments on real LANs that
    happen to use 172.16/12 — they no longer leak local-only endpoints to
    LAN peers just because the network's CIDR overlaps Docker's range.
    """
    client_host = (request.client.host if request.client else "") or ""
    if client_host in ("127.0.0.1", "::1", "localhost", "testclient"):
        return
    mode = os.environ.get("BETTERWEBUI_REQUIRE_LOCAL", "").lower()
    if mode == "strict":
        raise HTTPException(403, "This endpoint is limited to local callers.")
    try:
        addr = ipaddress.ip_address(client_host)
    except ValueError:
        raise HTTPException(403, "This endpoint is limited to local callers.")
    if addr.is_loopback:
        return
    # Docker bridge range is opt-in (default off) so a bare-metal install on
    # a real 172.16/12 LAN doesn't accidentally expose local-only endpoints.
    allow_docker = os.environ.get("BETTERWEBUI_ALLOW_DOCKER", "").lower() in ("1", "true", "yes")
    if allow_docker and isinstance(addr, ipaddress.IPv4Address) and addr in _DOCKER_BRIDGE_RANGE:
        return
    # Broader LAN access is also opt-in.
    allow_lan = os.environ.get("BETTERWEBUI_ALLOW_LAN", "").lower() in ("1", "true", "yes")
    if allow_lan and addr.is_private:
        return
    raise HTTPException(403, "This endpoint is limited to local callers.")


def _is_local_caller(request: Request) -> bool:
    try:
        _require_local_caller(request)
        return True
    except HTTPException:
        return False
