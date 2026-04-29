"""
BetterWebUI — a friendlier OpenWebUI front-end with skills, custom system
prompts, multimodal generation, MCP-style tooling, and gated shell execution.
"""

import asyncio
import base64
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

import aiofiles
import frontmatter
import httpx
from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).parent.resolve()
DATA_DIR = ROOT / "data"
SKILLS_DIR = ROOT / "skills"
UPLOADS_DIR = DATA_DIR / "uploads"
CONFIG_PATH = DATA_DIR / "config.json"
PROMPTS_PATH = DATA_DIR / "system_prompts.json"
CONVERSATIONS_PATH = DATA_DIR / "conversations.json"
WORKSPACES_PATH = DATA_DIR / "workspaces.json"
MCP_PATH = DATA_DIR / "mcp_servers.json"
CLI_PATH = DATA_DIR / "cli_tools.json"

for d in (DATA_DIR, SKILLS_DIR, UPLOADS_DIR):
    d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_config() -> dict:
    return load_json(
        CONFIG_PATH,
        {
            "base_url": "http://localhost:3000",
            "api_key": "",
            "default_model": "",
            "image_model": "",
            "tts_voice": "alloy",
            "active_prompt_id": "",
            "active_skills": [],
            "active_workspace_id": "",
            "auto_approve_safe": True,
            "shell_enabled": True,
            "api_profile": None,
        },
    )


# ---------------------------------------------------------------------------
# OpenWebUI endpoint discovery
#
# Different OpenWebUI versions and proxy configurations expose the
# OpenAI-compatible API at different prefixes. We probe known profiles and
# cache the first one that returns a sensible models list.
# ---------------------------------------------------------------------------

ENDPOINT_PROFILES: list[dict] = [
    {
        "name": "openwebui",
        "label": "OpenWebUI native",
        "models": "/api/models",
        "chat": "/api/chat/completions",
        "images": "/api/v1/images/generations",
        "audio": "/api/v1/audio/speech",
    },
    {
        "name": "openwebui-openai",
        "label": "OpenWebUI OpenAI proxy",
        "models": "/openai/v1/models",
        "chat": "/openai/v1/chat/completions",
        "images": "/openai/v1/images/generations",
        "audio": "/openai/v1/audio/speech",
    },
    {
        "name": "openai-v1",
        "label": "OpenAI-compatible (/v1)",
        "models": "/v1/models",
        "chat": "/v1/chat/completions",
        "images": "/v1/images/generations",
        "audio": "/v1/audio/speech",
    },
    {
        "name": "api-v1",
        "label": "API v1 (/api/v1)",
        "models": "/api/v1/models",
        "chat": "/api/v1/chat/completions",
        "images": "/api/v1/images/generations",
        "audio": "/api/v1/audio/speech",
    },
]


