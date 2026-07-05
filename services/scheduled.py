"""Scheduled-task execution and the in-memory notification queue
(Phase 3 extraction from app.py). The scheduler loop itself lives in
scheduler.py; app.py's lifespan wires _run_scheduled_task and
_emit_scheduled_notification into it.
"""
from __future__ import annotations

import time

from . import llm
from .prompt_builder import build_system_prompt
from .storage import load_config, load_prompts

# --- Scheduled tasks ---

# Queue of pending scheduled-task notifications. The /api/scheduled-tasks/notifications/stream
# SSE endpoint drains this and pushes to the browser; once delivered the
# in-memory list is cleared. We persist nothing — recently-missed
# notifications can still be read from each task's history field.
_scheduled_notifications: list[dict] = []


async def _emit_scheduled_notification(task: dict, result: dict) -> None:
    _scheduled_notifications.append({
        "id": task.get("id"),
        "name": task.get("name"),
        "ok": bool(result.get("ok", True)),
        "summary": (result.get("summary") or "")[:500],
        "ts": time.time(),
    })


async def _run_scheduled_task(task: dict) -> dict:
    """Execute a scheduled task by running its prompt through the same code
    path as /api/chat. Returns {ok, summary}."""
    cfg = load_config()
    if not cfg.get("api_key") or not cfg.get("base_url"):
        return {"ok": False, "summary": "BetterWebUI is not connected to a backend."}
    # Honour the task's workspace if it specifies one.
    if task.get("workspace_id"):
        cfg = dict(cfg)
        cfg["active_workspace_id"] = task["workspace_id"]
    prompts = load_prompts()
    model = cfg.get("default_model") or ""
    if not model:
        return {"ok": False, "summary": "No default model configured."}
    system_prompt = build_system_prompt(cfg, prompts, mode="trusted")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": (task.get("prompt") or "").strip() or "(no prompt)"},
    ]
    try:
        text, _usage = await llm.chat_complete(messages, model, cfg)
    except Exception as exc:
        return {"ok": False, "summary": f"LLM call failed: {exc}"}
    return {"ok": True, "summary": (text or "").strip()[:1000]}
