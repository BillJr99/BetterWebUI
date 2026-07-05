"""Tool execution: shell, file checkpoints, media/web tool calls, the
read-only subagent loop, and the execute_tool dispatcher
(Phase 3 extraction from app.py).

Path constants are read via ``storage.<NAME>`` attribute access and the chat
model via ``llm.chat_complete`` so test fixtures that rebind them through
app.py's forwarding properties are honored at call time.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import platform
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import httpx
from fastapi import HTTPException

from . import llm, storage
from .llm import active_profile, normalize_base_url
from .mcp import mcp_manager
from .prompt_builder import RENDERING_PROTOCOL, extract_tool_call, resolve_active_workspace
from .session import _session_trusted_commands, approvals, file_responses
from .skills import load_skill_content
from .storage import load_cli_tools

logger = logging.getLogger("betterwebui.tools")

# ---------------------------------------------------------------------------
# Shell / OS helpers
# ---------------------------------------------------------------------------

def detect_shell() -> tuple[list[str], str]:
    if platform.system() == "Windows":
        if shutil.which("pwsh"):
            return (["pwsh", "-NoProfile", "-Command"], "PowerShell")
        return (["powershell", "-NoProfile", "-Command"], "PowerShell")
    return (["bash", "-lc"], "bash")


async def run_shell(command: str, timeout: int = 120, cwd: Optional[str] = None) -> dict:
    argv_prefix, shell_name = detect_shell()
    argv = argv_prefix + [command]
    started = time.time()
    effective_cwd = cwd or str(storage.WORKSPACE_DIR)
    # Validate cwd up front so a misconfigured workspace project_root produces
    # a clear error instead of being reported as "Shell not available".
    cwd_path = Path(effective_cwd)
    if not cwd_path.exists() or not cwd_path.is_dir():
        return {
            "shell": shell_name,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Working directory does not exist: {effective_cwd}",
            "duration_ms": int((time.time() - started) * 1000),
        }
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=effective_cwd,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {
                "shell": shell_name,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Command timed out after {timeout}s.",
                "duration_ms": int((time.time() - started) * 1000),
            }
        return {
            "shell": shell_name,
            "exit_code": proc.returncode,
            "stdout": (stdout or b"").decode("utf-8", errors="replace")[:20000],
            "stderr": (stderr or b"").decode("utf-8", errors="replace")[:8000],
            "duration_ms": int((time.time() - started) * 1000),
        }
    except FileNotFoundError as exc:
        return {
            "shell": shell_name,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Shell not available: {exc}",
            "duration_ms": int((time.time() - started) * 1000),
        }


def _slug(text: str, fallback: str = "image") -> str:
    out = "".join(c if c.isalnum() or c in "-_" else "-" for c in (text or "")).strip("-")
    return (out or fallback)[:48]


# ---------------------------------------------------------------------------
# Checkpoint helpers (project file versioning)
# ---------------------------------------------------------------------------

def _ckpt_key(filename: str) -> str:
    """Collision-resistant directory key for a checkpointed filename.

    _slug() collapses punctuation and casing, so distinct filenames could share
    a slug and mix their histories. Use a hash of the normalized relative path
    instead — full content (history mixing risk gone) and bounded length.
    """
    norm = (filename or "file").strip().replace("\\", "/")
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def _checkpoint_file(workspace_id: str, filename: str, content: bytes) -> str:
    """Save a checkpoint snapshot of raw bytes. Returns the checkpoint id.

    Stores as `.bin` so binary files round-trip without UTF-8 replacement.
    Legacy `.txt` snapshots from earlier versions are still readable by
    _get_checkpoint and _list_checkpoints.
    """
    ckpt_dir = storage.CHECKPOINTS_DIR / workspace_id / _ckpt_key(filename)
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    ckpt_id = f"{int(time.time())}_{uuid.uuid4().hex[:6]}"
    ckpt_path = ckpt_dir / f"{ckpt_id}.bin"
    ckpt_path.write_bytes(content)
    return ckpt_id


def _list_checkpoints(workspace_id: str, filename: str) -> list[dict]:
    ckpt_dir = storage.CHECKPOINTS_DIR / workspace_id / _ckpt_key(filename)
    if not ckpt_dir.exists():
        return []
    # Sort by mtime (descending) so the .bin and legacy .txt eras interleave
    # correctly when both exist for the same filename.
    files = list(ckpt_dir.glob("*.bin")) + list(ckpt_dir.glob("*.txt"))
    out = []
    for p in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
        parts = p.stem.split("_", 1)
        ts = int(parts[0]) if parts else 0
        out.append({"id": p.stem, "filename": filename, "saved_at": ts})
    return out


def _get_checkpoint(workspace_id: str, filename: str, ckpt_id: str) -> Optional[bytes]:
    """Return the raw checkpoint bytes, or None if the checkpoint is missing.

    Reads `.bin` first; falls back to legacy `.txt` (UTF-8) for snapshots taken
    before checkpoints became binary-safe.
    """
    base = storage.CHECKPOINTS_DIR / workspace_id / _ckpt_key(filename)
    bin_path = base / f"{ckpt_id}.bin"
    if bin_path.exists():
        return bin_path.read_bytes()
    txt_path = base / f"{ckpt_id}.txt"
    if txt_path.exists():
        return txt_path.read_bytes()
    return None


# ---------------------------------------------------------------------------
# OpenWebUI proxy helpers
# ---------------------------------------------------------------------------

def _sniff_image_mime(raw: bytes) -> Optional[str]:
    """Magic-byte sniff. Returns canonical image mime or None for unrecognised bytes."""
    if len(raw) < 12:
        return None
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    if raw[:2] == b"BM":
        return "image/bmp"
    return None


def validate_image_bytes(raw: bytes, min_bytes: int = 64) -> tuple[bool, str, Optional[str]]:
    """Return (ok, reason, sniffed_mime). Cheap deterministic check used to
    detect broken image renders before they reach the UI as broken-link icons."""
    if not raw:
        return False, "Empty image payload.", None
    if len(raw) < min_bytes:
        return False, f"Image payload too small ({len(raw)} bytes).", None
    mime = _sniff_image_mime(raw)
    if mime is None:
        return False, "Image bytes do not match any known image format.", None
    return True, "", mime


async def call_openwebui_image(prompt: str, size: str, config: dict) -> dict:
    base = normalize_base_url(config["base_url"])
    profile = active_profile(config)
    headers = {"Authorization": f"Bearer {config['api_key']}"}
    payload = {"prompt": prompt, "n": 1, "size": size}
    if config.get("image_model"):
        payload["model"] = config["image_model"]
    async with httpx.AsyncClient(timeout=240.0) as client:
        resp = await client.post(f"{base}{profile['images']}", json=payload, headers=headers)
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=f"Image generation failed: {resp.text[:500]}")
    body = resp.json()
    if isinstance(body, list):
        item = body[0] if body else {}
    else:
        item = (body.get("data") or [{}])[0] if isinstance(body, dict) else {}
    filename = f"{_slug(prompt)}-{uuid.uuid4().hex[:6]}.png"
    if "b64_json" in item:
        try:
            raw = base64.b64decode(item["b64_json"], validate=False)
        except Exception as exc:
            return {"error": f"Image generation returned undecodable base64: {exc}", "prompt": prompt}
        ok, reason, sniffed = validate_image_bytes(raw)
        if not ok:
            return {"error": f"Image generation returned invalid data: {reason}", "prompt": prompt}
        return {
            "filename": filename,
            "mime": sniffed or "image/png",
            "data_b64": item["b64_json"],
            "prompt": prompt,
        }
    if "url" in item:
        async with httpx.AsyncClient(timeout=180.0) as client:
            img_resp = await client.get(item["url"])
        if img_resp.status_code != 200:
            return {"error": f"Could not fetch generated image at {item['url']} (HTTP {img_resp.status_code})."}
        ok, reason, sniffed = validate_image_bytes(img_resp.content)
        if not ok:
            return {"error": f"Image generation returned invalid data: {reason}", "prompt": prompt}
        return {
            "filename": filename,
            "mime": sniffed or img_resp.headers.get("content-type", "image/png"),
            "data_b64": base64.b64encode(img_resp.content).decode("ascii"),
            "prompt": prompt,
            "source_url": item["url"],
        }
    return {"raw": body, "error": "Image generation response had neither b64_json nor url."}


async def call_web_search(query: str, max_results: int, config: dict) -> dict:
    """Dispatch to the configured web-search provider. Returns a dict with
    keys: query, provider, results=[{title, url, snippet}], or {error: ...}.
    """
    web = (config or {}).get("web_search") or {}
    provider = (web.get("provider") or "").lower()
    api_key = web.get("api_key") or ""
    if not provider:
        return {"error": "Web search is not configured. Settings → Connection → Web search."}

    async with httpx.AsyncClient(timeout=20.0) as client:
        if provider == "tavily":
            if not api_key:
                return {"error": "Tavily requires an API key (Settings → Connection → Web search)."}
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic",
                },
            )
            if resp.status_code != 200:
                return {"error": f"Tavily search failed ({resp.status_code}): {resp.text[:300]}"}
            body = resp.json()
            results = [
                {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
                for r in (body.get("results") or [])[:max_results]
            ]
            return {"query": query, "provider": "tavily", "results": results}

        if provider == "brave":
            if not api_key:
                return {"error": "Brave Search requires an API key."}
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": max_results},
                headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
            )
            if resp.status_code != 200:
                return {"error": f"Brave search failed ({resp.status_code}): {resp.text[:300]}"}
            body = resp.json()
            results = [
                {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("description", "")}
                for r in ((body.get("web") or {}).get("results") or [])[:max_results]
            ]
            return {"query": query, "provider": "brave", "results": results}

        if provider == "serpapi":
            if not api_key:
                return {"error": "SerpAPI requires an API key."}
            resp = await client.get(
                "https://serpapi.com/search.json",
                params={"q": query, "engine": "google", "num": max_results, "api_key": api_key},
            )
            if resp.status_code != 200:
                return {"error": f"SerpAPI search failed ({resp.status_code}): {resp.text[:300]}"}
            body = resp.json()
            results = [
                {"title": r.get("title", ""), "url": r.get("link", ""), "snippet": r.get("snippet", "")}
                for r in (body.get("organic_results") or [])[:max_results]
            ]
            return {"query": query, "provider": "serpapi", "results": results}

        if provider == "custom":
            url = web.get("custom_url") or ""
            if not url:
                return {"error": "Custom web search needs a 'custom_url' in settings."}
            headers = {}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            resp = await client.post(url, json={"query": query, "max_results": max_results}, headers=headers)
            if resp.status_code != 200:
                return {"error": f"Custom search failed ({resp.status_code}): {resp.text[:300]}"}
            try:
                body = resp.json()
            except Exception:
                return {"error": "Custom search returned non-JSON."}
            custom_results = body.get("results") if isinstance(body, dict) else body
            if not isinstance(custom_results, list):
                return {"error": "Custom search did not return a 'results' list."}
            return {"query": query, "provider": "custom", "results": custom_results[:max_results]}

        return {"error": f"Unknown web_search provider: {provider}"}


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_HTML_ENTITIES = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&apos;": "'", "&#39;": "'", "&nbsp;": " "}


def _strip_html(raw: str) -> tuple[str, str]:
    """Return (title, body_text) extracted from HTML. Falls back to raw text."""
    title = ""
    title_m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.IGNORECASE | re.DOTALL)
    if title_m:
        title = title_m.group(1).strip()
    # Remove script/style blocks
    raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.IGNORECASE | re.DOTALL)
    # Remove all other tags
    text = _HTML_TAG_RE.sub(" ", raw)
    # Decode common HTML entities
    for ent, ch in _HTML_ENTITIES.items():
        text = text.replace(ent, ch)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    # Clean title entities too
    for ent, ch in _HTML_ENTITIES.items():
        title = title.replace(ent, ch)
    title = _WHITESPACE_RE.sub(" ", title).strip()
    return title, text


async def call_fetch_url(url: str) -> dict:
    """Fetch a URL and return extracted readable text."""
    parsed = None
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return {"error": f"fetch_url only supports http/https URLs (got '{parsed.scheme}')."}
    except Exception:
        return {"error": "Invalid URL."}
    try:
        async with httpx.AsyncClient(
            timeout=20.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 BetterWebUI/1.0"},
        ) as client:
            resp = await client.get(url)
        if resp.status_code >= 400:
            return {"error": f"Server returned {resp.status_code} for {url}."}
        ct = resp.headers.get("content-type", "")
        if "html" in ct:
            title, text = _strip_html(resp.text)
        else:
            title, text = "", resp.text
        max_chars = 12000
        truncated = len(text) > max_chars
        return {
            "url": url,
            "title": title,
            "text": text[:max_chars] + (" …[truncated]" if truncated else ""),
            "word_count": len(text.split()),
        }
    except Exception as exc:
        return {"error": f"Failed to fetch {url}: {exc}"}


async def call_openwebui_audio(text: str, voice: str, config: dict) -> dict:
    base = normalize_base_url(config["base_url"])
    profile = active_profile(config)
    headers = {"Authorization": f"Bearer {config['api_key']}"}
    payload = {"input": text, "voice": voice or "alloy", "model": "tts-1"}
    async with httpx.AsyncClient(timeout=240.0) as client:
        resp = await client.post(f"{base}{profile['audio']}", json=payload, headers=headers)
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Audio generation failed (upstream {resp.status_code}): {resp.text[:500]}")
    filename = f"{_slug(text, 'speech')}-{uuid.uuid4().hex[:6]}.mp3"
    return {
        "filename": filename,
        "mime": "audio/mpeg",
        "data_b64": base64.b64encode(resp.content).decode("ascii"),
        "voice": voice,
    }
# ---------------------------------------------------------------------------
# Subagent execution
# ---------------------------------------------------------------------------

_SUBAGENT_TOOL_PROTOCOL = """
You have one tool. To call it, output exactly one fenced JSON block on its own lines:

```tool
{"tool": "load_skill", "args": {"skill_id": "..."}}
```

Available tools:
- load_skill: load the full content of a named skill. Args: {"skill_id": "..."}.

Output at most one tool call per turn. Never invent tool output — wait for the result.
""".strip()


async def run_subagent_loop(
    prompt: str, model: str, config: dict, max_steps: int = 4
) -> str:
    """Run a read-only sub-loop. Returns final assistant text."""
    sub_system = (
        "You are a read-only research subagent. You may call load_skill only. "
        "Do NOT call execute_shell, write_file, generate_image, generate_audio, "
        "cli_call, mcp_call, or read_file. Summarize from your existing context. "
        "Produce a concise summary of your findings when done.\n\n"
        + _SUBAGENT_TOOL_PROTOCOL
        + "\n\n"
        + RENDERING_PROTOCOL
    )
    history = [{"role": "user", "content": prompt}]
    # Subagents are strictly read-only: mcp_call is blocked entirely because we
    # cannot statically guarantee any given MCP tool is side-effect-free.
    blocked_tools = (
        "execute_shell", "write_file", "generate_image",
        "generate_audio", "cli_call", "mcp_call",
    )
    for _ in range(max_steps):
        messages = [{"role": "system", "content": sub_system}] + history
        text, _ = await llm.chat_complete(messages, model, config)
        history.append({"role": "assistant", "content": text})
        call = extract_tool_call(text)
        if not call:
            return text
        # Only allow read-only tools
        if call["tool"] in blocked_tools:
            history.append({
                "role": "user",
                "content": f"[Tool '{call['tool']}' blocked — subagents are read-only]"
            })
            continue
        if call["tool"] == "read_file":
            result = {"error": "Subagents cannot use the file picker. Summarize from context instead."}
        elif call["tool"] == "load_skill":
            skill = load_skill_content(call["args"].get("skill_id", ""))
            result = skill or {"error": "Skill not found."}
        else:
            result = {"error": f"Unknown tool: {call['tool']}"}
        history.append({
            "role": "user",
            "content": f"[Tool '{call['tool']}' result]\n```json\n{json.dumps(result, indent=2)[:4000]}\n```"
        })
    # Return last assistant turn
    for m in reversed(history):
        if m["role"] == "assistant":
            return m["content"]
    return "(subagent produced no output)"


# ---------------------------------------------------------------------------
# Permission / approval helpers
# ---------------------------------------------------------------------------

def _should_skip_approval(command: str, cli_id: Optional[str], config: dict) -> bool:
    """Return True if this command/CLI can skip the approval dialog."""
    if command in _session_trusted_commands:
        return True
    if cli_id:
        cli_data = load_cli_tools()
        cli = next((c for c in cli_data.get("tools", []) if c["id"] == cli_id), None)
        if cli and cli.get("approval_policy") == "always":
            return True
    workspace = resolve_active_workspace(config)
    if workspace and workspace.get("shell_approval_policy") == "always":
        return True
    return False


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

async def execute_tool(
    call: dict,
    config: dict,
    send_event: Callable[[str, dict], Awaitable[Any]],
    mode: str = "approve-each",
    model: str = "",
) -> dict:
    tool = call["tool"]
    args = call["args"]

    # Plan mode: block side-effecting tools (and spawn_subagent, which can
    # transitively read files/skills but otherwise consumes context budget
    # before any execution has been approved — matches PLAN_MODE_BLOCK's
    # "ONLY call update_task_plan/read_file/load_skill" contract).
    if mode == "plan" and tool in (
        "execute_shell", "write_file", "generate_image",
        "generate_audio", "cli_call", "mcp_call", "spawn_subagent",
    ):
        return {"error": f"Tool '{tool}' is blocked in plan mode. Switch to Approve-each to execute."}

    if tool == "update_task_plan":
        items = args.get("items", [])
        await send_event("task_plan", {"items": items})
        return {"ok": True, "items_count": len(items), "items": items}

    if tool == "spawn_subagent":
        kind = args.get("kind", "explore")
        prompt = args.get("prompt", "")
        items = args.get("items") or []
        model = model or config.get("default_model", "")
        if not model:
            return {"error": "No model available — cannot spawn subagents."}

        # Build per-subagent prompts
        if items and kind == "compare":
            subprompts = [
                f"Investigate and summarize: {item}\n\nContext: {prompt}"
                for item in items[:3]
            ]
        else:
            subprompts = [prompt]

        await send_event("subagent_start", {"kind": kind, "count": len(subprompts)})
        results = await asyncio.gather(
            *[run_subagent_loop(sp, model, config) for sp in subprompts],
            return_exceptions=True,
        )
        texts = []
        for i, r in enumerate(results):
            item_label = items[i] if i < len(items) else f"Task {i+1}"
            if isinstance(r, Exception):
                texts.append(f"**{item_label}**: Error — {r}")
            else:
                texts.append(f"**{item_label}**:\n{r}")
        combined = "\n\n---\n\n".join(texts)
        await send_event("subagent_result", {"kind": kind, "count": len(subprompts), "combined": combined})
        return {"kind": kind, "results_count": len(subprompts), "combined": combined}

    if tool == "execute_shell":
        if not config.get("shell_enabled", True):
            return {"error": "Shell execution is disabled in settings."}
        command = args.get("command", "").strip()
        reason = args.get("reason", "")
        if not command:
            return {"error": "No command provided."}

        if mode == "trusted" or _should_skip_approval(command, None, config):
            await send_event("tool_running", {"tool": "execute_shell", "command": command, "auto_approved": True})
        else:
            aid = approvals.new()
            await send_event("approval_request", {
                "approval_id": aid,
                "tool": "execute_shell",
                "command": command,
                "reason": reason,
                "shell": detect_shell()[1],
            })
            approved = await approvals.wait(aid)
            if not approved:
                return {"error": "User denied this command."}
            await send_event("tool_running", {"tool": "execute_shell", "command": command})

        workspace = resolve_active_workspace(config)
        shell_cwd = _resolve_project_root(workspace)
        result = await run_shell(command, cwd=shell_cwd)
        # Auto-capture plot
        for _img_path in (Path("/tmp/bwui_plot.png"), storage.ROOT / "bwui_plot.png"):
            if _img_path.exists():
                try:
                    result["filename"] = "plot.png"
                    result["mime"] = "image/png"
                    result["data_b64"] = base64.b64encode(_img_path.read_bytes()).decode("ascii")
                    _img_path.unlink()
                except Exception as exc:
                    # Non-fatal: the shell result still goes back to the chat,
                    # just without the inline plot attachment.
                    logger.warning("Auto-capture of plot %s failed: %s", _img_path, exc)
                break
        return result

    if tool == "read_file":
        rid = file_responses.new()
        await send_event("file_request", {
            "request_id": rid,
            "purpose": args.get("reason") or args.get("purpose") or "read",
            "accept": args.get("accept", "*/*"),
            "multiple": bool(args.get("multiple", False)),
        })
        files = await file_responses.wait(rid)
        if not files:
            return {"error": "User cancelled the file picker (no files chosen)."}
        out_files = []
        for f in files:
            entry = {
                "filename": f.get("filename", "file"),
                "content_type": f.get("content_type", ""),
                "size": f.get("size", 0),
            }
            if f.get("content") is not None:
                entry["content"] = (f.get("content") or "")[:80000]
            elif f.get("data_b64"):
                b64 = f["data_b64"]
                entry["data_b64"] = b64[:200_000]
                entry["truncated"] = len(b64) > 200_000
            out_files.append(entry)
        return {"files": out_files}

    if tool == "write_file":
        filename = (args.get("filename") or args.get("path") or "file.txt").strip()
        filename = Path(filename).name or "file.txt"
        content = args.get("content", "")
        mime = args.get("mime", "text/plain")
        if not isinstance(content, str):
            content = str(content)
        # Cap write size so a single tool call can't blow up SSE/JSON payloads
        # (base64 inflates by ~33% on top of UTF-8 encoding).
        _MAX_WRITE_BYTES = 5 * 1024 * 1024
        content_bytes_len = len(content.encode("utf-8"))
        if content_bytes_len > _MAX_WRITE_BYTES:
            return {"error": f"write_file payload too large ({content_bytes_len} bytes; max {_MAX_WRITE_BYTES})."}

        # Determine the write directory: workspace project_root → WORKSPACE_DIR
        workspace = resolve_active_workspace(config)
        project_root = _resolve_project_root(workspace)
        dest = Path(project_root) / filename

        if mode != "trusted":
            aid = approvals.new()
            await send_event("approval_request", {
                "approval_id": aid,
                "tool": "write_file",
                "filename": filename,
                "mime": mime,
                "preview": content[:1000],
                "byte_count": len(content.encode("utf-8")),
                "dest_path": filename,
            })
            approved = await approvals.wait(aid)
            if not approved:
                return {"error": "User denied this file write."}

        # Snapshot the existing file only after approval, so denied requests
        # don't leave junk checkpoints behind on disk. Skip the snapshot
        # entirely if the existing file is larger than the checkpoint cap
        # (2 MB) — checkpoints are an undo helper, not a backup system, and
        # reading multi-hundred-MB files into memory just to checkpoint is
        # worse than degrading gracefully.
        _MAX_CHECKPOINT_BYTES = 2 * 1024 * 1024
        checkpoint_id = None
        if dest.exists():
            wid = (workspace or {}).get("id", "default")
            try:
                existing_size = dest.stat().st_size
                if existing_size <= _MAX_CHECKPOINT_BYTES:
                    # Read raw bytes so binary files round-trip through
                    # checkpoint/revert without lossy UTF-8 replacement.
                    checkpoint_id = _checkpoint_file(
                        wid, filename, dest.read_bytes()
                    )
            except Exception as exc:
                # Non-fatal: the write proceeds, it just won't have an Undo
                # checkpoint. Log so a user asking "where did Undo go?" has
                # a trail.
                logger.warning("Checkpoint of %s failed before write: %s", filename, exc)
                checkpoint_id = None

        # Write to disk
        write_error = None
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
        except Exception as exc:
            write_error = str(exc)

        # Only inline data_b64 on the failure path. When the on-disk write
        # succeeded, the file is already at <project_root>/<filename> and
        # the user can open it from the file-tree pane — we'd just bloat
        # every SSE event with up to 5 MB of base64 (≈6.7 MB JSON) for no
        # gain, and that's fragile behind common reverse proxies.
        # When the write failed (write_error), we still inline so the user
        # can recover the generated bytes via the chat download link.
        result = {
            "filename": filename,
            "dest_path": filename,
            "mime": mime,
            "bytes_written": content_bytes_len,
            "checkpoint_id": checkpoint_id,
        }
        if write_error:
            result["write_error"] = write_error
            result["data_b64"] = base64.b64encode(content.encode("utf-8")).decode("ascii")
        return result

    if tool == "delete_file":
        filename = (args.get("filename") or args.get("path") or "").strip()
        filename = Path(filename).name
        if not filename:
            return {"error": "delete_file requires a 'filename' argument."}
        workspace = resolve_active_workspace(config)
        project_root = _resolve_project_root(workspace)
        dest = Path(project_root) / filename
        if not dest.exists():
            return {"error": f"File '{filename}' not found in the workspace."}
        if not dest.is_file():
            return {"error": f"'{filename}' is not a regular file and cannot be deleted with this tool."}

        if mode != "trusted":
            aid = approvals.new()
            await send_event("approval_request", {
                "approval_id": aid,
                "tool": "delete_file",
                "filename": filename,
                "dest_path": filename,
                "reason": args.get("reason", ""),
            })
            approved = await approvals.wait(aid)
            if not approved:
                return {"error": "User denied file deletion."}

        try:
            dest.unlink()
        except OSError as exc:
            return {"error": f"Could not delete '{filename}': {exc}"}
        return {"deleted": filename, "path": filename}

    if tool == "load_skill":
        skill_id = args.get("skill_id", "")
        skill = load_skill_content(skill_id)
        if not skill:
            return {"error": f"Skill '{skill_id}' not found."}
        return skill

    if tool == "generate_image":
        try:
            return await call_openwebui_image(args.get("prompt", ""), args.get("size", "1024x1024"), config)
        except HTTPException as exc:
            return {"error": exc.detail}

    if tool == "generate_audio":
        try:
            return await call_openwebui_audio(
                args.get("text", ""),
                args.get("voice") or config.get("tts_voice", "alloy"),
                config,
            )
        except HTTPException as exc:
            return {"error": exc.detail}

    if tool == "mcp_call":
        server = args.get("server", "")
        name = args.get("name", "")
        arguments = args.get("arguments") or {}
        if not server or not name:
            return {"error": "mcp_call requires both 'server' and 'name'."}
        return await mcp_manager.call(server, name, arguments)

    if tool == "web_search":
        query = (args.get("query") or "").strip()
        if not query:
            return {"error": "web_search requires a 'query' argument."}
        try:
            max_results = int(args.get("max_results") or 5)
        except Exception:
            max_results = 5
        max_results = max(1, min(10, max_results))
        try:
            return await call_web_search(query, max_results, config)
        except HTTPException as exc:
            return {"error": exc.detail}

    if tool == "fetch_url":
        url = (args.get("url") or "").strip()
        if not url:
            return {"error": "fetch_url requires a 'url' argument."}
        if mode != "trusted":
            aid = approvals.new()
            await send_event("approval_request", {
                "approval_id": aid,
                "tool": "fetch_url",
                "command": f"Fetch: {url}",
                "reason": "The assistant wants to download the contents of a web page.",
                "shell": "",
            })
            approved = await approvals.wait(aid)
            if not approved:
                return {"error": "User denied fetch_url."}
        await send_event("tool_running", {"tool": "fetch_url", "url": url})
        return await call_fetch_url(url)

    if tool == "cli_call":
        if not config.get("shell_enabled", True):
            return {"error": "Shell execution is disabled in settings."}
        cli_id = args.get("id", "")
        cli_args = args.get("args", "")
        cli_data = load_cli_tools()
        cli = next((c for c in cli_data.get("tools", []) if c["id"] == cli_id), None)
        if not cli:
            return {"error": f"CLI shortcut '{cli_id}' is not configured."}
        template = cli.get("command_template", "{args}")
        command = template.replace("{args}", cli_args)
        workspace = resolve_active_workspace(config)

        if mode == "trusted" or _should_skip_approval(command, cli_id, config):
            await send_event("tool_running", {"tool": "cli_call", "command": command, "auto_approved": True})
        else:
            aid = approvals.new()
            await send_event("approval_request", {
                "approval_id": aid,
                "tool": "execute_shell",
                "command": command,
                "reason": f"CLI shortcut '{cli_id}': {cli.get('description', '')}",
                "shell": detect_shell()[1],
                "cli_id": cli_id,
            })
            approved = await approvals.wait(aid)
            if not approved:
                return {"error": "User denied this command."}
            await send_event("tool_running", {"tool": "cli_call", "command": command})

        # Run CLI shortcuts from the workspace project_root, mirroring
        # execute_shell so commands that assume they run inside the project
        # folder (e.g., pandoc on input/*.md) behave consistently.
        cli_cwd = _resolve_project_root(workspace)
        return await run_shell(command, cwd=cli_cwd)

    # ── Service tool calls ────────────────────────────────────────────────────

    if tool == "clk_research":
        import httpx as _httpx

        from services import state as svc_state
        from services.clients import get_clk_client
        if not svc_state.is_enabled("clk"):
            return {"error": "CognitiveLoopKernel is disabled. Enable it in Settings > Services."}
        command = args.get("command", "run")
        workflow = args.get("workflow", "")
        summary = f"CLK research — workflow: {workflow or 'default'}, command: {command}"
        if mode != "trusted":
            aid = approvals.new()
            await send_event("approval_request", {
                "approval_id": aid,
                "tool": "clk_research",
                "command": summary,
                "reason": "CognitiveLoopKernel will start a research task.",
            })
            approved = await approvals.wait(aid)
            if not approved:
                return {"error": "User denied CognitiveLoopKernel research task."}
        await send_event("tool_running", {"tool": "clk_research", "command": summary})
        try:
            client = get_clk_client()
            return await client.start_research(
                command=command,
                args=args.get("args", []),
                workspace_id=args.get("workspace_id"),
                workflow=workflow or None,
            )
        except (_httpx.ConnectError, _httpx.TimeoutException, _httpx.TransportError) as e:
            return {"error": f"CognitiveLoopKernel is enabled but could not be reached. ({e})"}

    if tool == "autogui_task":
        import httpx as _httpx

        from services import state as svc_state
        from services.clients import get_autogui_client
        if not svc_state.is_enabled("autogui"):
            return {"error": "AutoGUI is disabled. Enable it in Settings > Services."}
        task_desc = args.get("task") or ""
        if not task_desc.strip():
            return {"error": "autogui_task requires a non-empty 'task' argument. "
                    "Please call the tool again with {\"task\": \"description of what to do\", \"dry_run\": false}."}
        dry_run = args.get("dry_run") or False
        summary = f"AutoGUI task: {task_desc[:120]}" + (" [dry run]" if dry_run else "")
        if mode != "trusted":
            aid = approvals.new()
            await send_event("approval_request", {
                "approval_id": aid,
                "tool": "autogui_task",
                "command": summary,
                "reason": "AutoGUI will control the desktop GUI to complete this task.",
            })
            approved = await approvals.wait(aid)
            if not approved:
                return {"error": "User denied AutoGUI desktop task."}
        await send_event("tool_running", {"tool": "autogui_task", "command": summary})
        try:
            agui_client = get_autogui_client()
            return await agui_client.start_task(task=task_desc, model=model or None, dry_run=dry_run)
        except (_httpx.ConnectError, _httpx.TimeoutException, _httpx.TransportError) as e:
            return {"error": f"AutoGUI is enabled but could not be reached. ({e})"}

    if tool == "screen_windows":
        import httpx as _httpx

        from services import state as svc_state
        from services.clients import get_osso_client
        if not svc_state.is_enabled("osso"):
            return {"error": "OSScreenObserver is disabled. Enable it in Settings > Services."}
        try:
            return await get_osso_client().windows()
        except (_httpx.ConnectError, _httpx.TimeoutException, _httpx.TransportError) as e:
            return {"error": f"OSScreenObserver is enabled but could not be reached. ({e})"}

    if tool == "screen_description":
        import httpx as _httpx

        from services import state as svc_state
        from services.clients import get_osso_client
        if not svc_state.is_enabled("osso"):
            return {"error": "OSScreenObserver is disabled. Enable it in Settings > Services."}
        try:
            return await get_osso_client().description(
                window_index=args.get("window_index"),
                mode=args.get("mode", "accessibility"),
            )
        except (_httpx.ConnectError, _httpx.TimeoutException, _httpx.TransportError) as e:
            return {"error": f"OSScreenObserver is enabled but could not be reached. ({e})"}

    if tool == "screen_screenshot":
        import httpx as _httpx

        from services import state as svc_state
        from services.clients import get_osso_client
        if not svc_state.is_enabled("osso"):
            return {"error": "OSScreenObserver is disabled. Enable it in Settings > Services."}
        try:
            return await get_osso_client().screenshot(window_index=args.get("window_index"))
        except (_httpx.ConnectError, _httpx.TimeoutException, _httpx.TransportError) as e:
            return {"error": f"OSScreenObserver is enabled but could not be reached. ({e})"}

    if tool == "screen_action":
        import httpx as _httpx

        from services import state as svc_state
        from services.clients import get_osso_client
        if not svc_state.is_enabled("osso"):
            return {"error": "OSScreenObserver is disabled. Enable it in Settings > Services."}
        action_type = args.get("action", "")
        summary = f"screen_{action_type}" + (f" at ({args.get('x')}, {args.get('y')})" if "x" in args else "")
        if mode != "trusted":
            aid = approvals.new()
            await send_event("approval_request", {
                "approval_id": aid,
                "tool": "screen_action",
                "command": summary,
                "reason": "OSScreenObserver will perform an action on the screen.",
            })
            approved = await approvals.wait(aid)
            if not approved:
                return {"error": "User denied screen action."}
        await send_event("tool_running", {"tool": "screen_action", "command": summary})
        try:
            return await get_osso_client().action(args)
        except (_httpx.ConnectError, _httpx.TimeoutException, _httpx.TransportError) as e:
            return {"error": f"OSScreenObserver is enabled but could not be reached. ({e})"}

    return {"error": f"Unknown tool: {tool}"}

def _resolve_project_root(workspace: Optional[dict]) -> str:
    """Resolve a workspace's project root, restricting it to live under
    WORKSPACE_DIR. This prevents an unauthenticated caller (via /api/workspaces)
    from pointing a workspace at '/' or another sensitive directory and using
    the project file APIs to browse the host filesystem."""
    root, _clamped = _resolve_project_root_info(workspace)
    return root


def _resolve_project_root_info(workspace: Optional[dict]) -> tuple[str, bool]:
    """Same resolution as _resolve_project_root but also reports whether the
    stored project_root had to be clamped to WORKSPACE_DIR.

    Returns (effective_root, clamped). `clamped=True` means the caller set an
    out-of-bounds project_root that we silently coerced — useful for UI hints
    that distinguish "no project root configured" from "configured but invalid"
    even when the user intentionally pointed project_root at WORKSPACE_DIR
    itself (in which case clamped is False).
    """
    requested = (workspace or {}).get("project_root")
    base = Path(storage.WORKSPACE_DIR).resolve()
    if not requested:
        return str(base), False
    try:
        candidate = Path(requested)
        # Resolve relative paths against WORKSPACE_DIR (not the process CWD),
        # matching the validation logic in upsert_workspace.
        if not candidate.is_absolute():
            candidate = base / candidate
        candidate = candidate.resolve()
        candidate.relative_to(base)
        return str(candidate), False
    except (ValueError, OSError):
        return str(base), True
