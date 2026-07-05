"""OAuth provider status/connect/disconnect routes.

Extracted from app.py (Phase 3). Route paths, request/response shapes, and
behavior are unchanged. Handlers reach shared state through the services
package (module-attribute access for anything tests monkeypatch via app.py's
forwarding properties).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from services import session, storage

router = APIRouter()

# --- OAuth helper endpoints ---

@router.get("/api/oauth/status/{provider}")
async def oauth_status(provider: str, request: Request) -> dict:
    session._require_local_caller(request)
    from services.oauth import get_oauth_status
    return get_oauth_status(provider, storage.DATA_DIR)


@router.post("/api/oauth/connect/{provider}")
async def oauth_connect(provider: str, request: Request) -> dict:
    """Return an authorization URL for the user to open in their browser."""
    session._require_local_caller(request)
    cfg = storage.load_config()
    try:
        from services.oauth import start_oauth_flow
        auth_url = await start_oauth_flow(provider, cfg, storage.DATA_DIR)
        return {"auth_url": auth_url}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"OAuth connect failed: {exc}")


@router.delete("/api/oauth/disconnect/{provider}")
async def oauth_disconnect(provider: str, request: Request) -> dict:
    """Remove stored OAuth token."""
    session._require_local_caller(request)
    from services.oauth import revoke_oauth_token
    removed = revoke_oauth_token(provider, storage.DATA_DIR)
    return {"removed": removed}