def normalize_base_url(url: str) -> str:
    """Strip trailing slashes and common API path suffixes the user may have
    pasted in. We want just the host root."""
    if not url:
        return ""
    url = url.strip().rstrip("/")
    # Order matters: longest matches first.
    for suffix in ("/api/v1", "/openai/v1", "/api", "/v1", "/openai"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
            break
    return url.rstrip("/")


async def discover_profile(base: str, api_key: str) -> Optional[dict]:
    """Probe each known profile and return the first that yields a list of
    models. Returns None if nothing works."""
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
    """Return the cached profile, or fall back to the OpenWebUI native one."""
    profile = config.get("api_profile")
    if isinstance(profile, dict) and "models" in profile:
        return profile
    return ENDPOINT_PROFILES[0]


def load_prompts() -> dict:
    return load_json(
        PROMPTS_PATH,
        {
            "prompts": [
                {
                    "id": "default",
                    "name": "Helpful Assistant",
                    "content": (
                        "You are a helpful, friendly assistant for a faculty "
                        "member in higher education. Be clear, concise, and "
                        "patient. When asked to do something on their computer, "
                        "use available tools."
                    ),
                }
            ]
        },
    )


def load_conversations() -> dict:
    return load_json(CONVERSATIONS_PATH, {"conversations": {}})


def load_workspaces() -> dict:
    return load_json(WORKSPACES_PATH, {"workspaces": []})


def load_mcp_servers() -> dict:
    return load_json(MCP_PATH, {"servers": []})


def load_cli_tools() -> dict:
    return load_json(CLI_PATH, {"tools": []})


# ---------------------------------------------------------------------------
# MCP server registry (well-known servers a user can install with one click).
# Most run via npx (Node) or uvx (Python via uv). The launcher checks for these
# at runtime; we surface clear errors if they're missing.
# ---------------------------------------------------------------------------

MCP_REGISTRY: list[dict] = [
    {
        "id": "filesystem",
        "name": "Filesystem",
        "description": "Read and write files within a chosen directory.",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem",
        "command": "npx",
        "args_template": ["-y", "@modelcontextprotocol/server-filesystem", "{root_path}"],
        "fields": [
            {"name": "root_path", "label": "Root directory the assistant may access", "type": "path"}
        ],
        "requires": "Node.js (npm/npx).",
    },
    {
        "id": "github",
        "name": "GitHub",
        "description": "Browse repositories, search code, manage issues and pull requests.",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/github",
        "command": "npx",
        "args_template": ["-y", "@modelcontextprotocol/server-github"],
        "env_template": {"GITHUB_PERSONAL_ACCESS_TOKEN": "{token}"},
        "fields": [
            {"name": "token", "label": "GitHub personal access token", "type": "password"}
        ],
        "requires": "Node.js (npm/npx) and a GitHub PAT.",
    },
    {
        "id": "fetch",
        "name": "Fetch",
        "description": "Retrieve and convert web pages into structured text the assistant can read.",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/fetch",
        "command": "uvx",
        "args_template": ["mcp-server-fetch"],
        "fields": [],
        "requires": "Python with uv installed (https://docs.astral.sh/uv/).",
    },
    {
        "id": "brave-search",
        "name": "Brave Search",
        "description": "Search the web via Brave's API.",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/brave-search",
        "command": "npx",
        "args_template": ["-y", "@modelcontextprotocol/server-brave-search"],
        "env_template": {"BRAVE_API_KEY": "{api_key}"},
        "fields": [
            {"name": "api_key", "label": "Brave API key", "type": "password"}
        ],
        "requires": "Node.js plus a Brave Search API key.",
    },
    {
        "id": "memory",
        "name": "Memory",
        "description": "A persistent knowledge graph the assistant can read from and write to across chats.",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/memory",
        "command": "npx",
        "args_template": ["-y", "@modelcontextprotocol/server-memory"],
        "fields": [],
        "requires": "Node.js (npm/npx).",
    },
    {
        "id": "git",
        "name": "Git",
        "description": "Read and search a local Git repository's history and contents.",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/git",
        "command": "uvx",
        "args_template": ["mcp-server-git", "--repository", "{repo_path}"],
        "fields": [
            {"name": "repo_path", "label": "Path to a Git repository", "type": "path"}
        ],
        "requires": "Python with uv installed.",
    },
    {
        "id": "sequential-thinking",
        "name": "Sequential Thinking",
        "description": "Lets the assistant break problems into stepped thoughts before answering.",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking",
        "command": "npx",
        "args_template": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
        "fields": [],
        "requires": "Node.js (npm/npx).",
    },
    {
        "id": "time",
        "name": "Time",
        "description": "Provides accurate current time and timezone conversion.",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/time",
        "command": "uvx",
        "args_template": ["mcp-server-time"],
        "fields": [],
        "requires": "Python with uv installed.",
    },
]


# ---------------------------------------------------------------------------
# CLI shortcuts registry — common command-line tools the user can register so
# the assistant knows they're available and how to call them. The model invokes
# them via the cli_call tool, which routes through execute_shell with approval.
# ---------------------------------------------------------------------------

CLI_REGISTRY: list[dict] = [
    {
        "id": "git",
        "name": "git",
        "description": "Version control. Show status, diff, log, and history.",
        "command_template": "git {args}",
        "examples": ["git status", "git log --oneline -20", "git diff"],
    },
    {
        "id": "gh",
        "name": "GitHub CLI",
        "description": "Operate on GitHub repos, PRs, and issues from the terminal.",
        "command_template": "gh {args}",
        "examples": ["gh pr list", "gh issue view 123"],
    },
    {
        "id": "pandoc",
        "name": "pandoc",
        "description": "Convert documents between formats — markdown, docx, pdf, html, latex.",
        "command_template": "pandoc {args}",
        "examples": ["pandoc input.md -o output.docx", "pandoc paper.docx -o paper.pdf"],
    },
    {
        "id": "ffmpeg",
        "name": "ffmpeg",
        "description": "Convert and process audio/video files.",
        "command_template": "ffmpeg {args}",
        "examples": ["ffmpeg -i talk.mov -vn talk.mp3"],
    },
    {
        "id": "yt-dlp",
        "name": "yt-dlp",
        "description": "Download videos and audio from sites like YouTube, Vimeo, etc.",
        "command_template": "yt-dlp {args}",
        "examples": ["yt-dlp -x --audio-format mp3 'URL'"],
    },
    {
        "id": "sqlite3",
        "name": "sqlite3",
        "description": "Inspect and query SQLite databases.",
        "command_template": "sqlite3 {args}",
        "examples": ["sqlite3 grades.db '.tables'"],
    },
    {
        "id": "rg",
        "name": "ripgrep",
        "description": "Fast recursive search through text files.",
        "command_template": "rg {args}",
        "examples": ["rg 'TODO' src/"],
    },
    {
        "id": "curl",
        "name": "curl",
        "description": "Fetch URLs from the web.",
        "command_template": "curl {args}",
        "examples": ["curl -fsSL https://example.com"],
    },
]


# ---------------------------------------------------------------------------
# Minimal MCP stdio JSON-RPC client. Spawns the server, runs initialize and
# tools/list, and exposes call_tool. We don't pull in the full MCP SDK — this
# keeps the dependency footprint tiny.
# ---------------------------------------------------------------------------

class MCPStdioClient:
    def __init__(self, name: str, command: str, args: list[str], env: dict | None = None):
        self.name = name
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.tools: list[dict] = []
        self._next_id = 0
        self._read_lock = asyncio.Lock()
        self.error: Optional[str] = None

    async def start(self, timeout: float = 30.0) -> None:
        env = {**os.environ, **self.env}
        try:
            self.proc = await asyncio.create_subprocess_exec(
                self.command,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as exc:
            self.error = f"Command not found: {self.command}. {exc}"
            return
        try:
            await asyncio.wait_for(self._handshake(), timeout=timeout)
        except asyncio.TimeoutError:
            self.error = "MCP server did not respond to initialize within 30s."
            await self.stop()
        except Exception as exc:
            self.error = f"MCP handshake failed: {exc}"
            await self.stop()

    async def _handshake(self) -> None:
        await self._call("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "BetterWebUI", "version": "0.1"},
        })
        await self._notify("notifications/initialized", {})
        result = await self._call("tools/list", {})
        self.tools = result.get("tools", []) if isinstance(result, dict) else []

    async def _send(self, message: dict) -> None:
        if not self.proc or not self.proc.stdin:
            raise RuntimeError("MCP server is not running.")
        line = (json.dumps(message) + "\n").encode("utf-8")
        self.proc.stdin.write(line)
        await self.proc.stdin.drain()

    async def _read_one(self) -> dict:
        async with self._read_lock:
            assert self.proc and self.proc.stdout
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    raise RuntimeError("MCP server closed unexpectedly.")
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue

    async def _call(self, method: str, params: dict) -> dict:
        self._next_id += 1
        req_id = self._next_id
        await self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        while True:
            msg = await self._read_one()
            if msg.get("id") == req_id:
                if "error" in msg:
                    err = msg["error"]
                    raise RuntimeError(err.get("message") if isinstance(err, dict) else str(err))
                return msg.get("result") or {}

    async def _notify(self, method: str, params: dict) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        return await self._call("tools/call", {"name": tool_name, "arguments": arguments})

    async def stop(self) -> None:
        if not self.proc:
            return
        try:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                self.proc.kill()
                await self.proc.wait()
        except ProcessLookupError:
            pass
        self.proc = None


class MCPManager:
    """Holds running MCPStdioClients keyed by server name. Reconcile against
    the current persisted server list."""

    def __init__(self) -> None:
        self.clients: dict[str, MCPStdioClient] = {}

    async def reconcile(self) -> None:
        cfg = load_mcp_servers()
        wanted = {s["name"]: s for s in cfg.get("servers", []) if s.get("enabled", True)}
        # Stop removed/disabled
        for name in list(self.clients):
            if name not in wanted:
                await self.clients[name].stop()
                del self.clients[name]
        # Start newly added/enabled
        for name, s in wanted.items():
            if name in self.clients:
                continue
            client = MCPStdioClient(
                name=name,
                command=s.get("command", ""),
                args=s.get("args", []),
                env=s.get("env", {}),
            )
            await client.start()
            self.clients[name] = client

    def status(self) -> list[dict]:
        out = []
        for name, client in self.clients.items():
            out.append({
                "name": name,
                "running": client.proc is not None and client.error is None,
                "error": client.error,
                "tool_count": len(client.tools),
                "tools": [
                    {"name": t.get("name"), "description": t.get("description", "")}
                    for t in client.tools
                ],
            })
        return out

    def list_all_tools(self, allowed_servers: Optional[list[str]] = None) -> list[dict]:
        out = []
        for name, client in self.clients.items():
            if allowed_servers is not None and name not in allowed_servers:
                continue
            for t in client.tools:
                out.append({
                    "server": name,
                    "name": t.get("name"),
                    "description": t.get("description", ""),
                })
        return out

    async def call(self, server_name: str, tool_name: str, args: dict) -> dict:
        client = self.clients.get(server_name)
        if not client:
            return {"error": f"MCP server '{server_name}' is not running."}
        if client.error:
            return {"error": f"MCP server '{server_name}' error: {client.error}"}
        try:
            result = await client.call_tool(tool_name, args)
        except Exception as exc:
            return {"error": f"MCP call failed: {exc}"}
        # The MCP tools/call result has a 'content' field; pass through.
        return result if isinstance(result, dict) else {"result": result}


mcp_manager = MCPManager()


# ---------------------------------------------------------------------------
# Skill loading
# ---------------------------------------------------------------------------

def list_skill_files() -> list[dict]:
    skills = []
    for path in sorted(SKILLS_DIR.glob("*.md")):
        try:
            post = frontmatter.load(path)
            skills.append(
                {
                    "id": path.stem,
                    "name": post.get("name", path.stem),
                    "description": post.get("description", ""),
                    "filename": path.name,
                }
            )
        except Exception as exc:
            skills.append(
                {
                    "id": path.stem,
                    "name": path.stem,
                    "description": f"(could not parse: {exc})",
                    "filename": path.name,
                }
            )
    return skills


def load_skill_content(skill_id: str) -> Optional[dict]:
    path = SKILLS_DIR / f"{skill_id}.md"
    if not path.exists():
        return None
    post = frontmatter.load(path)
    return {
        "id": skill_id,
        "name": post.get("name", skill_id),
        "description": post.get("description", ""),
        "content": post.content,
    }


# ---------------------------------------------------------------------------
# Tool definitions and the system-prompt suffix that explains how to use them
# ---------------------------------------------------------------------------

TOOL_PROTOCOL = """
You have tools. To call a tool, output exactly one fenced JSON block on its
own lines like this:

```tool
{"tool": "TOOL_NAME", "args": {...}}
```

After the tool runs, the result is added to the conversation and you continue.
Output at most one tool call per assistant turn. Speak naturally to the user
before and after tool calls. Never invent tool output — wait for the result.

You may use Markdown freely (headings, lists, tables, code fences, links).
Mathematics is rendered with KaTeX: use $...$ for inline math and $$...$$
for display equations. \\(...\\) and \\[...\\] also work.

Available tools:

- execute_shell: run a shell command on the user's computer. The host OS is
  detected automatically (bash on Linux/macOS, PowerShell on Windows). USER
  APPROVAL IS REQUIRED before the command runs — if denied, you'll see an
  error and should ask the user what they'd prefer. Args: {"command": "...",
  "reason": "short explanation of why this command is needed"}.

- read_file: read file(s) chosen by the user. The user is shown a file
  picker — you do NOT specify a path. The result is the chosen file(s)' name,
  type, and content. Args: {"reason": "why you need to read", "accept": "*",
  "multiple": false}. Use accept="image/*" or "text/*,.md,.csv" to filter.

- write_file: send the user a file to download to their computer. The browser
  saves it (REQUIRES APPROVAL). Args: {"filename": "name.ext", "content":
  "...", "mime": "text/plain"}.

- load_skill: load the full content of a named skill so you can follow its
  instructions. Args: {"skill_id": "..."}. Use this when a listed skill
  matches the user's request.

- generate_image: create an image via OpenWebUI's image endpoint. The image
  is sent to the user and downloaded to their computer automatically. Args:
  {"prompt": "description", "size": "1024x1024"}.

- generate_audio: text-to-speech via OpenWebUI. The audio is sent to the
  user and downloaded automatically. Args: {"text": "...", "voice": "alloy"}.

- mcp_call: call a tool from a connected MCP server (only available if
  servers are configured and running). Args: {"server": "server_name",
  "name": "tool_name", "arguments": {...}}.

- cli_call: run one of the user's pre-registered CLI shortcuts. Routes
  through execute_shell with approval. Args: {"id": "shortcut_id",
  "args": "command-line arguments"}.
""".strip()


def resolve_active_workspace(config: dict) -> Optional[dict]:
    wid = config.get("active_workspace_id")
    if not wid:
        return None
    data = load_workspaces()
    return next((w for w in data["workspaces"] if w["id"] == wid), None)


def build_system_prompt(config: dict, prompts: dict) -> str:
    parts: list[str] = []
    workspace = resolve_active_workspace(config)

    # 1. The system prompt itself — workspace overrides default.
    prompt_id = (workspace or {}).get("system_prompt_id") or config.get("active_prompt_id") or "default"
    chosen = next(
        (p for p in prompts["prompts"] if p["id"] == prompt_id),
        prompts["prompts"][0] if prompts["prompts"] else None,
    )
    if chosen:
        parts.append(chosen["content"])

    if workspace:
        parts.append(
            f"Active workspace: {workspace['name']}."
            + (f" {workspace['description']}" if workspace.get("description") else "")
        )

    # 2. Available skills.
    if workspace:
        active_skill_ids = workspace.get("active_skills") or []
    else:
        active_skill_ids = config.get("active_skills") or []
    available_skills = list_skill_files()
    if available_skills:
        listing = "\n".join(
            f"- {s['id']}: {s['description']}"
            for s in available_skills
            if not active_skill_ids or s["id"] in active_skill_ids
        )
        if listing:
            parts.append(
                "Skills you may invoke via load_skill (id: when to use):\n" + listing
            )

    # 3. Available MCP tools (only those whose server is running and, if a
    #    workspace is active, listed in the workspace).
    allowed_servers = (workspace or {}).get("active_mcp_servers")
    mcp_tools = mcp_manager.list_all_tools(allowed_servers=allowed_servers)
    if mcp_tools:
        listing = "\n".join(
            f"- {t['server']}.{t['name']}: {t['description']}" for t in mcp_tools
        )
        parts.append(
            "MCP tools available via mcp_call (server.name: description):\n" + listing
        )

    # 4. Available CLI shortcuts.
    cli_data = load_cli_tools()
    cli_ids = (workspace or {}).get("active_cli_tools")
    cli_listing = []
    for c in cli_data.get("tools", []):
        if cli_ids is not None and c["id"] not in cli_ids:
            continue
        cli_listing.append(
            f"- {c['id']} ({c.get('name', c['id'])}): {c.get('description', '')} "
            f"[template: {c.get('command_template', '')}]"
        )
    if cli_listing:
        parts.append(
            "CLI shortcuts available via cli_call (id: description [template]):\n"
            + "\n".join(cli_listing)
        )

    parts.append(f"Detected operating system: {platform.system()} ({platform.platform()}).")
    parts.append(TOOL_PROTOCOL)
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Tool-call parsing from model output
# ---------------------------------------------------------------------------

def extract_tool_call(text: str) -> Optional[dict]:
    """Find the first ```tool ...``` block and parse its JSON."""
    marker = "```tool"
    start = text.find(marker)
    if start == -1:
        return None
    body_start = text.find("\n", start) + 1
    end = text.find("```", body_start)
    if end == -1:
        return None
    body = text[body_start:end].strip()
    try:
        call = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(call, dict) or "tool" not in call:
        return None
    return {
        "tool": call["tool"],
        "args": call.get("args", {}) or {},
        "raw_block": text[start : end + 3],
    }


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

class ApprovalState:
    """Tracks pending tool approvals keyed by approval_id."""

    def __init__(self) -> None:
        self.events: dict[str, asyncio.Event] = {}
        self.results: dict[str, bool] = {}

    def new(self) -> str:
        aid = uuid.uuid4().hex
        self.events[aid] = asyncio.Event()
        return aid

    async def wait(self, aid: str, timeout: float = 600.0) -> bool:
        try:
            await asyncio.wait_for(self.events[aid].wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        return self.results.get(aid, False)

    def resolve(self, aid: str, approved: bool) -> bool:
        if aid not in self.events:
            return False
        self.results[aid] = approved
        self.events[aid].set()
        return True


approvals = ApprovalState()


class FileResponseStore:
    """Pending requests for the user to choose file(s) via the browser
    file picker. Same wait/resolve pattern as ApprovalState, but the result
    is a list of {filename, content_type, content_b64} payloads."""

    def __init__(self) -> None:
        self.events: dict[str, asyncio.Event] = {}
        self.results: dict[str, list[dict]] = {}

    def new(self) -> str:
        rid = uuid.uuid4().hex
        self.events[rid] = asyncio.Event()
        return rid

    async def wait(self, rid: str, timeout: float = 600.0) -> list[dict]:
        try:
            await asyncio.wait_for(self.events[rid].wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return []
        return self.results.get(rid, [])

    def resolve(self, rid: str, files: list[dict]) -> bool:
        if rid not in self.events:
            return False
        self.results[rid] = files
        self.events[rid].set()
        return True


file_responses = FileResponseStore()


def detect_shell() -> tuple[list[str], str]:
    """Return (argv prefix, friendly name) for the host shell."""
    if platform.system() == "Windows":
        if shutil.which("pwsh"):
            return (["pwsh", "-NoProfile", "-Command"], "PowerShell")
        return (["powershell", "-NoProfile", "-Command"], "PowerShell")
    return (["bash", "-lc"], "bash")


async def run_shell(command: str, timeout: int = 120) -> dict:
    argv_prefix, shell_name = detect_shell()
    argv = argv_prefix + [command]
    started = time.time()
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(ROOT),
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


async def call_openwebui_image(prompt: str, size: str, config: dict) -> dict:
    """Generate an image and return its bytes inline as base64 — the browser
    will download it to the user's Downloads folder. Nothing is saved on
    the server."""
    base = normalize_base_url(config["base_url"])
    profile = active_profile(config)
    headers = {"Authorization": f"Bearer {config['api_key']}"}
    payload = {"prompt": prompt, "n": 1, "size": size}
    if config.get("image_model"):
        payload["model"] = config["image_model"]
    async with httpx.AsyncClient(timeout=240.0) as client:
        resp = await client.post(
            f"{base}{profile['images']}", json=payload, headers=headers
        )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"Image generation failed: {resp.text[:500]}",
        )
    body = resp.json()
    item = (body.get("data") or [{}])[0]
    filename = f"{_slug(prompt)}-{uuid.uuid4().hex[:6]}.png"
    if "b64_json" in item:
        return {
            "filename": filename,
            "mime": "image/png",
            "data_b64": item["b64_json"],
            "prompt": prompt,
        }
    if "url" in item:
        async with httpx.AsyncClient(timeout=180.0) as client:
            img_resp = await client.get(item["url"])
        if img_resp.status_code != 200:
            return {"error": f"Could not fetch generated image at {item['url']}"}
        return {
            "filename": filename,
            "mime": img_resp.headers.get("content-type", "image/png"),
            "data_b64": base64.b64encode(img_resp.content).decode("ascii"),
            "prompt": prompt,
            "source_url": item["url"],
        }
    return {"raw": body}


async def call_openwebui_audio(text: str, voice: str, config: dict) -> dict:
    base = normalize_base_url(config["base_url"])
    profile = active_profile(config)
    headers = {"Authorization": f"Bearer {config['api_key']}"}
    payload = {"input": text, "voice": voice or "alloy", "model": "tts-1"}
    async with httpx.AsyncClient(timeout=240.0) as client:
        resp = await client.post(
            f"{base}{profile['audio']}", json=payload, headers=headers
        )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"Audio generation failed: {resp.text[:500]}",
        )
    filename = f"{_slug(text, 'speech')}-{uuid.uuid4().hex[:6]}.mp3"
    return {
        "filename": filename,
        "mime": "audio/mpeg",
        "data_b64": base64.b64encode(resp.content).decode("ascii"),
        "voice": voice,
    }


async def execute_tool(
    call: dict, config: dict, send_event
) -> dict:
    tool = call["tool"]
    args = call["args"]

    if tool == "execute_shell":
        if not config.get("shell_enabled", True):
            return {"error": "Shell execution is disabled in settings."}
        command = args.get("command", "").strip()
        reason = args.get("reason", "")
        if not command:
            return {"error": "No command provided."}
        aid = approvals.new()
        await send_event(
            "approval_request",
            {
                "approval_id": aid,
                "tool": "execute_shell",
                "command": command,
                "reason": reason,
                "shell": detect_shell()[1],
            },
        )
        approved = await approvals.wait(aid)
        if not approved:
            return {"error": "User denied this command."}
        await send_event("tool_running", {"tool": "execute_shell", "command": command})
        result = await run_shell(command)
        return result

    if tool == "read_file":
        rid = file_responses.new()
        await send_event(
            "file_request",
            {
                "request_id": rid,
                "purpose": args.get("reason") or args.get("purpose") or "read",
                "accept": args.get("accept", "*/*"),
                "multiple": bool(args.get("multiple", False)),
            },
        )
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
                # Binary file — return base64 to the model in case it knows
                # what to do with it (e.g. pass to a vision model). Cap size.
                b64 = f["data_b64"]
                entry["data_b64"] = b64[:200_000]
                entry["truncated"] = len(b64) > 200_000
            out_files.append(entry)
        return {"files": out_files}

    if tool == "write_file":
        # New behavior: stream the file to the browser as a download,
        # rather than writing on the server. Path-based writes have been
        # retired in favor of local download.
        filename = (args.get("filename") or args.get("path") or "file.txt").strip()
        filename = Path(filename).name or "file.txt"
        content = args.get("content", "")
        mime = args.get("mime", "text/plain")
        if not isinstance(content, str):
            content = str(content)
        aid = approvals.new()
        await send_event(
            "approval_request",
            {
                "approval_id": aid,
                "tool": "write_file",
                "filename": filename,
                "mime": mime,
                "preview": content[:1000],
                "byte_count": len(content.encode("utf-8")),
            },
        )
        approved = await approvals.wait(aid)
        if not approved:
            return {"error": "User denied this file write."}
        return {
            "filename": filename,
            "mime": mime,
            "bytes_written": len(content.encode("utf-8")),
            "data_b64": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        }

    if tool == "load_skill":
        skill_id = args.get("skill_id", "")
        skill = load_skill_content(skill_id)
        if not skill:
            return {"error": f"Skill '{skill_id}' not found."}
        return skill

    if tool == "generate_image":
        try:
            return await call_openwebui_image(
                args.get("prompt", ""),
                args.get("size", "1024x1024"),
                config,
            )
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
        # Reuse the execute_shell approval flow.
        aid = approvals.new()
        await send_event(
            "approval_request",
            {
                "approval_id": aid,
                "tool": "execute_shell",
                "command": command,
                "reason": f"CLI shortcut '{cli_id}': {cli.get('description', '')}",
                "shell": detect_shell()[1],
            },
        )
        approved = await approvals.wait(aid)
        if not approved:
            return {"error": "User denied this command."}
        await send_event("tool_running", {"tool": "cli_call", "command": command})
        return await run_shell(command)

    return {"error": f"Unknown tool: {tool}"}


# ---------------------------------------------------------------------------
# OpenWebUI proxy (chat completion + model listing)
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
                detail=(
                    "Couldn't find a working API endpoint at that URL. "
                    "Check the URL is your OpenWebUI root (e.g. http://localhost:3000) "
                    "and your API key has model access."
                ),
            )
        config["api_profile"] = profile
        config["base_url"] = base
        save_json(CONFIG_PATH, config)

    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            resp = await client.get(f"{base}{profile['models']}", headers=headers)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Cannot reach OpenWebUI: {exc}")

    if resp.status_code != 200:
        # Cached profile may be stale (e.g. server upgraded). Try to rediscover.
        new_profile = await discover_profile(base, api_key)
        if new_profile and new_profile != profile:
            config["api_profile"] = new_profile
            save_json(CONFIG_PATH, config)
            return await fetch_models(config)
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"OpenWebUI returned {resp.status_code}: {resp.text[:300]}",
        )

    try:
        body = resp.json()
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(
            status_code=502,
            detail=(
                f"Got a non-JSON response from {profile['models']}. "
                "The URL probably points at a web page rather than the API."
            ),
        )

    raw = body.get("data") if isinstance(body, dict) else body
    out = []
    for m in raw or []:
        mid = m.get("id") or m.get("name")
        if not mid:
            continue
        out.append({"id": mid, "name": m.get("name") or mid})
    return out


