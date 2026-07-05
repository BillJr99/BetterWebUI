"""Onboarding wizard templates and completion.

Extracted from app.py (Phase 3). Route paths, request/response shapes, and
behavior are unchanged. Handlers reach shared state through the services
package (module-attribute access for anything tests monkeypatch via app.py's
forwarding properties).
"""
from __future__ import annotations

import time
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import catalog, storage

router = APIRouter()

# --- Onboarding ---

@router.get("/api/onboarding/templates")
async def onboarding_templates() -> dict:
    return {"templates": catalog.ONBOARDING_TEMPLATES}


class OnboardingCompleteIn(BaseModel):
    template_id: Optional[str] = None
    workspace_name: Optional[str] = None


@router.post("/api/onboarding/complete")
async def onboarding_complete(body: OnboardingCompleteIn) -> dict:
    cfg = storage.load_config()

    # If a template was requested, validate it before flipping onboarding_done
    # so a bad template_id can't permanently skip the wizard.
    if body.template_id:
        tmpl = next((t for t in catalog.ONBOARDING_TEMPLATES if t["id"] == body.template_id), None)
        if not tmpl:
            raise HTTPException(400, f"Unknown onboarding template '{body.template_id}'.")
        ws_data = storage.load_workspaces()
        wid = uuid.uuid4().hex[:8]
        ws_name = body.workspace_name or tmpl["name"]
        # Upsert system prompt
        p_data = storage.load_prompts()
        pid = f"onboarding-{tmpl['id']}"
        if not any(x["id"] == pid for x in p_data["prompts"]):
            p_data["prompts"].append({"id": pid, "name": ws_name, "content": tmpl["system_prompt"]})
            storage.save_json(storage.PROMPTS_PATH, p_data)
        ws_data["workspaces"].append({
            "id": wid,
            "name": ws_name,
            "description": tmpl["description"],
            "system_prompt_id": pid,
            "active_skills": tmpl.get("skills", []),
            "active_mcp_servers": tmpl.get("mcp", []),
            "active_cli_tools": tmpl.get("cli", []),
            "files": [],
            "mode": "approve-each",
            "created_at": int(time.time()),
            "updated_at": int(time.time()),
        })
        storage.save_json(storage.WORKSPACES_PATH, ws_data)
        # Set as active and only now flip onboarding_done, so partial failures
        # above re-raise (caller can retry) instead of locking out the wizard.
        cfg["active_workspace_id"] = wid
        cfg["onboarding_done"] = True
        storage.save_json(storage.CONFIG_PATH, cfg)
        return {"ok": True, "workspace_id": wid, "workspace_name": ws_name}

    cfg["onboarding_done"] = True
    storage.save_json(storage.CONFIG_PATH, cfg)
    return {"ok": True}
