"""
Tests for the transient-uploads, memory-extract, and scheduled-tasks endpoints
added in the response-robustness branch.
"""

from __future__ import annotations

import io
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Transient uploads
# ---------------------------------------------------------------------------

def test_transient_upload_and_delete(client, isolated_dirs):
    r = client.post(
        "/api/uploads/transient?chat_id=test-1",
        files={"file": ("hello.txt", io.BytesIO(b"hi there"), "text/plain")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["filename"] == "hello.txt"
    assert body["url"].startswith("/uploads/transient/test-1/")
    chat_dir = isolated_dirs["data"] / "uploads" / "transient" / "test-1"
    assert chat_dir.exists()
    assert any(chat_dir.iterdir())

    r2 = client.delete("/api/uploads/transient/test-1")
    assert r2.status_code == 200
    assert not chat_dir.exists()


def test_transient_upload_sanitizes_chat_id(client, isolated_dirs):
    # Path-traversal attempt should be sanitized to a flat name.
    r = client.post(
        "/api/uploads/transient?chat_id=../../etc",
        files={"file": ("a.txt", io.BytesIO(b"x"), "text/plain")},
    )
    assert r.status_code == 200
    body = r.json()
    assert "../../etc" not in body["url"]
    # The actual directory should be created under transient/
    transient_root = isolated_dirs["data"] / "uploads" / "transient"
    subdirs = [p.name for p in transient_root.iterdir()]
    assert all(".." not in s for s in subdirs)


def test_transient_sweep_removes_old_dirs(isolated_dirs):
    import app as app_module
    transient_root = isolated_dirs["data"] / "uploads" / "transient"
    transient_root.mkdir(parents=True, exist_ok=True)
    old = transient_root / "stale-chat"
    old.mkdir()
    (old / "f.txt").write_text("x")
    very_old = time.time() - (48 * 3600)
    import os
    os.utime(old, (very_old, very_old))
    removed = app_module._sweep_transient_uploads()
    assert removed >= 1
    assert not old.exists()


# ---------------------------------------------------------------------------
# Memory extraction
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_memory_extract_returns_candidates(client, isolated_dirs):
    # Set up config so the endpoint doesn't short-circuit on missing keys.
    client.post("/api/config", json={"base_url": "http://example.invalid", "api_key": "dummy",
                                     "default_model": "test-model"})
    with patch(
        "app.chat_complete",
        new=AsyncMock(return_value=(
            '{"candidates": [{"text": "User prefers concise answers", "category": "preference"}]}',
            {},
        )),
    ):
        r = client.post("/api/memory/extract", json={
            "user_message": "Please keep replies short.",
            "assistant_message": "Got it!",
        })
    assert r.status_code == 200
    body = r.json()
    assert len(body["candidates"]) == 1
    assert body["candidates"][0]["text"] == "User prefers concise answers"
    assert body["candidates"][0]["category"] == "preference"


@pytest.mark.asyncio
async def test_memory_extract_returns_empty_on_garbage(client):
    client.post("/api/config", json={"base_url": "http://example.invalid", "api_key": "dummy",
                                     "default_model": "test-model"})
    with patch(
        "app.chat_complete",
        new=AsyncMock(return_value=("not json at all", {})),
    ):
        r = client.post("/api/memory/extract", json={
            "user_message": "x",
            "assistant_message": "y",
        })
    assert r.status_code == 200
    assert r.json()["candidates"] == []


def test_memory_extract_no_model_returns_empty(client):
    # No default_model configured: should short-circuit safely.
    r = client.post("/api/memory/extract", json={"user_message": "x", "assistant_message": "y"})
    assert r.status_code == 200
    assert r.json()["candidates"] == []


# ---------------------------------------------------------------------------
# Scheduled tasks
# ---------------------------------------------------------------------------

def test_scheduled_task_crud(client):
    r = client.post("/api/scheduled-tasks", json={
        "name": "Daily summary",
        "prompt": "Summarise the day.",
        "schedule": {"kind": "cron-lite", "hour": 9, "minute": 0, "weekdays": [1, 2, 3, 4, 5]},
        "enabled": True,
    })
    assert r.status_code == 200
    task = r.json()
    assert task["id"]
    assert task["next_run_at"] is not None

    r2 = client.get("/api/scheduled-tasks")
    assert r2.status_code == 200
    tasks = r2.json()["tasks"]
    assert any(t["id"] == task["id"] for t in tasks)

    # Update: pause it.
    paused = dict(task)
    paused["enabled"] = False
    r3 = client.post("/api/scheduled-tasks", json=paused)
    assert r3.status_code == 200
    assert r3.json()["enabled"] is False

    r4 = client.delete(f"/api/scheduled-tasks/{task['id']}")
    assert r4.status_code == 200
    r5 = client.get("/api/scheduled-tasks")
    assert all(t["id"] != task["id"] for t in r5.json()["tasks"])


def test_scheduled_compute_next_run_once():
    from scheduler import compute_next_run
    future_iso = "2099-01-01T09:00:00+00:00"
    nxt = compute_next_run({"schedule": {"kind": "once", "at_iso": future_iso}})
    assert nxt is not None
    # Past one-shots should not fire.
    past_iso = "2000-01-01T09:00:00+00:00"
    assert compute_next_run({"schedule": {"kind": "once", "at_iso": past_iso}}) is None


def test_scheduled_compute_next_run_cron_lite():
    from scheduler import compute_next_run
    # Any weekday at 9:00 — should return a future timestamp.
    nxt = compute_next_run({"schedule": {"kind": "cron-lite", "hour": 9, "minute": 0, "weekdays": list(range(7))}})
    assert nxt is not None
    assert nxt > time.time()
