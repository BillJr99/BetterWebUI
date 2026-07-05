"""Scheduled task CRUD, notifications drain, and verification logs.

Extracted from app.py (Phase 3). Route paths, request/response shapes, and
behavior are unchanged. Handlers reach shared state through the services
package (module-attribute access for anything tests monkeypatch via app.py's
forwarding properties).
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from services import scheduled as scheduled_svc
from services import session, storage

router = APIRouter()

class ScheduledTaskIn(BaseModel):
    id: Optional[str] = None
    name: str
    prompt: str
    workspace_id: Optional[str] = ""
    schedule: dict
    enabled: Optional[bool] = True


@router.get("/api/scheduled-tasks")
async def list_scheduled_tasks(request: Request) -> dict:
    session._require_local_caller(request)
    from scheduler import list_tasks
    return {"tasks": list_tasks(storage.SCHEDULED_TASKS_PATH)}


@router.post("/api/scheduled-tasks")
async def create_or_update_scheduled_task(body: ScheduledTaskIn, request: Request) -> dict:
    session._require_local_caller(request)
    from scheduler import upsert_task
    task = body.model_dump()
    if not task.get("id"):
        task["id"] = uuid.uuid4().hex
    task.setdefault("history", [])
    task.setdefault("last_run_at", None)
    return upsert_task(storage.SCHEDULED_TASKS_PATH, task)


@router.delete("/api/scheduled-tasks/{task_id}")
async def delete_scheduled_task(task_id: str, request: Request) -> dict:
    session._require_local_caller(request)
    from scheduler import delete_task
    ok = delete_task(storage.SCHEDULED_TASKS_PATH, task_id)
    if not ok:
        raise HTTPException(404, "Task not found.")
    return {"ok": True}


@router.get("/api/verification/{chat_id}")
async def get_verification_log(chat_id: str, request: Request) -> dict:
    """Return verification trace entries for a chat (one per tool call)."""
    session._require_local_caller(request)
    chat_id = re.sub(r"[^A-Za-z0-9_-]+", "_", chat_id)[:128].strip("._-")
    if not chat_id:
        raise HTTPException(400, "Invalid chat_id.")
    path = storage.DATA_DIR / "verification" / f"{chat_id}.jsonl"
    if not path.exists():
        return {"entries": []}
    entries: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except Exception:
                    continue
    except Exception as exc:
        raise HTTPException(500, f"Could not read verification log: {exc}")
    return {"entries": entries}


@router.get("/api/scheduled-tasks/notifications")
async def drain_scheduled_notifications(request: Request) -> dict:
    """Poll-style: returns and clears pending notifications. The frontend
    polls this on a short interval rather than holding an SSE open."""
    session._require_local_caller(request)
    pending = list(scheduled_svc._scheduled_notifications)
    scheduled_svc._scheduled_notifications.clear()
    return {"notifications": pending}
