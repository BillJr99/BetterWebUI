"""Transient (per-chat, TTL-swept) upload storage helpers
(Phase 3 extraction from app.py).
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path

from . import storage

# --- Transient uploads (per-chat, TTL-swept). Used for file bundles that
# live in browser IndexedDB and are streamed up only for the duration of
# the chat turn — keeps sensitive bytes off the server long-term.

_TRANSIENT_TTL_SECONDS = 24 * 3600


def _transient_root() -> Path:
    """Resolve the transient-uploads directory lazily from the current
    UPLOADS_DIR. Lazy resolution lets test fixtures rebind UPLOADS_DIR
    without these endpoints pointing at the stale module-load value."""
    root = storage.UPLOADS_DIR / "transient"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _sweep_transient_uploads() -> int:
    """Delete transient-upload chat directories older than the TTL.
    Returns the count of directories removed. Safe to call on a timer."""
    cutoff = time.time() - _TRANSIENT_TTL_SECONDS
    removed = 0
    try:
        for chat_dir in _transient_root().iterdir():
            if not chat_dir.is_dir():
                continue
            try:
                if chat_dir.stat().st_mtime < cutoff:
                    shutil.rmtree(chat_dir, ignore_errors=True)
                    removed += 1
            except Exception:
                continue
    except FileNotFoundError:
        pass
    return removed


async def _transient_sweep_loop() -> None:
    """Background loop: sweep stale transient uploads every hour."""
    while True:
        try:
            removed = _sweep_transient_uploads()
            if removed:
                logging.getLogger("betterwebui.uploads").info(
                    "Swept %d stale transient upload directories.", removed,
                )
        except Exception as exc:
            logging.getLogger("betterwebui.uploads").warning("Sweep failed: %s", exc)
        await asyncio.sleep(3600)
