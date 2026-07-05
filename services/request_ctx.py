"""Per-request correlation id plumbing (Phase 3 extraction from app.py).

The contextvar is set by app.py's request-id middleware for the duration of
each HTTP request (tasks spawned inside a request inherit it), echoed back in
the X-Request-ID response header, embedded in error envelopes, and stamped
onto every log record via RequestIdFilter.
"""
from __future__ import annotations

import contextvars
import logging

from fastapi import Request

request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    """Stamp the current request id onto every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()
        return True


def request_id_of(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    return rid or request_id_ctx.get()