async def chat_complete(messages: list, model: str, config: dict) -> str:
    base = normalize_base_url(config["base_url"])
    profile = active_profile(config)
    headers = {"Authorization": f"Bearer {config['api_key']}"}
    payload = {"model": model, "messages": messages, "stream": False}
    async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
        resp = await client.post(
            f"{base}{profile['chat']}", json=payload, headers=headers
        )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"Chat call failed ({profile['name']}): {resp.text[:500]}",
        )
    try:
        body = resp.json()
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(
            status_code=502,
            detail="Chat endpoint returned non-JSON. Try saving Settings again to re-detect the API.",
        )
    try:
        return body["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError):
        return json.dumps(body)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="BetterWebUI")


@app.on_event("startup")
async def _startup() -> None:
    # Bring up any MCP servers the user has configured (best-effort).
    try:
        await mcp_manager.reconcile()
    except Exception as exc:
        print(f"[BetterWebUI] MCP startup error: {exc}")


@app.on_event("shutdown")
async def _shutdown() -> None:
    for client in list(mcp_manager.clients.values()):
        try:
            await client.stop()
        except Exception:
            pass


@app.get("/")
async def index():
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")


# --- Settings ---

class ConfigPatch(BaseModel):
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    default_model: Optional[str] = None
    image_model: Optional[str] = None
    tts_voice: Optional[str] = None
    active_prompt_id: Optional[str] = None
    active_skills: Optional[list[str]] = None
    active_workspace_id: Optional[str] = None
    auto_approve_safe: Optional[bool] = None
    shell_enabled: Optional[bool] = None


