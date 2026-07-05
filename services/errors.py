"""
services/errors.py — structured error envelopes.

Every error the server hands to a client — HTTP error responses and SSE
`error` events alike — uses one consistent JSON shape:

    {"error": {"code": ..., "message": ..., "hint": ..., "request_id": ...}}

`code` is a stable machine-readable slug (e.g. "not_found", "internal_error")
so the frontend can branch on it without regex-matching prose; `message` is
human-readable; `hint` is an optional actionable suggestion; `request_id`
correlates the response with server log lines.
"""
from __future__ import annotations

from typing import Optional

# Stable slugs for the HTTP status codes this app actually raises.
_STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    502: "upstream_error",
    503: "service_unavailable",
    504: "upstream_timeout",
}


def code_for_status(status_code: int) -> str:
    """Map an HTTP status code to a stable machine-readable error code."""
    slug = _STATUS_CODES.get(status_code)
    if slug:
        return slug
    return "internal_error" if status_code >= 500 else "http_error"


def error_envelope(
    code: str,
    message: str,
    hint: Optional[str] = None,
    request_id: Optional[str] = None,
) -> dict:
    """Return the canonical error body. All keys are always present so
    clients never need existence checks — absent values are null."""
    return {
        "error": {
            "code": code,
            "message": message,
            "hint": hint,
            "request_id": request_id,
        }
    }
