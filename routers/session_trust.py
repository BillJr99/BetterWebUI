"""Approval resolution, session-trusted commands, and file-picker responses.

Extracted from app.py (Phase 3). Route paths, request/response shapes, and
behavior are unchanged. Handlers reach shared state through the services
package (module-attribute access for anything tests monkeypatch via app.py's
forwarding properties).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from services import session

router = APIRouter()

# --- Approvals ---

class ApprovalIn(BaseModel):
    approval_id: str
    approved: bool
    trust_session: Optional[bool] = False
    command: Optional[str] = None


@router.post("/api/approve")
async def approve(a: ApprovalIn, request: Request) -> dict:
    # Approving an in-flight tool call can release shell/file-write side
    # effects, so restrict the endpoint to local callers up front (matching
    # /api/project/* and /api/session/trust). Remote clients can't approve
    # tool calls without the operator's local browser.
    session._require_local_caller(request)
    ok = session.approvals.resolve(a.approval_id, a.approved)
    if not ok:
        raise HTTPException(404, "Unknown approval id")
    if a.approved and a.trust_session and a.command:
        session._session_trusted_commands.add(a.command)
    return {"ok": True}
class SessionTrustIn(BaseModel):
    command: str
@router.post("/api/session/trust")
async def session_trust(t: SessionTrustIn, request: Request) -> dict:
    session._require_local_caller(request)
    session._session_trusted_commands.add(t.command)
    return {"ok": True, "trusted_count": len(session._session_trusted_commands)}


@router.get("/api/session/trust")
async def list_session_trust(request: Request) -> dict:
    session._require_local_caller(request)
    return {"commands": list(session._session_trusted_commands)}


@router.delete("/api/session/trust")
async def clear_session_trust(request: Request) -> dict:
    session._require_local_caller(request)
    session._session_trusted_commands.clear()
    return {"ok": True}


# --- File-picker responses ---

class FileResponseIn(BaseModel):
    request_id: str
    files: Optional[list] = None
    action: Optional[str] = None


@router.post("/api/file-response")
async def post_file_response(r: FileResponseIn, request: Request) -> dict:
    # Resolving an in-flight file-picker request can influence the tool gate,
    # so restrict this endpoint to local callers (matching /api/approve and
    # /api/session/trust). A remote host must not be able to answer file
    # requests destined for the operator's local browser.
    session._require_local_caller(request)
    ok = session.file_responses.resolve(r.request_id, r.files or [])
    if not ok:
        raise HTTPException(404, "Unknown file request id")
    return {"ok": True}