def _public_config(cfg: dict) -> dict:
    safe = dict(cfg)
    safe["api_key_set"] = bool(safe.get("api_key"))
    safe["api_key"] = ""
    profile = safe.get("api_profile")
    if isinstance(profile, dict):
        safe["api_profile_label"] = profile.get("label", profile.get("name", ""))
    else:
        safe["api_profile_label"] = ""
    return safe


@app.get("/api/config")
async def get_config():
    return _public_config(load_config())


@app.post("/api/config")
async def set_config(patch: ConfigPatch):
    cfg = load_config()
    payload = patch.model_dump(exclude_none=True)
    url_changed = False
    key_changed = False
    for k, v in payload.items():
        if k == "base_url":
            new_url = normalize_base_url(v)
            if new_url != cfg.get("base_url"):
                url_changed = True
            cfg[k] = new_url
        elif k == "api_key":
            if v != cfg.get("api_key"):
                key_changed = True
            cfg[k] = v
        else:
            cfg[k] = v

    # Connection details changed — invalidate cached profile and rediscover.
    if url_changed or key_changed:
        cfg["api_profile"] = None

    if cfg.get("base_url") and cfg.get("api_key") and not cfg.get("api_profile"):
        try:
            profile = await discover_profile(cfg["base_url"], cfg["api_key"])
            if profile:
                cfg["api_profile"] = profile
        except Exception:
            pass

    save_json(CONFIG_PATH, cfg)
    return _public_config(cfg)


