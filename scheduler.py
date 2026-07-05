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


# Serialises read-modify-write cycles on the tasks file. Invariants:
#
#   1. The CRUD helpers at the bottom of this module (list_tasks / upsert_task
#      / delete_task / get_task) are synchronous and await-free, and every
#      route handler in app.py is async — so the event loop already runs each
#      helper atomically with respect to every other coroutine. They therefore
#      do not need this lock WHILE THAT STAYS TRUE. If one of them ever awaits
#      between its _read_tasks() and _write_tasks() (e.g. aiofiles), it must
#      take _TASKS_LOCK around the whole read-modify-write.
#
#   2. The scheduler tick DOES await (run_callback can run for minutes)
#      between reading the task list and persisting a task's outcome. Request
#      handlers can add/edit/delete tasks during that window, so the tick must
#      never write back its pre-await snapshot — it re-reads the file and
#      merges the outcome into the live task by id, under this lock (see
#      _scheduler_tick).
_TASKS_LOCK = asyncio.Lock()


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
        except Exception:
            log.exception("Scheduler tick failed")
        await asyncio.sleep(poll_seconds)


async def _scheduler_tick(
    tasks_path: Path,
    run_callback: Callable[[dict], Awaitable[dict]],
    send_notification: Callable[[dict, dict], Awaitable[None]],
) -> None:
    # Phase 1 — decide what is due. Read, backfill missing next_run_at, and
    # persist the backfill in one locked section with no awaits inside, so
    # concurrent CRUD from request handlers cannot interleave with it.
    async with _TASKS_LOCK:
        tasks = _read_tasks(tasks_path)
        if not tasks:
            return
        now = _now()
        due: list[dict] = []
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
            due.append(task)
        if dirty:
            _write_tasks(tasks_path, tasks)

    # Phase 2 — fire each due task, then persist its outcome. run_callback can
    # take minutes, and /api/scheduled-tasks handlers may have added, edited,
    # or deleted tasks in the meantime; writing back the pre-run snapshot
    # would silently revert those changes. So after each run we RE-READ the
    # file and merge the outcome into the live version of the task by id.
    for task in due:
        log.info("Firing scheduled task %s (%s).", task.get("id"), task.get("name"))
        try:
            result = await run_callback(task)
            ok = bool(result.get("ok", True))
            summary = (result.get("summary") or "")[:500]
        except Exception as exc:
            ok = False
            summary = f"Run failed: {exc}"
            result = {"ok": False, "summary": summary}

        async with _TASKS_LOCK:
            current = _read_tasks(tasks_path)
            live = next((t for t in current if t.get("id") == task.get("id")), None)
            if live is None:
                # Deleted while it ran — do not resurrect it.
                log.info("Scheduled task %s was deleted mid-run; outcome not persisted.", task.get("id"))
            else:
                live["last_run_at"] = now
                _append_history(live, {"ts": now, "ok": ok, "summary": summary})
                sched = live.get("schedule") or {}
                if sched.get("kind") == "once":
                    live["enabled"] = False
                    live["next_run_at"] = None
                else:
                    _refresh_next_run(live)
                _write_tasks(tasks_path, current)

        try:
            await send_notification(task, result)
        except Exception as exc:
            log.warning("Notification for %s failed: %s", task.get("id"), exc)


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
