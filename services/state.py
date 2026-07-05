"""Persist enabled/disabled state for each integrated service.

Concurrency invariants (audited, no lock required today):

- Every helper here is synchronous and await-free, and all callers — the
  /api/services/* route handlers in services/routes.py + app.py's chat tool
  paths, and the background health probes spawned via asyncio.gather in
  services/health.py — are coroutines on the single event loop. Each
  load -> mutate -> save cycle therefore runs to completion without another
  coroutine interleaving, which is what makes the read-modify-write in
  set_enabled() safe without a lock.
- If any helper ever awaits between _load() and _save() (or the file I/O is
  moved to a thread/executor), wrap the read-modify-write in an
  asyncio.Lock — see scheduler._TASKS_LOCK for the pattern.
- The state file is only written through _save(); background tasks only
  *read* (services/health.py checks is_enabled), so readers can at worst see
  a value that is one write stale, never a torn in-memory structure.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_STATE_PATH = Path(os.environ.get("BWUI_DATA_DIR", "data")) / "services_state.json"
_VALID_NAMES = frozenset({"clk", "autogui", "osso"})
_DEFAULTS: dict[str, bool] = {"clk": True, "autogui": True, "osso": True}


def _load() -> dict[str, bool]:
    if _STATE_PATH.exists():
        try:
            raw = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
            return {k: bool(raw.get(k, _DEFAULTS[k])) for k in _DEFAULTS}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(_DEFAULTS)


def _save(updated: dict[str, bool]) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(updated, indent=2), encoding="utf-8")


def is_enabled(name: str) -> bool:
    """Return True if the named service is enabled (enabled by default)."""
    return _load().get(name, True)


def set_enabled(name: str, enabled: bool) -> None:
    """Persist the enabled/disabled state for a service."""
    current = _load()
    current[name] = enabled
    _save(current)


def get_all() -> dict[str, bool]:
    """Return the enabled state for every known service."""
    return _load()