# --- Models ---

@app.get("/api/models")
async def get_models():
    cfg = load_config()
    if not cfg.get("api_key") or not cfg.get("base_url"):
        return {"models": [], "error": "Set your OpenWebUI URL and API key first."}
    try:
        models = await fetch_models(cfg)
    except HTTPException as exc:
        return {"models": [], "error": str(exc.detail)}
    return {"models": models}


# --- System prompts ---

class PromptIn(BaseModel):
    id: Optional[str] = None
    name: str
    content: str


@app.get("/api/system-prompts")
async def list_prompts():
    return load_prompts()


@app.post("/api/system-prompts")
async def upsert_prompt(p: PromptIn):
    data = load_prompts()
    pid = p.id or p.name.lower().replace(" ", "-")
    existing = next((x for x in data["prompts"] if x["id"] == pid), None)
    if existing:
        existing["name"] = p.name
        existing["content"] = p.content
    else:
        data["prompts"].append({"id": pid, "name": p.name, "content": p.content})
    save_json(PROMPTS_PATH, data)
    return {"id": pid}


@app.delete("/api/system-prompts/{prompt_id}")
async def delete_prompt(prompt_id: str):
    data = load_prompts()
    data["prompts"] = [x for x in data["prompts"] if x["id"] != prompt_id]
    save_json(PROMPTS_PATH, data)
    return {"ok": True}


