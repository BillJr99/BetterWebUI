"""
services/oauth.py - Localhost OAuth 2.0 callback flow for Google and Microsoft.

Exposes two functions the FastAPI app imports:
  start_oauth_flow(provider, config) -> str (authorization URL to open in browser)
  get_oauth_token(provider, data_dir) -> dict|None (current token dict or None)

Tokens are stored as JSON at <data_dir>/oauth/<provider>.json with 0600 perms.
The in-process HTTP callback server runs only during the connect flow (transient),
listening on OAUTH_CALLBACK_PORT (default 8889 to avoid clashing with BetterWebUI).

Supported providers: "google", "microsoft"
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger("betterwebui.oauth")

OAUTH_CALLBACK_PORT = int(os.environ.get("OAUTH_CALLBACK_PORT", "8889"))
OAUTH_CALLBACK_PATH = "/oauth/callback"
OAUTH_REDIRECT_URI = f"http://localhost:{OAUTH_CALLBACK_PORT}{OAUTH_CALLBACK_PATH}"

# ---------------------------------------------------------------------------
# Provider configs
# ---------------------------------------------------------------------------

_PROVIDER_META = {
    "google": {
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scopes": [
            "openid",
            "email",
            "profile",
            "https://www.googleapis.com/auth/calendar.readonly",
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
        ],
        "client_id_key": "google_client_id",
        "client_secret_key": "google_client_secret",
    },
    "microsoft": {
        "auth_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "scopes": [
            "offline_access",
            "openid",
            "profile",
            "email",
            "Calendars.Read",
            "Mail.Read",
            "Files.Read",
            "Sites.Read.All",
        ],
        "client_id_key": "microsoft_client_id",
        "client_secret_key": "microsoft_client_secret",
    },
}

# ---------------------------------------------------------------------------
# Token storage
# ---------------------------------------------------------------------------

def _token_path(provider: str, data_dir: Path) -> Path:
    oauth_dir = data_dir / "oauth"
    oauth_dir.mkdir(parents=True, exist_ok=True)
    return oauth_dir / f"{provider}.json"


def _write_token(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except Exception:
        pass


def get_oauth_token(provider: str, data_dir: Path) -> Optional[dict]:
    """Return stored token dict, refreshing if expired, or None if not connected."""
    path = _token_path(provider, data_dir)
    if not path.exists():
        return None
    try:
        tok = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not tok.get("access_token"):
        return None
    expires_at = tok.get("expires_at", 0)
    if time.time() < expires_at - 60:
        return tok
    # Try to refresh
    refresh = tok.get("refresh_token")
    if not refresh:
        return tok  # Return stale; let callers deal with it
    meta = _PROVIDER_META.get(provider)
    if not meta:
        return tok
    client_id = tok.get("client_id", "")
    client_secret = tok.get("client_secret", "")
    if not client_id:
        return tok
    try:
        resp = httpx.post(
            meta["token_url"],
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=15.0,
        )
        if resp.status_code == 200:
            new = resp.json()
            tok["access_token"] = new["access_token"]
            tok["expires_at"] = time.time() + int(new.get("expires_in", 3600))
            if new.get("refresh_token"):
                tok["refresh_token"] = new["refresh_token"]
            _write_token(path, tok)
    except Exception as exc:
        log.warning("Token refresh failed for %s: %s", provider, exc)
    return tok


def get_oauth_status(provider: str, data_dir: Path) -> dict:
    """Return {connected: bool, email: str, expires_at: int, expired: bool}."""
    tok = get_oauth_token(provider, data_dir)
    if not tok:
        return {"connected": False}
    expired = time.time() >= tok.get("expires_at", 0) - 60
    return {
        "connected": True,
        "email": tok.get("email", ""),
        "expires_at": tok.get("expires_at", 0),
        "expired": expired,
    }


# ---------------------------------------------------------------------------
# Authorization flow
# ---------------------------------------------------------------------------

# Pending state: state_token -> {provider, code_verifier, data_dir, config, future}
_pending: dict[str, dict] = {}


async def start_oauth_flow(provider: str, config: dict, data_dir: Path) -> str:
    """Build and return the authorization URL. Starts the callback server."""
    meta = _PROVIDER_META.get(provider)
    if not meta:
        raise ValueError(f"Unknown OAuth provider: {provider}")
    client_id = config.get(meta["client_id_key"], "")
    if not client_id:
        raise ValueError(
            f"No client_id configured for {provider}. "
            f"Add '{meta['client_id_key']}' in Settings."
        )
    state = uuid.uuid4().hex
    params = {
        "client_id": client_id,
        "redirect_uri": OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(meta["scopes"]),
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = meta["auth_url"] + "?" + urllib.parse.urlencode(params)
    loop = asyncio.get_event_loop()
    fut: asyncio.Future = loop.create_future()
    _pending[state] = {
        "provider": provider,
        "data_dir": data_dir,
        "config": config,
        "future": fut,
    }
    # Ensure callback server is running
    _ensure_callback_server()
    return auth_url


async def wait_for_oauth_callback(state: str, timeout: float = 120.0) -> dict:
    """Wait for the OAuth callback to complete (up to timeout seconds)."""
    entry = _pending.get(state)
    if not entry:
        raise ValueError("Unknown OAuth state token.")
    try:
        result = await asyncio.wait_for(entry["future"], timeout=timeout)
        return result
    finally:
        _pending.pop(state, None)


# ---------------------------------------------------------------------------
# Callback HTTP server (minimal asyncio server, no extra deps)
# ---------------------------------------------------------------------------

_server_state: dict = {"server": None}


def _ensure_callback_server() -> None:
    if _server_state["server"] is not None:
        return
    loop = asyncio.get_event_loop()
    loop.create_task(_run_callback_server())


async def _run_callback_server() -> None:
    try:
        srv = await asyncio.start_server(
            _handle_callback_connection, "127.0.0.1", OAUTH_CALLBACK_PORT
        )
        _server_state["server"] = srv
        async with srv:
            await srv.serve_forever()
    except Exception as exc:
        log.error("OAuth callback server failed: %s", exc)
        _server_state["server"] = None


async def _handle_callback_connection(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    try:
        data = await asyncio.wait_for(reader.read(8192), timeout=10.0)
        request_line = data.decode("utf-8", errors="replace").split("\r\n")[0]
        # "GET /oauth/callback?code=...&state=... HTTP/1.1"
        parts = request_line.split(" ")
        if len(parts) < 2:
            _send_http(writer, 400, "Bad request")
            return
        path = parts[1]
        parsed = urllib.parse.urlparse(path)
        qs = urllib.parse.parse_qs(parsed.query)
        code = (qs.get("code") or [""])[0]
        state = (qs.get("state") or [""])[0]
        error = (qs.get("error") or [""])[0]

        if error or not code or not state:
            _send_http(writer, 400, _html_page("Connection failed", f"OAuth error: {error or 'missing code'}"))
            if state in _pending:
                entry = _pending.pop(state)
                if not entry["future"].done():
                    entry["future"].set_exception(RuntimeError(f"OAuth error: {error}"))
            return

        entry = _pending.get(state)
        if not entry:
            _send_http(writer, 400, _html_page("Connection failed", "Unknown state. Try connecting again."))
            return

        # Exchange code for tokens
        try:
            tok = await _exchange_code(code, entry["provider"], entry["config"])
            tok["client_id"] = entry["config"].get(
                _PROVIDER_META[entry["provider"]]["client_id_key"], ""
            )
            tok["client_secret"] = entry["config"].get(
                _PROVIDER_META[entry["provider"]]["client_secret_key"], ""
            )
            path_out = _token_path(entry["provider"], entry["data_dir"])
            _write_token(path_out, tok)
            _send_http(writer, 200, _html_page(
                "Connected!",
                "You can close this tab and return to BetterWebUI.",
            ))
            if not entry["future"].done():
                entry["future"].set_result(tok)
        except Exception as exc:
            log.error("Token exchange failed: %s", exc)
            _send_http(writer, 500, _html_page("Connection failed", str(exc)))
            if not entry["future"].done():
                entry["future"].set_exception(exc)
    finally:
        writer.close()


def _send_http(writer: asyncio.StreamWriter, status: int, body: str) -> None:
    body_bytes = body.encode("utf-8")
    headers = (
        f"HTTP/1.1 {status} {'OK' if status == 200 else 'Error'}\r\n"
        f"Content-Type: text/html; charset=utf-8\r\n"
        f"Content-Length: {len(body_bytes)}\r\n"
        f"Connection: close\r\n\r\n"
    )
    writer.write(headers.encode() + body_bytes)


def _html_page(title: str, body: str) -> str:
    return (
        f"<!doctype html><html><head><title>{title}</title>"
        f"<style>body{{font-family:sans-serif;padding:2em;max-width:480px;margin:auto}}"
        f"h1{{color:#2a7}}p{{color:#444}}</style></head>"
        f"<body><h1>{title}</h1><p>{body}</p></body></html>"
    )


async def _exchange_code(code: str, provider: str, config: dict) -> dict:
    meta = _PROVIDER_META[provider]
    client_id = config.get(meta["client_id_key"], "")
    client_secret = config.get(meta["client_secret_key"], "")
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            meta["token_url"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": OAUTH_REDIRECT_URI,
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Token exchange failed ({resp.status_code}): {resp.text[:300]}")
    body = resp.json()
    tok = {
        "access_token": body.get("access_token", ""),
        "refresh_token": body.get("refresh_token", ""),
        "expires_at": time.time() + int(body.get("expires_in", 3600)),
        "token_type": body.get("token_type", "Bearer"),
    }
    # Try to extract email from id_token (Google JWT) or userinfo
    id_token = body.get("id_token", "")
    if id_token:
        try:
            parts = id_token.split(".")
            if len(parts) >= 2:
                padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
                import base64 as _b64
                payload = json.loads(_b64.urlsafe_b64decode(padded))
                tok["email"] = payload.get("email", "")
        except Exception:
            pass
    return tok


def revoke_oauth_token(provider: str, data_dir: Path) -> bool:
    """Delete the stored token. Returns True if a token was deleted."""
    path = _token_path(provider, data_dir)
    if path.exists():
        path.unlink()
        return True
    return False
