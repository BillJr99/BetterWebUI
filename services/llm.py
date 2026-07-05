"""Model I/O: chat completion, endpoint discovery, model listing, and
message shaping (Phase 3 extraction from app.py).

`chat_complete` is the canonical implementation; app.py exposes a forwarding
property so `patch("app.chat_complete", ...)` rebinds the name *here*, which
is why every caller in services/ and routers/ must invoke it as
``llm.chat_complete(...)`` (module attribute access at call time) rather than
importing the function object directly.
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

import httpx
from fastapi import HTTPException

from . import storage
from .catalog import ENDPOINT_PROFILES
from .storage import save_json

# When BWUI_TEST_MODE=1 the server trims its system prompt and caps model
# output so the test suite can complete in reasonable CI time without a GPU.
_TEST_MODE = os.environ.get("BWUI_TEST_MODE") == "1"
_TEST_MAX_TOKENS = int(os.environ.get("BWUI_TEST_MAX_TOKENS", "30"))

# Runtime-toggleable mock for UI tests (only active when _TEST_MODE=1).
# Enabled via POST /api/test/mock-chat so the e2e tests (which use a real
# model) can share the same container without being affected.
_mock_chat_enabled: bool = False
_mock_chat_text: str = "Mock response."


def normalize_base_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip().rstrip("/")
    for suffix in ("/api/v1", "/openai/v1", "/api", "/v1", "/openai"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
            break
    return url.rstrip("/")


async def discover_profile(base: str, api_key: str) -> Optional[dict]:
    if not base:
        return None
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        for profile in ENDPOINT_PROFILES:
            try:
                resp = await client.get(f"{base}{profile['models']}", headers=headers)
            except httpx.HTTPError:
                continue
            if resp.status_code != 200:
                continue
            try:
                body = resp.json()
            except (json.JSONDecodeError, ValueError):
                continue
            raw = body.get("data") if isinstance(body, dict) else body
            if isinstance(raw, list) and raw:
                return profile
    return None


def active_profile(config: dict) -> dict:
    profile = config.get("api_profile")
    if isinstance(profile, dict) and "models" in profile:
        return profile
    return ENDPOINT_PROFILES[0]


async def chat_complete(messages: list, model: str, config: dict, chat_id: str = "") -> tuple[str, dict]:
    """Returns (text, usage_dict)."""
    if _TEST_MODE and _mock_chat_enabled:
        last_user = next(
            (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), ""
        )
        if isinstance(last_user, list):
            last_user = " ".join(
                p.get("text", "") for p in last_user if isinstance(p, dict) and p.get("type") == "text"
            )
        if "fenced markdown code block" in last_user or ("Reply with exactly" in last_user and "```" in last_user):
            text = "```\nhello\n```"
        elif "LaTeX" in last_user and ("$E" in last_user or "mc^2" in last_user):
            text = "$E = mc^2$"
        else:
            text = _mock_chat_text
        return text, {"prompt_tokens": 1, "completion_tokens": len(text.split()), "total_tokens": len(text.split()) + 1, "elapsed_ms": 10}
    base = normalize_base_url(config["base_url"])
    profile = active_profile(config)
    headers = {"Authorization": f"Bearer {config.get('api_key', '')}"}
    payload: dict = {"model": model, "messages": messages, "stream": False}
    if chat_id and profile.get("name") == "openwebui":
        payload["chat_id"] = chat_id
    if _TEST_MODE:
        payload["max_tokens"] = _TEST_MAX_TOKENS
    t0 = time.time()
    async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
        resp = await client.post(f"{base}{profile['chat']}", json=payload, headers=headers)
    elapsed_ms = int((time.time() - t0) * 1000)
    if resp.status_code != 200:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"Chat call failed ({profile['name']}): {resp.text[:500]}",
        )
    try:
        body = resp.json()
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=502, detail="Chat endpoint returned non-JSON.")
    try:
        text = body["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError):
        text = json.dumps(body)
    usage = body.get("usage") or {}
    usage["elapsed_ms"] = elapsed_ms
    return text, usage
# ---------------------------------------------------------------------------
# Context-window management
# ---------------------------------------------------------------------------

CONTEXT_TOKEN_LIMIT = 32_000
_CONTEXT_CHAR_BUDGET = int(CONTEXT_TOKEN_LIMIT * 0.80 * 4)


def _count_chars(messages: list) -> int:
    total = 0
    for m in messages:
        c = m.get("content", "")
        if isinstance(c, str):
            total += len(c)
        elif isinstance(c, list):
            for part in c:
                if isinstance(part, dict) and part.get("type") == "text":
                    total += len(part.get("text", ""))
    return total


def trim_to_context(history: list, system_prompt: str) -> tuple[list, int]:
    budget = max(_CONTEXT_CHAR_BUDGET - len(system_prompt), 4_000)
    total = _count_chars(history)
    if total <= budget:
        return history, 0
    trimmed = list(history)
    n_dropped = 0
    while total > budget and len(trimmed) > 2:
        removed = trimmed.pop(0)
        total -= _count_chars([removed])
        n_dropped += 1
    while trimmed and trimmed[0].get("role") != "user":
        total -= _count_chars([trimmed[0]])
        trimmed.pop(0)
        n_dropped += 1
    return trimmed, n_dropped
# ---------------------------------------------------------------------------
# OpenWebUI model listing
# ---------------------------------------------------------------------------

async def fetch_models(config: dict) -> list[dict]:
    base = normalize_base_url(config["base_url"])
    if not base:
        raise HTTPException(400, "Set the OpenWebUI URL first.")
    api_key = config.get("api_key", "")
    profile = config.get("api_profile")
    if not profile:
        profile = await discover_profile(base, api_key)
        if not profile:
            raise HTTPException(
                status_code=502,
                detail="Couldn't find a working API endpoint at that URL.",
            )
        config["api_profile"] = profile
        config["base_url"] = base
        save_json(storage.CONFIG_PATH, config)

    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            resp = await client.get(f"{base}{profile['models']}", headers=headers)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Cannot reach OpenWebUI: {exc}")

    if resp.status_code != 200:
        new_profile = await discover_profile(base, api_key)
        if new_profile and new_profile != profile:
            config["api_profile"] = new_profile
            save_json(storage.CONFIG_PATH, config)
            return await fetch_models(config)
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"OpenWebUI returned {resp.status_code}: {resp.text[:300]}",
        )
    try:
        body = resp.json()
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=502, detail="Got a non-JSON response from the models endpoint.")
    raw = body.get("data") if isinstance(body, dict) else body
    out = []
    for m in raw or []:
        mid = m.get("id") or m.get("name")
        if not mid:
            continue
        out.append({"id": mid, "name": m.get("name") or mid})
    return out


_VALID_ROLES = {"system", "user", "assistant", "function", "tool", "developer"}


def to_openai_messages(history: list, system_prompt: str) -> list:
    out: list[dict] = [{"role": "system", "content": system_prompt}]
    for m in history:
        role = m.get("role", "user")
        if role not in _VALID_ROLES:
            continue
        if role == "tool" and not m.get("tool_call_id"):
            role = "user"
            content = f"[Tool result]\n{m.get('content') or ''}"
            out.append({"role": role, "content": content})
            continue
        content = m.get("content") or ""
        attachments = m.get("attachments") or []
        if attachments and role == "user":
            parts: list[dict] = [{"type": "text", "text": content}] if content else []
            for a in attachments:
                ctype = a.get("content_type") or ""
                url = a.get("url") or ""
                if ctype.startswith("image/"):
                    parts.append({"type": "image_url", "image_url": {"url": url}})
                else:
                    parts.append({"type": "text", "text": f"[Attachment: {a.get('filename') or url}]"})
            out.append({"role": role, "content": parts})
        else:
            out.append({"role": role, "content": content})
    return out