# --- Skills ---

@app.get("/api/skills")
async def list_skills():
    return {"skills": list_skill_files()}


@app.get("/api/skills/{skill_id}")
async def get_skill(skill_id: str):
    skill = load_skill_content(skill_id)
    if not skill:
        raise HTTPException(404, "Skill not found")
    return skill


@app.post("/api/skills/upload")
async def upload_skill(file: UploadFile = File(...)):
    if not file.filename.endswith(".md"):
        raise HTTPException(400, "Skills must be .md files with frontmatter.")
    safe_name = Path(file.filename).name
    dest = SKILLS_DIR / safe_name
    content = await file.read()
    dest.write_bytes(content)
    return {"id": dest.stem, "filename": safe_name}


class SkillIn(BaseModel):
    id: str
    name: str
    description: str
    content: str


@app.post("/api/skills")
async def create_skill(s: SkillIn):
    safe_id = "".join(c for c in s.id if c.isalnum() or c in "-_").strip("-_") or "skill"
    body = (
        f"---\nname: {s.name}\ndescription: {s.description}\n---\n\n{s.content}\n"
    )
    (SKILLS_DIR / f"{safe_id}.md").write_text(body, encoding="utf-8")
    return {"id": safe_id}


@app.delete("/api/skills/{skill_id}")
async def delete_skill(skill_id: str):
    path = SKILLS_DIR / f"{skill_id}.md"
    if path.exists():
        path.unlink()
    return {"ok": True}


# --- Approvals ---

class ApprovalIn(BaseModel):
    approval_id: str
    approved: bool


@app.post("/api/approve")
async def approve(a: ApprovalIn):
    ok = approvals.resolve(a.approval_id, a.approved)
    if not ok:
        raise HTTPException(404, "Unknown approval id")
    return {"ok": True}


# --- File-picker responses ---

class FileResponseIn(BaseModel):
    request_id: str
    files: list  # [{filename, content_type, size, content?, data_b64?}, ...]


@app.post("/api/file-response")
async def post_file_response(r: FileResponseIn):
    ok = file_responses.resolve(r.request_id, r.files or [])
    if not ok:
        raise HTTPException(404, "Unknown file request id")
    return {"ok": True}


# --- Workspaces ---

