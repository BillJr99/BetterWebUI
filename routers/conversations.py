"""Conversation listing, search, summaries, pin/tag/fork, and deletion.

Extracted from app.py (Phase 3). Route paths, request/response shapes, and
behavior are unchanged. Handlers reach shared state through the services
package (module-attribute access for anything tests monkeypatch via app.py's
forwarding properties).
"""
from __future__ import annotations

import time
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from services import session, storage

router = APIRouter()

# --- Conversations ---

@router.get("/api/conversations")
async def list_conversations(request: Request) -> dict:
    session._require_local_caller(request)
    data = storage.load_conversations()
    summary = []
    for cid, conv in data["conversations"].items():
        summary.append({
            "id": cid,
            "title": conv.get("title", "Untitled"),
            "updated_at": conv.get("updated_at", 0),
            "pinned": conv.get("pinned", False),
            "workspace_id": conv.get("workspace_id", ""),
            "tags": conv.get("tags", []),
        })
    summary.sort(key=lambda x: (not x["pinned"], -x["updated_at"]))
    return {"conversations": summary}


@router.get("/api/conversations/recent")
async def recent_conversations(request: Request, limit: int = 3) -> dict:
    """Return the most-recently-updated conversations with their one-line summaries."""
    session._require_local_caller(request)
    data = storage.load_conversations()
    convs = sorted(
        [{"id": cid, **conv} for cid, conv in data["conversations"].items()],
        key=lambda x: -x.get("updated_at", 0),
    )[:max(1, min(10, limit))]
    return {"recent": [
        {
            "id": c["id"],
            "title": c.get("title", "Untitled"),
            "updated_at": c.get("updated_at", 0),
            "summary": c.get("summary", ""),
            "message_count": len(c.get("messages", [])),
        }
        for c in convs
    ]}


@router.post("/api/conversations/{cid}/summary")
async def set_conversation_summary(request: Request, cid: str) -> dict:
    """Store a one-line summary for a conversation (generated client-side or by the LLM)."""
    session._require_local_caller(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    summary = str(body.get("summary", ""))[:300].strip()
    data = storage.load_conversations()
    conv = data["conversations"].get(cid)
    if not conv:
        raise HTTPException(404, "Not found")
    conv["summary"] = summary
    storage.save_json(storage.CONVERSATIONS_PATH, data)
    return {"ok": True}


@router.get("/api/conversations/search")
async def search_conversations(request: Request, q: str = "") -> dict:
    session._require_local_caller(request)
    data = storage.load_conversations()
    q_lower = q.lower().strip()
    results = []
    for cid, conv in data["conversations"].items():
        if not q_lower:
            results.append({"id": cid, "title": conv.get("title", ""), "updated_at": conv.get("updated_at", 0)})
            continue
        title = conv.get("title", "").lower()
        msgs_text = " ".join(
            m.get("content", "") for m in conv.get("messages", []) if isinstance(m.get("content"), str)
        ).lower()
        if q_lower in title or q_lower in msgs_text:
            # Find first matching snippet
            idx = msgs_text.find(q_lower)
            snippet = ""
            if idx != -1:
                raw_text = " ".join(
                    m.get("content", "") for m in conv.get("messages", []) if isinstance(m.get("content"), str)
                )
                snippet = raw_text[max(0, idx - 40) : idx + 80]
            results.append({
                "id": cid,
                "title": conv.get("title", ""),
                "updated_at": conv.get("updated_at", 0),
                "snippet": snippet,
            })
    results.sort(key=lambda x: -x["updated_at"])
    return {"results": results[:50]}


@router.get("/api/conversations/{cid}")
async def get_conversation(request: Request, cid: str) -> dict:
    session._require_local_caller(request)
    data = storage.load_conversations()
    conv = data["conversations"].get(cid)
    if not conv:
        raise HTTPException(404, "Not found")
    return conv


@router.delete("/api/conversations/{cid}")
async def delete_conversation(request: Request, cid: str) -> dict:
    session._require_local_caller(request)
    data = storage.load_conversations()
    data["conversations"].pop(cid, None)
    storage.save_json(storage.CONVERSATIONS_PATH, data)
    return {"ok": True}


class PinIn(BaseModel):
    pinned: bool


@router.post("/api/conversations/{cid}/pin")
async def pin_conversation(request: Request, cid: str, body: PinIn) -> dict:
    session._require_local_caller(request)
    data = storage.load_conversations()
    conv = data["conversations"].get(cid)
    if not conv:
        raise HTTPException(404, "Not found")
    conv["pinned"] = body.pinned
    storage.save_json(storage.CONVERSATIONS_PATH, data)
    return {"ok": True}


class TagIn(BaseModel):
    tags: list[str]


@router.post("/api/conversations/{cid}/tags")
async def tag_conversation(request: Request, cid: str, body: TagIn) -> dict:
    session._require_local_caller(request)
    data = storage.load_conversations()
    conv = data["conversations"].get(cid)
    if not conv:
        raise HTTPException(404, "Not found")
    conv["tags"] = body.tags
    storage.save_json(storage.CONVERSATIONS_PATH, data)
    return {"ok": True}


class ForkIn(BaseModel):
    fork_at: Optional[int] = None          # index sent by the JS client
    from_message_index: Optional[int] = None  # alias kept for back-compat
    title: Optional[str] = None


@router.post("/api/conversations/{cid}/fork")
async def fork_conversation(request: Request, cid: str, body: ForkIn) -> dict:
    session._require_local_caller(request)
    data = storage.load_conversations()
    conv = data["conversations"].get(cid)
    if not conv:
        raise HTTPException(404, "Not found")
    messages = conv.get("messages", [])
    if body.fork_at is not None:
        idx = body.fork_at
    elif body.from_message_index is not None:
        idx = body.from_message_index
    else:
        idx = len(messages) - 1
    idx = max(0, min(idx, len(messages) - 1))
    forked_messages = messages[: idx + 1]
    new_cid = uuid.uuid4().hex
    title = body.title or f"{conv.get('title', 'Conversation')} (fork)"
    # Copy schema-shaped fields from the source so the forked conversation
    # matches what save_conversation/load_conversations produce elsewhere.
    # Forks start unpinned with the parent's tags, workspace, and current
    # task plan snapshot — but as a brand-new conversation otherwise.
    now = int(time.time())
    data["conversations"][new_cid] = {
        "id": new_cid,
        "title": title,
        "messages": forked_messages,
        "parent_id": cid,
        "pinned": False,
        "tags": list(conv.get("tags", [])),
        "task_plan": list(conv.get("task_plan", [])),
        "workspace_id": conv.get("workspace_id", ""),
        "updated_at": now,
        "created_at": now,
    }
    storage.save_json(storage.CONVERSATIONS_PATH, data)
    return {"id": new_cid, "title": title}
