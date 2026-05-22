"""
scheduler.py — hand-rolled asyncio scheduler for BetterWebUI's scheduled
and recurring tasks. Stored on disk as JSON; the loop polls every 30s
and triggers due tasks via the run_callback injected by app.py.

A task is:
    {
      "id": "...",
      "name": "Daily summary",
      "prompt": "Summarise unread emails",
      "workspace_id": "" | "<workspace id>",
      "schedule": {"kind": "once"|"interval"|"cron-lite",
                   "at_iso": "...", "every_seconds": 3600,
                   "weekdays": [0..6], "hour": 9, "minute": 0},
      "enabled": true,
      "next_run_at": <epoch>,
      "last_run_at": <epoch>|null,
      "history": [{"ts": ..., "ok": bool, "summary": "..."}, ...]
    }

The "cron-lite" kind covers the common cases without a real cron parser:
{"kind": "cron-lite", "weekdays": [0..6], "hour": h, "minute": m}.
For one-shots, kind="once" with at_iso. For simple intervals,
kind="interval" with every_seconds.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional

log = logging.getLogger("betterwebui.scheduler")


def _now() -> float:
    return time.time()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _read_tasks(path: Path) -> list[dict]:
    try:
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("tasks"), list):
            return data["tasks"]
        if isinstance(data, list):
            return data
    except Exception as exc:
        log.warning("Could not read tasks at %s: %s", path, exc)
    return []


def _write_tasks(path: Path, tasks: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"tasks": tasks}, indent=2), encoding="utf-8")
    tmp.replace(path)


def compute_next_run(task: dict, *, after: Optional[float] = None) -> Optional[float]:
    """Return the next epoch timestamp this task should fire, or None for
    completed one-shots."""
    after = after if after is not None else _now()
    sched = task.get("schedule") or {}
    kind = sched.get("kind") or "once"

    if kind == "once":
        at_iso = sched.get("at_iso")
        if not at_iso:
            return None
        try:
            dt = datetime.fromisoformat(at_iso.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp() if dt.timestamp() > after else None
        except Exception:
            return None

    if kind == "interval":
        every = int(sched.get("every_seconds") or 0)
        if every <= 0:
            return None
        last = task.get("last_run_at") or after
        return float(last) + every

    if kind == "cron-lite":
        try:
            hour = int(sched.get("hour", 0))
            minute = int(sched.get("minute", 0))
        except Exception:
            return None
        weekdays = sched.get("weekdays") or list(range(7))
        if not weekdays:
            return None
        # Walk forward day-by-day up to 14 days (safety cap).
        now_dt = datetime.fromtimestamp(after, tz=timezone.utc)
        for d in range(0, 14):
            cand = (now_dt + timedelta(days=d)).replace(hour=hour, minute=minute, second=0, microsecond=0)
            if cand.weekday() not in weekdays:
                continue
            if cand.timestamp() <= after:
                continue
            return cand.timestamp()
        return None

    return None


def _refresh_next_run(task: dict) -> None:
    nxt = compute_next_run(task, after=task.get("last_run_at") or _now())
    if nxt is None:
        nxt = compute_next_run(task)
    task["next_run_at"] = nxt


def _append_history(task: dict, entry: dict, cap: int = 10) -> None:
    h = task.get("history") or []
    h.append(entry)
    task["history"] = h[-cap:]


async def start_scheduler(
    tasks_path: Path,
    run_callback: Callable[[dict], Awaitable[dict]],
    send_notification: Callable[[dict, dict], Awaitable[None]],
    poll_seconds: int = 30,
) -> None:
    """Long-running coroutine. Polls tasks_path every poll_seconds, fires due
    tasks via run_callback, and emits a notification via send_notification."""
    log.info("Scheduler starting (poll every %ss).", poll_seconds)
    while True:
        try:
            await _scheduler_tick(tasks_path, run_callback, send_notification)
        except asyncio.CancelledError:
            log.info("Scheduler stopping.")
            raise
        except Exception as exc:
            log.warning("Scheduler tick failed: %s", exc)
        await asyncio.sleep(poll_seconds)


async def _scheduler_tick(
    tasks_path: Path,
    run_callback: Callable[[dict], Awaitable[dict]],
    send_notification: Callable[[dict, dict], Awaitable[None]],
) -> None:
    tasks = _read_tasks(tasks_path)
    if not tasks:
        return
    now = _now()
    dirty = False
    for task in tasks:
        if not task.get("enabled", True):
            continue
        nxt = task.get("next_run_at")
        if nxt is None:
            _refresh_next_run(task)
            nxt = task.get("next_run_at")
            dirty = True
            if nxt is None:
                continue
        if float(nxt) > now:
            continue
        # Fire it.
        log.info("Firing scheduled task %s (%s).", task.get("id"), task.get("name"))
        try:
            result = await run_callback(task)
            ok = bool(result.get("ok", True))
            summary = (result.get("summary") or "")[:500]
        except Exception as exc:
            ok = False
            summary = f"Run failed: {exc}"
            result = {"ok": False, "summary": summary}
        task["last_run_at"] = now
        _append_history(task, {"ts": now, "ok": ok, "summary": summary})
        sched = task.get("schedule") or {}
        if sched.get("kind") == "once":
            task["enabled"] = False
            task["next_run_at"] = None
        else:
            _refresh_next_run(task)
        dirty = True
        try:
            await send_notification(task, result)
        except Exception as exc:
            log.warning("Notification for %s failed: %s", task.get("id"), exc)
    if dirty:
        _write_tasks(tasks_path, tasks)


# CRUD helpers used by app.py endpoints

def list_tasks(path: Path) -> list[dict]:
    tasks = _read_tasks(path)
    # Make sure each has a next_run_at for the UI countdown.
    for t in tasks:
        if t.get("next_run_at") is None and t.get("enabled", True):
            _refresh_next_run(t)
    return tasks


def upsert_task(path: Path, task: dict) -> dict:
    tasks = _read_tasks(path)
    _refresh_next_run(task)
    found = False
    for i, existing in enumerate(tasks):
        if existing.get("id") == task.get("id"):
            tasks[i] = task
            found = True
            break
    if not found:
        tasks.append(task)
    _write_tasks(path, tasks)
    return task


def delete_task(path: Path, task_id: str) -> bool:
    tasks = _read_tasks(path)
    before = len(tasks)
    tasks = [t for t in tasks if t.get("id") != task_id]
    if len(tasks) != before:
        _write_tasks(path, tasks)
        return True
    return False


def get_task(path: Path, task_id: str) -> Optional[dict]:
    for t in _read_tasks(path):
        if t.get("id") == task_id:
            return t
    return None