class WorkspaceIn(BaseModel):
    id: Optional[str] = None
    name: str
    description: Optional[str] = ""
    system_prompt_id: Optional[str] = None
    active_skills: Optional[list[str]] = None
    active_mcp_servers: Optional[list[str]] = None
    active_cli_tools: Optional[list[str]] = None
    files: Optional[list[dict]] = None
    default_model: Optional[str] = None


@app.get("/api/workspaces")
async def list_workspaces_endpoint():
    return load_workspaces()


@app.get("/api/workspaces/{wid}")
async def get_workspace(wid: str):
    data = load_workspaces()
    w = next((x for x in data["workspaces"] if x["id"] == wid), None)
    if not w:
        raise HTTPException(404, "Workspace not found")
    return w


@app.post("/api/workspaces")
async def upsert_workspace(w: WorkspaceIn):
    data = load_workspaces()
    wid = w.id or "".join(c for c in w.name.lower() if c.isalnum() or c in "-_ ").strip().replace(" ", "-") or uuid.uuid4().hex[:8]
    payload = w.model_dump(exclude_none=True)
    payload["id"] = wid
    payload.setdefault("active_skills", [])
    payload.setdefault("active_mcp_servers", [])
    payload.setdefault("active_cli_tools", [])
    payload.setdefault("files", [])
    payload["updated_at"] = int(time.time())
    existing_idx = next(
        (i for i, x in enumerate(data["workspaces"]) if x["id"] == wid), None
    )
    if existing_idx is not None:
        data["workspaces"][existing_idx] = {**data["workspaces"][existing_idx], **payload}
    else:
        payload["created_at"] = payload["updated_at"]
        data["workspaces"].append(payload)
    save_json(WORKSPACES_PATH, data)
    return {"id": wid}


@app.delete("/api/workspaces/{wid}")
async def delete_workspace(wid: str):
    data = load_workspaces()
    data["workspaces"] = [x for x in data["workspaces"] if x["id"] != wid]
    save_json(WORKSPACES_PATH, data)
    cfg = load_config()
    if cfg.get("active_workspace_id") == wid:
        cfg["active_workspace_id"] = ""
        save_json(CONFIG_PATH, cfg)
    return {"ok": True}


# --- MCP servers ---

class MCPServerIn(BaseModel):
    name: str
    command: str
    args: Optional[list[str]] = None
    env: Optional[dict] = None
    description: Optional[str] = ""
    enabled: Optional[bool] = True


@app.get("/api/mcp/registry")
async def mcp_registry():
    return {"registry": MCP_REGISTRY}


@app.get("/api/mcp/servers")
async def list_mcp_servers_endpoint():
    cfg = load_mcp_servers()
    status_by_name = {s["name"]: s for s in mcp_manager.status()}
    out = []
    for s in cfg.get("servers", []):
        st = status_by_name.get(s["name"])
        out.append(
            {
                **s,
                "running": (st or {}).get("running", False),
                "error": (st or {}).get("error"),
                "tool_count": (st or {}).get("tool_count", 0),
                "tools": (st or {}).get("tools", []),
            }
        )
    return {"servers": out}


@app.post("/api/mcp/servers")
async def upsert_mcp_server(s: MCPServerIn):
    data = load_mcp_servers()
    payload = s.model_dump(exclude_none=True)
    existing = next(
        (i for i, x in enumerate(data["servers"]) if x["name"] == s.name), None
    )
    if existing is not None:
        data["servers"][existing] = {**data["servers"][existing], **payload}
    else:
        data["servers"].append(payload)
    save_json(MCP_PATH, data)
    await mcp_manager.reconcile()
    return {"name": s.name}


@app.delete("/api/mcp/servers/{name}")
async def delete_mcp_server(name: str):
    data = load_mcp_servers()
    data["servers"] = [x for x in data["servers"] if x["name"] != name]
    save_json(MCP_PATH, data)
    await mcp_manager.reconcile()
    return {"ok": True}


@app.post("/api/mcp/reconcile")
async def mcp_reconcile_endpoint():
    await mcp_manager.reconcile()
    return {"servers": mcp_manager.status()}


# --- CLI shortcuts ---

class CliToolIn(BaseModel):
    id: str
    name: str
    description: Optional[str] = ""
    command_template: str
    examples: Optional[list[str]] = None


@app.get("/api/cli/registry")
async def cli_registry():
    return {"registry": CLI_REGISTRY}


@app.get("/api/cli/tools")
async def list_cli_tools_endpoint():
    return load_cli_tools()


@app.post("/api/cli/tools")
async def upsert_cli_tool(t: CliToolIn):
    data = load_cli_tools()
    payload = t.model_dump(exclude_none=True)
    existing = next((i for i, x in enumerate(data["tools"]) if x["id"] == t.id), None)
    if existing is not None:
        data["tools"][existing] = {**data["tools"][existing], **payload}
    else:
        data["tools"].append(payload)
    save_json(CLI_PATH, data)
    return {"id": t.id}


@app.delete("/api/cli/tools/{tid}")
async def delete_cli_tool(tid: str):
    data = load_cli_tools()
    data["tools"] = [x for x in data["tools"] if x["id"] != tid]
    save_json(CLI_PATH, data)
    return {"ok": True}


# --- File uploads (for multimodal input) ---

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    safe_name = f"{uuid.uuid4().hex}_{Path(file.filename).name}"
    dest = UPLOADS_DIR / safe_name
    async with aiofiles.open(dest, "wb") as f:
        while chunk := await file.read(1024 * 64):
            await f.write(chunk)
    return {
        "url": f"/uploads/{safe_name}",
        "filename": file.filename,
        "content_type": file.content_type,
    }


# --- Conversations ---

@app.get("/api/conversations")
async def list_conversations():
    data = load_conversations()
    summary = []
    for cid, conv in data["conversations"].items():
        summary.append(
            {
                "id": cid,
                "title": conv.get("title", "Untitled"),
                "updated_at": conv.get("updated_at", 0),
            }
        )
    summary.sort(key=lambda x: x["updated_at"], reverse=True)
    return {"conversations": summary}


@app.get("/api/conversations/{cid}")
async def get_conversation(cid: str):
    data = load_conversations()
    conv = data["conversations"].get(cid)
    if not conv:
        raise HTTPException(404, "Not found")
    return conv


@app.delete("/api/conversations/{cid}")
async def delete_conversation(cid: str):
    data = load_conversations()
    data["conversations"].pop(cid, None)
    save_json(CONVERSATIONS_PATH, data)
    return {"ok": True}


def save_conversation(cid: str, title: str, messages: list) -> None:
    data = load_conversations()
    data["conversations"][cid] = {
        "id": cid,
        "title": title,
        "messages": messages,
        "updated_at": int(time.time()),
    }
    save_json(CONVERSATIONS_PATH, data)


# --- Chat (the main loop) ---

class ChatRequest(BaseModel):
    conversation_id: Optional[str] = None
    messages: list  # full history from the client (role/content/attachments)
    model: Optional[str] = None
    title: Optional[str] = None


# Roles OpenAI/OpenWebUI's chat completions API accepts. Anything else is a
# UI-only annotation we created on the client and must NOT be forwarded.
_VALID_ROLES = {"system", "user", "assistant", "function", "tool", "developer"}


def to_openai_messages(history: list, system_prompt: str) -> list:
    out = [{"role": "system", "content": system_prompt}]
    for m in history:
        role = m.get("role", "user")
        if role not in _VALID_ROLES:
            # "system-event" and similar UI-only messages are dropped here.
            continue
        # "tool" messages without a tool_call_id will also be rejected; if
        # they're our pure-UI tool displays (no tool_call_id), demote them
        # into a synthetic user message so the model still sees the result.
        if role == "tool" and not m.get("tool_call_id"):
            role = "user"
            content = f"[Tool result]\n{m.get('content', '')}"
            out.append({"role": role, "content": content})
            continue
        content = m.get("content", "")
        attachments = m.get("attachments") or []
        if attachments and role == "user":
            # Encode as multimodal content array per OpenAI spec
            parts = [{"type": "text", "text": content}] if content else []
            for a in attachments:
                ctype = a.get("content_type", "")
                url = a.get("url", "")
                if ctype.startswith("image/"):
                    parts.append({"type": "image_url", "image_url": {"url": url}})
                else:
                    parts.append(
                        {"type": "text", "text": f"[Attachment: {a.get('filename', url)}]"}
                    )
            out.append({"role": role, "content": parts})
        else:
            out.append({"role": role, "content": content})
    return out


@app.post("/api/chat")
async def chat(req: ChatRequest):
    cfg = load_config()
    if not cfg.get("api_key") or not cfg.get("base_url"):
        raise HTTPException(400, "Set your OpenWebUI URL and API key first.")
    model = req.model or cfg.get("default_model")
    if not model:
        raise HTTPException(400, "Pick a model first.")
    prompts = load_prompts()
    cid = req.conversation_id or uuid.uuid4().hex

    queue: asyncio.Queue = asyncio.Queue()

    async def send_event(event: str, data: dict) -> None:
        await queue.put({"event": event, "data": data})

    async def run_loop() -> None:
        # Defensive intake filter: never let UI-only roles ("system-event",
        # ad-hoc tool displays without tool_call_id) enter the canonical
        # history that we persist and feed back to the model.
        history = [
            m for m in req.messages
            if isinstance(m, dict) and m.get("role") in {"user", "assistant"}
        ]
        system_prompt = build_system_prompt(cfg, prompts)
        try:
            for _step in range(8):  # safety cap on tool iterations
                openai_messages = to_openai_messages(history, system_prompt)
                await send_event("status", {"message": "Thinking..."})
                text = await chat_complete(openai_messages, model, cfg)
                await send_event("assistant_text", {"text": text})
                history.append({"role": "assistant", "content": text})

                call = extract_tool_call(text)
                if not call:
                    break

                await send_event(
                    "tool_call",
                    {"tool": call["tool"], "args": call["args"]},
                )
                result = await execute_tool(call, cfg, send_event)
                await send_event("tool_result", {"tool": call["tool"], "result": result})

                # The model only needs the metadata of generated/picked binary
                # content, not the raw base64 — that would explode token
                # usage and hit context limits.
                result_for_model = dict(result) if isinstance(result, dict) else result
                if isinstance(result_for_model, dict):
                    if "data_b64" in result_for_model:
                        size = len(result_for_model["data_b64"])
                        result_for_model["data_b64"] = f"<{size} chars of base64 omitted; sent to user>"
                    if "files" in result_for_model and isinstance(result_for_model["files"], list):
                        for f in result_for_model["files"]:
                            if isinstance(f, dict) and "data_b64" in f:
                                size = len(f["data_b64"])
                                f["data_b64"] = f"<{size} chars of base64 omitted>"
                history.append(
                    {
                        "role": "user",
                        "content": (
                            f"[Tool '{call['tool']}' result]\n"
                            f"```json\n{json.dumps(result_for_model, indent=2)[:8000]}\n```"
                        ),
                    }
                )
            title = req.title or (
                history[0]["content"][:60] if history and history[0].get("content") else "Conversation"
            )
            save_conversation(cid, title, history)
            await send_event("done", {"conversation_id": cid, "messages": history})
        except HTTPException as exc:
            await send_event("error", {"message": str(exc.detail)})
        except Exception as exc:  # pragma: no cover
            await send_event("error", {"message": f"{type(exc).__name__}: {exc}"})
        finally:
            await queue.put(None)

    task = asyncio.create_task(run_loop())

    async def event_stream() -> AsyncGenerator[bytes, None]:
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield f"event: {item['event']}\ndata: {json.dumps(item['data'])}\n\n".encode("utf-8")
        finally:
            task.cancel()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# --- Health ---

@app.get("/api/health")
async def health():
    mcp_status = mcp_manager.status()
    return {
        "ok": True,
        "platform": platform.system(),
        "shell": detect_shell()[1],
        "skills": len(list_skill_files()),
        "workspaces": len(load_workspaces()["workspaces"]),
        "mcp_servers": len(mcp_status),
        "mcp_running": sum(1 for s in mcp_status if s.get("running")),
        "cli_tools": len(load_cli_tools()["tools"]),
    }


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8765"))
    uvicorn.run(app, host=host, port=port, log_level="info")
