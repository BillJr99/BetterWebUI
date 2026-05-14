"""
BetterWebUI — a friendlier OpenWebUI front-end with skills, custom system
prompts, multimodal generation, MCP-style tooling, gated shell execution,
visible task plans, file-tree/diff/checkpoints, plan mode, subagents,
workspace bundles, conversation search/pinning/forking, background tasks,
per-turn telemetry, onboarding wizard, and accessibility features.
"""

import asyncio
import base64
import hashlib
import io
import json
import os
import platform
import shutil
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

import aiofiles
import frontmatter
import httpx
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).parent.resolve()
DATA_DIR = ROOT / "data"
SKILLS_DIR = ROOT / "skills"
UPLOADS_DIR = DATA_DIR / "uploads"
CHECKPOINTS_DIR = DATA_DIR / "checkpoints"
TASKS_DIR = DATA_DIR / "tasks"
CONFIG_PATH = DATA_DIR / "config.json"
PROMPTS_PATH = DATA_DIR / "system_prompts.json"
CONVERSATIONS_PATH = DATA_DIR / "conversations.json"
WORKSPACES_PATH = DATA_DIR / "workspaces.json"
MCP_PATH = DATA_DIR / "mcp_servers.json"
CLI_PATH = DATA_DIR / "cli_tools.json"
BRANDING_PATH = DATA_DIR / "branding.json"

# WORKSPACE_DIR is the default directory for shell execution and file I/O.
# Set via the WORKSPACE_DIR environment variable (Docker mounts a host folder
# here). Falls back to a local "workspace/" subfolder when running without Docker.
WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", str(ROOT / "workspace")))

for d in (DATA_DIR, SKILLS_DIR, UPLOADS_DIR, CHECKPOINTS_DIR, TASKS_DIR, WORKSPACE_DIR):
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
            "consensus_runs": 1,
            "api_profile": None,
            "chat_mode": "approve-each",
            "onboarding_done": False,
            "display": {},
        },
    )


# ---------------------------------------------------------------------------
# OpenWebUI endpoint discovery
# ---------------------------------------------------------------------------

ENDPOINT_PROFILES: list[dict] = [
    {
        "name": "openwebui",
        "label": "OpenWebUI native",
        "models": "/api/models",
        "chat": "/api/chat/completions",
        "images": "/api/v1/images/generations",
        "audio": "/api/v1/audio/speech",
        "transcribe": "/api/v1/audio/transcriptions",
    },
    {
        "name": "openwebui-openai",
        "label": "OpenWebUI OpenAI proxy",
        "models": "/openai/v1/models",
        "chat": "/openai/v1/chat/completions",
        "images": "/openai/v1/images/generations",
        "audio": "/openai/v1/audio/speech",
        "transcribe": "/openai/v1/audio/transcriptions",
    },
    {
        "name": "openai-v1",
        "label": "OpenAI-compatible (/v1)",
        "models": "/v1/models",
        "chat": "/v1/chat/completions",
        "images": "/v1/images/generations",
        "audio": "/v1/audio/speech",
        "transcribe": "/v1/audio/transcriptions",
    },
    {
        "name": "api-v1",
        "label": "API v1 (/api/v1)",
        "models": "/api/v1/models",
        "chat": "/api/v1/chat/completions",
        "images": "/api/v1/images/generations",
        "audio": "/api/v1/audio/speech",
        "transcribe": "/api/v1/audio/transcriptions",
    },
]


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
# MCP server registry
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
# CLI shortcuts registry
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

# Onboarding workspace templates (use-case presets)
ONBOARDING_TEMPLATES: list[dict] = [
    {
        "id": "grading",
        "name": "Grading",
        "description": "Grade and give feedback on student work.",
        "system_prompt": (
            "You are a grading assistant for a higher-ed instructor. "
            "Be constructive, specific, and aligned with the rubric provided. "
            "Use load_skill to load the grading-rubric skill when grading."
        ),
        "skills": ["grading-rubric"],
        "cli": [],
        "mcp": [],
    },
    {
        "id": "research",
        "name": "Research",
        "description": "Find sources, summarize papers, manage citations.",
        "system_prompt": (
            "You are a research assistant for an academic. Help find, summarize, "
            "and cite sources. Use load_skill for research-citations."
        ),
        "skills": ["research-citations"],
        "cli": [],
        "mcp": ["fetch", "brave-search"],
    },
    {
        "id": "course-prep",
        "name": "Course Prep",
        "description": "Write syllabi, slides, lecture notes, and assignments.",
        "system_prompt": (
            "You are a course-preparation assistant. Help draft syllabi, "
            "lesson plans, slides, and assignments."
        ),
        "skills": [],
        "cli": ["pandoc"],
        "mcp": [],
    },
    {
        "id": "writing",
        "name": "Writing",
        "description": "Draft, edit, and polish academic or professional writing.",
        "system_prompt": (
            "You are a writing coach and editor. Help draft, revise, "
            "and polish documents."
        ),
        "skills": [],
        "cli": ["pandoc"],
        "mcp": [],
    },
    {
        "id": "coding",
        "name": "Coding",
        "description": "Write, debug, and explain code.",
        "system_prompt": (
            "You are a coding assistant. Help write, debug, and explain code. "
            "Use the computer-helper skill for running commands on the user's machine."
        ),
        "skills": ["computer-helper"],
        "cli": ["git", "rg"],
        "mcp": ["filesystem", "git"],
    },
]


# ---------------------------------------------------------------------------
# MCP stdio client
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
            "clientInfo": {"name": "BetterWebUI", "version": "0.2"},
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
    def __init__(self) -> None:
        self.clients: dict[str, MCPStdioClient] = {}

    async def reconcile(self) -> None:
        cfg = load_mcp_servers()
        wanted = {s["name"]: s for s in cfg.get("servers", []) if s.get("enabled", True)}
        for name in list(self.clients):
            if name not in wanted:
                await self.clients[name].stop()
                del self.clients[name]
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
        return result if isinstance(result, dict) else {"result": result}


mcp_manager = MCPManager()

# ---------------------------------------------------------------------------
# Session-level in-memory stores
# ---------------------------------------------------------------------------

# Commands trusted for the duration of this server session
_session_trusted_commands: set[str] = set()

# Explanation cache keyed by command hash
_command_explanation_cache: dict[str, str] = {}

# Background task store: task_id -> {status, events, ...}
_background_tasks: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Skill loading
# ---------------------------------------------------------------------------

def list_skill_files() -> list[dict]:
    skills = []
    for path in sorted(SKILLS_DIR.glob("*.md")):
        try:
            post = frontmatter.load(path)
            skills.append({
                "id": path.stem,
                "name": post.get("name", path.stem),
                "description": post.get("description", ""),
                "filename": path.name,
            })
        except Exception as exc:
            skills.append({
                "id": path.stem,
                "name": path.stem,
                "description": f"(could not parse: {exc})",
                "filename": path.name,
            })
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


def _lint_skills() -> list[dict]:
    issues = []
    for path in sorted(SKILLS_DIR.glob("*.md")):
        try:
            post = frontmatter.load(path)
            if not post.get("name"):
                issues.append({"type": "skill", "id": path.stem, "issue": "Missing 'name' in frontmatter"})
            if not post.get("description"):
                issues.append({"type": "skill", "id": path.stem, "issue": "Missing 'description' in frontmatter"})
        except Exception as exc:
            issues.append({"type": "skill", "id": path.stem, "issue": f"Parse error: {exc}"})
    return issues


def _lint_mcp() -> list[dict]:
    issues = []
    # Suggest what a missing binary usually means so the user knows how to fix it
    hint_by_bin = {
        "npx": "install Node.js (provides npx)",
        "node": "install Node.js",
        "uvx": "install uv (provides uvx)",
        "uv": "install uv",
    }
    for s in load_mcp_servers().get("servers", []):
        if not s.get("command"):
            issues.append({"type": "mcp", "id": s.get("name", "?"), "issue": "Missing 'command'"})
            continue
        cmd = s.get("command", "")
        # Validate the exact configured binary, not an alternative
        if cmd and not shutil.which(cmd):
            hint = hint_by_bin.get(cmd)
            msg = f"'{cmd}' not found on PATH"
            if hint:
                msg += f" — {hint}"
            issues.append({"type": "mcp", "id": s["name"], "issue": msg})
    return issues


def _lint_cli() -> list[dict]:
    issues = []
    for c in load_cli_tools().get("tools", []):
        if "{args}" not in c.get("command_template", ""):
            issues.append({"type": "cli", "id": c.get("id", "?"), "issue": "command_template does not contain {args}"})
    return issues


# ---------------------------------------------------------------------------
# Tool definitions and system-prompt builders
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

Available tools:

- update_task_plan: update the visible task plan the user sees in the right-hand
  panel. Call this when starting ANY multi-step task (to lay out the steps) and
  after each step (to tick items done, mark in_progress, or flag blocked).
  ALWAYS start a complex task by calling this first.
  Args: {"items": [{"id": "step-1", "title": "Step description",
  "status": "pending|in_progress|done|blocked", "note": "optional detail"}]}.

- spawn_subagent: run up to 3 read-only parallel sub-tasks to research,
  compare, or explore. Useful for "compare these rubrics", "research these
  topics", "check these files". The main conversation pauses until all
  subagents finish, then you get a combined summary.
  Args: {"kind": "explore|compare", "prompt": "what to investigate",
  "items": ["item1", "item2"] (optional for compare)}.

- execute_shell: run a shell command on the user's computer. The host OS is
  detected automatically (bash on Linux/macOS, PowerShell on Windows). USER
  APPROVAL IS REQUIRED before the command runs — if denied, you'll see an
  error and should ask the user what they'd prefer. Args: {"command": "...",
  "reason": "short explanation of why this command is needed"}.
  GRAPH / PLOT CONVENTION: if you run a Python script that generates a plot,
  save it to /tmp/bwui_plot.png (e.g. plt.savefig('/tmp/bwui_plot.png',
  bbox_inches='tight')) instead of plt.show(). The image is then
  automatically captured and displayed inline in the chat.

- read_file: read file(s) chosen by the user. The user is shown a file
  picker — you do NOT specify a path. The result is the chosen file(s)' name,
  type, and content. Args: {"reason": "why you need to read", "accept": "*",
  "multiple": false}. Use accept="image/*" or "text/*,.md,.csv" to filter.

- write_file: send the user a file to download to their computer. The browser
  saves it (REQUIRES APPROVAL). If a project root is set, the file is also
  checkpointed for undo. Args: {"filename": "name.ext", "content":
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
  through execute_shell with approval (unless the shortcut has always-allow
  policy). Args: {"id": "shortcut_id", "args": "command-line arguments"}.
""".strip()

PLAN_MODE_BLOCK = """
⚠️ PLAN MODE IS ACTIVE.

You may ONLY call: update_task_plan, read_file, load_skill.
Do NOT call any side-effecting tool: execute_shell, write_file,
generate_image, generate_audio, cli_call, mcp_call.

Your job in plan mode is to:
1. Use update_task_plan to lay out a complete step-by-step plan.
2. Explain your approach clearly in plain English.
3. Tell the user to switch to "Approve-each" mode (the chip in the chat header)
   when they are ready to execute.

Do NOT execute anything — plan only.
""".strip()

RENDERING_PROTOCOL = r"""
The user sees your replies rendered as Markdown with LaTeX/KaTeX for math.
This is not a hint — it's how the UI works. Plain-text "math" like
"x^2 + (b/a)x = -c/a" displays literally and looks wrong. Always wrap
mathematics in LaTeX delimiters and use real LaTeX commands.

Markdown:
  Headings:    ##, ###  (use ### for sub-sections inside replies)
  Emphasis:    **bold**, *italic*
  Code:        `inline`, ```python\nfenced\n```
  Lists:       - bullet      OR      1. numbered
  Quotes:      > a quote
  Links:       [text](https://example.com)
  Tables:      | header | header |
               |--------|--------|
               | a      | b      |

Math — REQUIRED for any equation, expression, fraction, exponent, root,
sum, integral, matrix, or set-builder. Choose:
  Inline:   $...$            $\\(...\\)$
  Display:  $$...$$           \\[...\\]

Use real LaTeX commands. WRONG vs RIGHT:

  WRONG:   x^2 + (b/a)x = -c/a
  RIGHT:   $x^{2} + \frac{b}{a}\,x = -\frac{c}{a}$

  WRONG:   sqrt(b^2 - 4ac)
  RIGHT:   $\sqrt{b^{2} - 4ac}$

  WRONG:   (-b +/- sqrt(b^2 - 4ac)) / (2a)
  RIGHT:   $$x = \frac{-b \pm \sqrt{b^{2} - 4ac}}{2a}$$

  WRONG:   sum from i=1 to n of i^2
  RIGHT:   $\sum_{i=1}^{n} i^{2}$

  WRONG:   integral from 0 to 1 of x^2 dx
  RIGHT:   $\int_{0}^{1} x^{2}\,dx$

Common LaTeX you should know:
  Fractions       \frac{num}{den}        Roots     \sqrt{x}, \sqrt[n]{x}
  Exponents       x^{n}    (always brace multi-character exponents)
  Subscripts      x_{i}
  Operators       \pm  \mp  \cdot  \times  \div  \ast
  Relations       \leq  \geq  \neq  \approx  \equiv  \rightarrow  \Leftrightarrow
  Greek           \alpha \beta \gamma \delta \epsilon \pi \sigma \theta \phi \omega
  Sets            \mathbb{R} \mathbb{N} \mathbb{Z} \emptyset \in \notin \subset
  Logic           \forall \exists \neg \land \lor \Rightarrow
  Calculus        \int  \sum  \prod  \lim  \partial  \nabla  \infty
  Spacing         \,  (thin space)   \;  (thick)   \quad  \qquad

Aligned multi-line equations:
$$
\begin{aligned}
ax^{2} + bx + c &= 0 \\
x &= \frac{-b \pm \sqrt{b^{2} - 4ac}}{2a}
\end{aligned}
$$

Matrices:
$$
A = \begin{pmatrix} 1 & 2 \\ 3 & 4 \end{pmatrix}
$$

When in doubt: lean toward wrapping it in $...$. Never use ASCII math
shortcuts (^, /, sqrt(), <=, !=, sum, int) in user-visible prose.
""".strip()


def resolve_active_workspace(config: dict) -> Optional[dict]:
    wid = config.get("active_workspace_id")
    if not wid:
        return None
    data = load_workspaces()
    return next((w for w in data["workspaces"] if w["id"] == wid), None)


def build_system_prompt(config: dict, prompts: dict, mode: str = "approve-each") -> str:
    parts: list[str] = []
    workspace = resolve_active_workspace(config)

    # 1. The system prompt itself
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

    # Plan mode block (injected before other tools if active)
    effective_mode = mode or (workspace or {}).get("mode") or config.get("chat_mode", "approve-each")
    if effective_mode == "plan":
        parts.append(PLAN_MODE_BLOCK)

    # Response style
    parts.append(
        "Always attempt a complete, useful response to the user's request before "
        "asking clarifying questions. If something is ambiguous, make a reasonable "
        "assumption, state it briefly, and proceed. Save any follow-up questions for "
        "the end of your reply, after the substantive response."
    )

    # Rendering rules
    parts.append(RENDERING_PROTOCOL)

    # 2. Available skills
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
            parts.append("Skills you may invoke via load_skill (id: when to use):\n" + listing)

    # 3. MCP tools
    allowed_servers = (workspace or {}).get("active_mcp_servers")
    mcp_tools = mcp_manager.list_all_tools(allowed_servers=allowed_servers)
    if mcp_tools:
        listing = "\n".join(
            f"- {t['server']}.{t['name']}: {t['description']}" for t in mcp_tools
        )
        parts.append("MCP tools available via mcp_call (server.name: description):\n" + listing)

    # 4. CLI shortcuts
    cli_data = load_cli_tools()
    cli_ids = (workspace or {}).get("active_cli_tools")
    cli_listing = []
    for c in cli_data.get("tools", []):
        if cli_ids is not None and c["id"] not in cli_ids:
            continue
        policy = c.get("approval_policy", "ask")
        policy_note = " [always-allowed]" if policy == "always" else ""
        cli_listing.append(
            f"- {c['id']} ({c.get('name', c['id'])}): {c.get('description', '')} "
            f"[template: {c.get('command_template', '')}]{policy_note}"
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
# Tool-call parsing
# ---------------------------------------------------------------------------

def extract_tool_call(text: str) -> Optional[dict]:
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
    raw_args = call.get("args", {}) or {}
    return {
        "tool": call["tool"],
        "args": raw_args if isinstance(raw_args, dict) else {},
        "raw_block": text[start : end + 3],
    }


# ---------------------------------------------------------------------------
# Approval / file-response state
# ---------------------------------------------------------------------------

class ApprovalState:
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
    effective_cwd = cwd or str(WORKSPACE_DIR)
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

def _checkpoint_file(workspace_id: str, filename: str, content: str) -> str:
    """Save a checkpoint snapshot. Returns the checkpoint id."""
    ckpt_dir = CHECKPOINTS_DIR / workspace_id / _slug(filename, "file")
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    ckpt_id = f"{int(time.time())}_{uuid.uuid4().hex[:6]}"
    ckpt_path = ckpt_dir / f"{ckpt_id}.txt"
    ckpt_path.write_text(content, encoding="utf-8")
    return ckpt_id


def _list_checkpoints(workspace_id: str, filename: str) -> list[dict]:
    ckpt_dir = CHECKPOINTS_DIR / workspace_id / _slug(filename, "file")
    if not ckpt_dir.exists():
        return []
    out = []
    for p in sorted(ckpt_dir.glob("*.txt"), reverse=True)[:20]:
        parts = p.stem.split("_", 1)
        ts = int(parts[0]) if parts else 0
        out.append({"id": p.stem, "filename": filename, "saved_at": ts})
    return out


def _get_checkpoint(workspace_id: str, filename: str, ckpt_id: str) -> Optional[str]:
    ckpt_path = CHECKPOINTS_DIR / workspace_id / _slug(filename, "file") / f"{ckpt_id}.txt"
    if not ckpt_path.exists():
        return None
    return ckpt_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# OpenWebUI proxy helpers
# ---------------------------------------------------------------------------

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
        return {"filename": filename, "mime": "image/png", "data_b64": item["b64_json"], "prompt": prompt}
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
        resp = await client.post(f"{base}{profile['audio']}", json=payload, headers=headers)
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=f"Audio generation failed: {resp.text[:500]}")
    filename = f"{_slug(text, 'speech')}-{uuid.uuid4().hex[:6]}.mp3"
    return {
        "filename": filename,
        "mime": "audio/mpeg",
        "data_b64": base64.b64encode(resp.content).decode("ascii"),
        "voice": voice,
    }


async def chat_complete(messages: list, model: str, config: dict) -> tuple[str, dict]:
    """Returns (text, usage_dict)."""
    base = normalize_base_url(config["base_url"])
    profile = active_profile(config)
    headers = {"Authorization": f"Bearer {config['api_key']}"}
    payload = {"model": model, "messages": messages, "stream": False}
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
# Subagent execution
# ---------------------------------------------------------------------------

async def run_subagent_loop(
    prompt: str, model: str, config: dict, max_steps: int = 4
) -> str:
    """Run a read-only sub-loop. Returns final assistant text."""
    sub_system = (
        "You are a read-only research subagent. You may call load_skill only. "
        "Do NOT call execute_shell, write_file, generate_image, generate_audio, "
        "cli_call, mcp_call, or read_file. Summarize from your existing context. "
        "Produce a concise summary of your findings when done.\n\n"
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
        text, _ = await chat_complete(messages, model, config)
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

async def execute_tool(call: dict, config: dict, send_event, mode: str = "approve-each", model: str = "") -> dict:
    tool = call["tool"]
    args = call["args"]

    # Plan mode: block side-effecting tools
    if mode == "plan" and tool in (
        "execute_shell", "write_file", "generate_image",
        "generate_audio", "cli_call", "mcp_call",
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
        for _img_path in (Path("/tmp/bwui_plot.png"), ROOT / "bwui_plot.png"):
            if _img_path.exists():
                try:
                    result["filename"] = "plot.png"
                    result["mime"] = "image/png"
                    result["data_b64"] = base64.b64encode(_img_path.read_bytes()).decode("ascii")
                    _img_path.unlink()
                except Exception:
                    pass
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
        # don't leave junk checkpoints behind on disk.
        checkpoint_id = None
        if dest.exists():
            wid = (workspace or {}).get("id", "default")
            try:
                checkpoint_id = _checkpoint_file(
                    wid, filename, dest.read_text(encoding="utf-8", errors="replace")
                )
            except Exception:
                checkpoint_id = None

        # Write to disk
        write_error = None
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
        except Exception as exc:
            write_error = str(exc)

        # Inline the data only when small enough for the browser to handle as a
        # blob in the chat transcript; for larger writes, the file is still on
        # disk at project_root and the frontend just won't show an inline link.
        _MAX_INLINE_BYTES = 1 * 1024 * 1024
        result = {
            "filename": filename,
            "dest_path": filename,
            "mime": mime,
            "bytes_written": content_bytes_len,
            "checkpoint_id": checkpoint_id,
        }
        if content_bytes_len <= _MAX_INLINE_BYTES:
            result["data_b64"] = base64.b64encode(content.encode("utf-8")).decode("ascii")
        if write_error:
            result["write_error"] = write_error
        return result

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

        return await run_shell(command)

    return {"error": f"Unknown tool: {tool}"}


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
        save_json(CONFIG_PATH, config)

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
            save_json(CONFIG_PATH, config)
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


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="BetterWebUI")


@app.on_event("startup")
async def _startup() -> None:
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
    consensus_runs: Optional[int] = None
    chat_mode: Optional[str] = None
    onboarding_done: Optional[bool] = None
    display: Optional[dict] = None


def _public_config(cfg: dict) -> dict:
    safe = dict(cfg)
    safe["api_key_set"] = bool(safe.get("api_key"))
    safe["api_key"] = ""
    profile = safe.get("api_profile")
    if isinstance(profile, dict):
        safe["api_profile_label"] = profile.get("label", profile.get("name", ""))
    else:
        safe["api_profile_label"] = ""
    # Expose the server-controlled workspace base so the UI can render an
    # accurate placeholder and validation hint for workspace project_root.
    safe["workspace_dir"] = str(Path(WORKSPACE_DIR).resolve())
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


@app.get("/api/recommend-model")
async def recommend_model(use_case: str = "general"):
    cfg = load_config()
    try:
        models = await fetch_models(cfg)
    except HTTPException:
        return {"recommendation": None}
    if not models:
        return {"recommendation": None}
    # Simple heuristic: prefer models with "gpt-4" or "claude" in name for complex tasks,
    # smaller models for grading/simple tasks
    heavy = ["gpt-4", "claude-opus", "claude-3-5", "llama-3.3", "mixtral-8x22"]
    light = ["gpt-3.5", "claude-haiku", "llama-3.1-8b", "phi", "mistral-7b"]
    if use_case in ("research", "coding", "writing"):
        for h in heavy:
            m = next((x for x in models if h in x["id"].lower()), None)
            if m:
                return {"recommendation": m, "reason": f"This model handles complex {use_case} tasks well."}
    else:
        for l in light:
            m = next((x for x in models if l in x["id"].lower()), None)
            if m:
                return {"recommendation": m, "reason": f"This efficient model works great for {use_case}."}
    return {"recommendation": models[0], "reason": "Using the first available model."}


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
    body = f"---\nname: {s.name}\ndescription: {s.description}\n---\n\n{s.content}\n"
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
    trust_session: Optional[bool] = False
    command: Optional[str] = None


@app.post("/api/approve")
async def approve(a: ApprovalIn):
    ok = approvals.resolve(a.approval_id, a.approved)
    if not ok:
        raise HTTPException(404, "Unknown approval id")
    if a.approved and a.trust_session and a.command:
        _session_trusted_commands.add(a.command)
    return {"ok": True}


class SessionTrustIn(BaseModel):
    command: str


@app.post("/api/session/trust")
async def session_trust(t: SessionTrustIn):
    _session_trusted_commands.add(t.command)
    return {"ok": True, "trusted_count": len(_session_trusted_commands)}


@app.get("/api/session/trust")
async def list_session_trust():
    return {"commands": list(_session_trusted_commands)}


@app.delete("/api/session/trust")
async def clear_session_trust():
    _session_trusted_commands.clear()
    return {"ok": True}


# --- File-picker responses ---

class FileResponseIn(BaseModel):
    request_id: str
    files: list


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
    project_root: Optional[str] = None
    mode: Optional[str] = None
    shell_approval_policy: Optional[str] = None


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
    # Reject project_root values that escape WORKSPACE_DIR up front so the user
    # gets actionable feedback. _resolve_project_root still clamps as defense-
    # in-depth, but failing closed here avoids the "configured path silently
    # ignored" footgun the reviewer flagged.
    if w.project_root:
        base = Path(WORKSPACE_DIR).resolve()
        try:
            Path(w.project_root).resolve().relative_to(base)
        except (ValueError, OSError):
            raise HTTPException(
                400,
                f"project_root must be inside the workspace directory ({base}).",
            )
    data = load_workspaces()
    wid = w.id or "".join(c for c in w.name.lower() if c.isalnum() or c in "-_ ").strip().replace(" ", "-") or uuid.uuid4().hex[:8]
    payload = w.model_dump(exclude_none=True)
    payload["id"] = wid
    payload.setdefault("active_skills", [])
    payload.setdefault("active_mcp_servers", [])
    payload.setdefault("active_cli_tools", [])
    payload.setdefault("files", [])
    payload["updated_at"] = int(time.time())
    existing_idx = next((i for i, x in enumerate(data["workspaces"]) if x["id"] == wid), None)
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


@app.get("/api/workspaces/{wid}/export")
async def export_workspace(wid: str):
    data = load_workspaces()
    w = next((x for x in data["workspaces"] if x["id"] == wid), None)
    if not w:
        raise HTTPException(404, "Workspace not found")
    prompts = load_prompts()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Manifest
        manifest = {
            "version": "1",
            "name": w["name"],
            "description": w.get("description", ""),
            "exported_at": int(time.time()),
            "active_skills": w.get("active_skills", []),
            "active_mcp_servers": w.get("active_mcp_servers", []),
            "active_cli_tools": w.get("active_cli_tools", []),
            "mode": w.get("mode", "approve-each"),
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        # System prompt
        pid = w.get("system_prompt_id")
        if pid:
            p = next((x for x in prompts["prompts"] if x["id"] == pid), None)
            if p:
                zf.writestr("system_prompt.json", json.dumps(p, indent=2))
        # Skills
        for sid in w.get("active_skills", []):
            skill = load_skill_content(sid)
            if skill:
                path = SKILLS_DIR / f"{sid}.md"
                if path.exists():
                    zf.write(path, f"skills/{sid}.md")
        # MCP stubs (no secrets)
        mcp_data = load_mcp_servers()
        mcp_stubs = []
        for sname in w.get("active_mcp_servers", []):
            s = next((x for x in mcp_data.get("servers", []) if x["name"] == sname), None)
            if s:
                stub = {k: v for k, v in s.items() if k not in ("env",)}
                stub["env_keys"] = list((s.get("env") or {}).keys())
                mcp_stubs.append(stub)
        zf.writestr("mcp_servers.json", json.dumps({"servers": mcp_stubs}, indent=2))
        # CLI tools
        cli_data = load_cli_tools()
        cli_items = [c for c in cli_data.get("tools", []) if c["id"] in w.get("active_cli_tools", [])]
        zf.writestr("cli_tools.json", json.dumps({"tools": cli_items}, indent=2))
    buf.seek(0)
    safe_name = _slug(w["name"], "workspace")
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.bwui"'},
    )


_MAX_BUNDLE_BYTES = 10 * 1024 * 1024       # 10 MB compressed
_MAX_MEMBER_BYTES = 2 * 1024 * 1024        # 2 MB per uncompressed member
_MAX_BUNDLE_MEMBERS = 500                  # cap member count to bound iteration cost
_MAX_BUNDLE_TOTAL_BYTES = 50 * 1024 * 1024  # cap total uncompressed bytes (zip-bomb guard)


@app.post("/api/workspaces/import")
async def import_workspace(file: UploadFile = File(...)):
    content = await file.read(_MAX_BUNDLE_BYTES + 1)
    if len(content) > _MAX_BUNDLE_BYTES:
        raise HTTPException(413, "Workspace bundle too large (max 10 MB).")
    try:
        buf = io.BytesIO(content)
        with zipfile.ZipFile(buf, "r") as zf:
            infos = zf.infolist()
            if len(infos) > _MAX_BUNDLE_MEMBERS:
                raise HTTPException(413, f"Bundle has too many entries (max {_MAX_BUNDLE_MEMBERS}).")
            total_uncompressed = 0
            for info in infos:
                if info.file_size > _MAX_MEMBER_BYTES:
                    raise HTTPException(413, f"Bundle member '{info.filename}' exceeds 2 MB limit.")
                total_uncompressed += info.file_size
                if total_uncompressed > _MAX_BUNDLE_TOTAL_BYTES:
                    raise HTTPException(413, f"Bundle uncompressed total exceeds {_MAX_BUNDLE_TOTAL_BYTES} bytes.")
            names = zf.namelist()
            manifest = json.loads(zf.read("manifest.json"))
            # Import system prompt if present
            prompt_id = None
            if "system_prompt.json" in names:
                p = json.loads(zf.read("system_prompt.json"))
                p_data = load_prompts()
                existing = next((x for x in p_data["prompts"] if x["id"] == p["id"]), None)
                if not existing:
                    p_data["prompts"].append(p)
                    save_json(PROMPTS_PATH, p_data)
                prompt_id = p["id"]
            # Import skills
            for name in names:
                if name.startswith("skills/") and name.endswith(".md"):
                    skill_bytes = zf.read(name)
                    dest = SKILLS_DIR / Path(name).name
                    dest.write_bytes(skill_bytes)
            # Import CLI tools
            if "cli_tools.json" in names:
                imported_cli = json.loads(zf.read("cli_tools.json"))
                cli_data = load_cli_tools()
                existing_ids = {c["id"] for c in cli_data.get("tools", [])}
                for c in imported_cli.get("tools", []):
                    if c["id"] not in existing_ids:
                        cli_data["tools"].append(c)
                save_json(CLI_PATH, cli_data)
            # Import MCP server stubs (safe fields only — env keys noted but not restored)
            if "mcp_servers.json" in names:
                imported_mcp = json.loads(zf.read("mcp_servers.json"))
                mcp_data = load_mcp_servers()
                existing_names = {s["name"] for s in mcp_data.get("servers", [])}
                for s in imported_mcp.get("servers", []):
                    if s.get("name") and s["name"] not in existing_names:
                        # env_keys is informational; don't restore actual env values
                        stub = {k: v for k, v in s.items() if k != "env_keys"}
                        mcp_data["servers"].append(stub)
                save_json(MCP_PATH, mcp_data)
        # Create the workspace
        wid = uuid.uuid4().hex[:8]
        ws_data = load_workspaces()
        ws_data["workspaces"].append({
            "id": wid,
            "name": manifest.get("name", "Imported Workspace"),
            "description": manifest.get("description", ""),
            "system_prompt_id": prompt_id,
            "active_skills": manifest.get("active_skills", []),
            "active_mcp_servers": manifest.get("active_mcp_servers", []),
            "active_cli_tools": manifest.get("active_cli_tools", []),
            "files": [],
            "mode": manifest.get("mode", "approve-each"),
            "created_at": int(time.time()),
            "updated_at": int(time.time()),
        })
        save_json(WORKSPACES_PATH, ws_data)
        return {"id": wid, "name": manifest.get("name")}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Invalid workspace bundle: {exc}")


# --- Onboarding ---

@app.get("/api/onboarding/templates")
async def onboarding_templates():
    return {"templates": ONBOARDING_TEMPLATES}


class OnboardingCompleteIn(BaseModel):
    template_id: Optional[str] = None
    workspace_name: Optional[str] = None


@app.post("/api/onboarding/complete")
async def onboarding_complete(body: OnboardingCompleteIn):
    cfg = load_config()
    cfg["onboarding_done"] = True
    save_json(CONFIG_PATH, cfg)

    if body.template_id:
        tmpl = next((t for t in ONBOARDING_TEMPLATES if t["id"] == body.template_id), None)
        if tmpl:
            # Create skill stubs if they don't exist (they should be in skills/ already)
            # Create workspace
            ws_data = load_workspaces()
            wid = uuid.uuid4().hex[:8]
            ws_name = body.workspace_name or tmpl["name"]
            # Upsert system prompt
            p_data = load_prompts()
            pid = f"onboarding-{tmpl['id']}"
            if not any(x["id"] == pid for x in p_data["prompts"]):
                p_data["prompts"].append({"id": pid, "name": ws_name, "content": tmpl["system_prompt"]})
                save_json(PROMPTS_PATH, p_data)
            ws_data["workspaces"].append({
                "id": wid,
                "name": ws_name,
                "description": tmpl["description"],
                "system_prompt_id": pid,
                "active_skills": tmpl.get("skills", []),
                "active_mcp_servers": tmpl.get("mcp", []),
                "active_cli_tools": tmpl.get("cli", []),
                "files": [],
                "mode": "approve-each",
                "created_at": int(time.time()),
                "updated_at": int(time.time()),
            })
            save_json(WORKSPACES_PATH, ws_data)
            # Set as active
            cfg["active_workspace_id"] = wid
            save_json(CONFIG_PATH, cfg)
            return {"ok": True, "workspace_id": wid, "workspace_name": ws_name}
    return {"ok": True}


# --- Project (file tree + checkpoints) ---

def _resolve_project_root(workspace: Optional[dict]) -> str:
    """Resolve a workspace's project root, restricting it to live under
    WORKSPACE_DIR. This prevents an unauthenticated caller (via /api/workspaces)
    from pointing a workspace at '/' or another sensitive directory and using
    the project file APIs to browse the host filesystem."""
    requested = (workspace or {}).get("project_root")
    base = Path(WORKSPACE_DIR).resolve()
    if not requested:
        return str(base)
    try:
        candidate = Path(requested).resolve()
        candidate.relative_to(base)
        return str(candidate)
    except (ValueError, OSError):
        # Fall back to the safe base so the workspace silently degrades to
        # the default workspace directory rather than exposing the filesystem.
        return str(base)


@app.get("/api/project/tree")
async def project_tree(path: str = ""):
    cfg = load_config()
    workspace = resolve_active_workspace(cfg)
    root = _resolve_project_root(workspace)
    root_path = Path(root).resolve()

    # Determine which subdirectory to list (support lazy directory expansion)
    if path:
        target = (root_path / path).resolve()
        try:
            target.relative_to(root_path)
        except ValueError:
            raise HTTPException(403, "Path outside project root.")
    else:
        target = root_path

    if not target.exists():
        raise HTTPException(404, "Path not found.")
    if not target.is_dir():
        raise HTTPException(400, f"'{path or '.'}' is not a directory.")

    entries = []
    try:
        for p in sorted(target.iterdir()):
            if p.name.startswith("."):
                continue
            rel = str(p.relative_to(root_path))
            if p.is_dir():
                entries.append({"type": "dir", "name": p.name, "path": rel})
            else:
                entries.append({
                    "type": "file",
                    "name": p.name,
                    "path": rel,
                    "size": p.stat().st_size,
                    "modified_at": int(p.stat().st_mtime),
                    "ext": p.suffix.lower(),
                })
    except Exception as exc:
        raise HTTPException(500, f"Could not list directory: {exc}")
    # Don't leak the absolute filesystem root to the client; the frontend
    # only needs the relative entries to render and request further paths.
    return {"entries": entries}


_MAX_PROJECT_FILE_BYTES = 1 * 1024 * 1024  # 1 MB cap for /api/project/file


@app.get("/api/project/file")
async def project_file(path: str, include_content: bool = True):
    cfg = load_config()
    workspace = resolve_active_workspace(cfg)
    root = _resolve_project_root(workspace)
    full = Path(root) / path
    try:
        full.resolve().relative_to(Path(root).resolve())
    except ValueError:
        raise HTTPException(403, "Path outside project root.")
    if not full.exists():
        raise HTTPException(404, "File not found.")
    size = full.stat().st_size
    try:
        # Read cap+1 bytes so we can detect truncation from the read itself
        # (not just from stat, which can lie on streaming filesystems or fifos).
        with full.open("rb") as fh:
            raw = fh.read(_MAX_PROJECT_FILE_BYTES + 1)
        truncated = len(raw) > _MAX_PROJECT_FILE_BYTES
        if truncated:
            raw = raw[:_MAX_PROJECT_FILE_BYTES]
        # NUL byte heuristic + strict UTF-8 decode: anything that fails either
        # check is treated as binary so the diff modal's binary guard works.
        if b"\x00" in raw:
            content = base64.b64encode(raw).decode("ascii")
            is_binary = True
        else:
            try:
                content = raw.decode("utf-8", errors="strict")
                is_binary = False
            except UnicodeDecodeError:
                content = base64.b64encode(raw).decode("ascii")
                is_binary = True
    except Exception:
        # Capped streaming fallback so a transient read failure can't blow past the 1 MB cap
        try:
            with full.open("rb") as fh:
                raw = fh.read(_MAX_PROJECT_FILE_BYTES + 1)
            truncated = len(raw) > _MAX_PROJECT_FILE_BYTES
            if truncated:
                raw = raw[:_MAX_PROJECT_FILE_BYTES]
            content = base64.b64encode(raw).decode("ascii")
            is_binary = True
        except Exception as exc:
            raise HTTPException(500, f"Could not read file: {exc}")
    # The frontend treats binary files as "preview not available" and never
    # reads the bytes, so omit the base64 content by default to keep responses
    # small. Callers that genuinely need the bytes can pass include_content=true.
    payload = {
        "path": path,
        "is_binary": is_binary,
        "size": size,
        "modified_at": int(full.stat().st_mtime),
        "truncated": truncated,
    }
    if not is_binary or include_content:
        payload["content"] = content
    return payload


@app.get("/api/project/checkpoints")
async def list_project_checkpoints(filename: str):
    cfg = load_config()
    workspace = resolve_active_workspace(cfg)
    wid = (workspace or {}).get("id", "default")
    return {"checkpoints": _list_checkpoints(wid, filename)}


class RevertIn(BaseModel):
    filename: str
    checkpoint_id: str


@app.post("/api/project/revert")
async def revert_project_file(r: RevertIn):
    cfg = load_config()
    workspace = resolve_active_workspace(cfg)
    wid = (workspace or {}).get("id", "default")
    content = _get_checkpoint(wid, r.filename, r.checkpoint_id)
    if content is None:
        raise HTTPException(404, "Checkpoint not found.")
    root = _resolve_project_root(workspace)
    dest = Path(root) / r.filename
    # Reject path traversal: ensure dest stays under the resolved project root
    try:
        dest.resolve().relative_to(Path(root).resolve())
    except ValueError:
        raise HTTPException(403, "Path outside project root.")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    except Exception as exc:
        raise HTTPException(500, f"Could not write file: {exc}")
    return {"ok": True, "filename": r.filename, "bytes": len(content)}


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
        out.append({
            **s,
            "running": (st or {}).get("running", False),
            "error": (st or {}).get("error"),
            "tool_count": (st or {}).get("tool_count", 0),
            "tools": (st or {}).get("tools", []),
        })
    return {"servers": out}


@app.post("/api/mcp/servers")
async def upsert_mcp_server(s: MCPServerIn):
    data = load_mcp_servers()
    payload = s.model_dump(exclude_none=True)
    existing = next((i for i, x in enumerate(data["servers"]) if x["name"] == s.name), None)
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
    approval_policy: Optional[str] = "ask"


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


# --- File uploads ---

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    safe_name = f"{uuid.uuid4().hex}_{Path(file.filename).name}"
    dest = UPLOADS_DIR / safe_name
    async with aiofiles.open(dest, "wb") as f:
        while chunk := await file.read(1024 * 64):
            await f.write(chunk)
    return {"url": f"/uploads/{safe_name}", "filename": file.filename, "content_type": file.content_type}


# --- Voice transcription ---

_MAX_TRANSCRIBE_BYTES = 25 * 1024 * 1024  # 25 MB cap for /api/transcribe uploads


@app.post("/api/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    cfg = load_config()
    if not cfg.get("api_key") or not cfg.get("base_url"):
        raise HTTPException(400, "Set your OpenWebUI URL and API key first.")
    profile = active_profile(cfg)
    base = normalize_base_url(cfg["base_url"])
    transcribe_url = f"{base}{profile.get('transcribe', '/api/v1/audio/transcriptions')}"
    headers = {"Authorization": f"Bearer {cfg['api_key']}"}
    audio_bytes = await file.read(_MAX_TRANSCRIBE_BYTES + 1)
    if len(audio_bytes) > _MAX_TRANSCRIBE_BYTES:
        raise HTTPException(413, "Audio upload too large (max 25 MB).")
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(
                transcribe_url,
                headers=headers,
                files={"file": (file.filename or "audio.webm", audio_bytes, file.content_type or "audio/webm")},
                data={"model": "whisper-1"},
            )
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"Transcription request failed: {exc}")
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, f"Transcription failed: {resp.text[:300]}")
    try:
        body = resp.json()
        return {"text": body.get("text", "")}
    except Exception:
        return {"text": resp.text}


# --- Text-to-speech (read-aloud) ---

class TtsIn(BaseModel):
    text: str
    voice: Optional[str] = None


@app.post("/api/tts")
async def tts_endpoint(body: TtsIn):
    cfg = load_config()
    if not cfg.get("api_key") or not cfg.get("base_url"):
        raise HTTPException(400, "Set your OpenWebUI URL and API key first.")
    voice = body.voice or cfg.get("tts_voice", "alloy")
    result = await call_openwebui_audio(body.text[:4096], voice, cfg)
    audio_bytes = base64.b64decode(result["data_b64"])
    return Response(content=audio_bytes, media_type="audio/mpeg")


# --- Command explanation ---

class ExplainCommandIn(BaseModel):
    command: str


@app.post("/api/explain-command")
async def explain_command(body: ExplainCommandIn):
    cmd = body.command.strip()
    if not cmd:
        raise HTTPException(400, "Command is required.")
    key = hashlib.md5(cmd.encode()).hexdigest()[:16]
    if key in _command_explanation_cache:
        return {"explanation": _command_explanation_cache[key], "cached": True}
    cfg = load_config()
    model = cfg.get("default_model", "")
    if not model:
        return {"explanation": "No model configured — cannot explain commands."}
    messages = [
        {"role": "system", "content": "You are a plain-English explainer for shell commands. Keep your explanation to one or two sentences that a non-technical person can understand. Do not include any code or technical jargon."},
        {"role": "user", "content": f"Explain this command:\n\n{cmd}"},
    ]
    try:
        text, _ = await chat_complete(messages, model, cfg)
        _command_explanation_cache[key] = text
        return {"explanation": text}
    except Exception as exc:
        return {"explanation": f"Could not explain: {exc}"}


# --- Conversations ---

@app.get("/api/conversations")
async def list_conversations():
    data = load_conversations()
    summary = []
    for cid, conv in data["conversations"].items():
        summary.append({
            "id": cid,
            "title": conv.get("title", "Untitled"),
            "updated_at": conv.get("updated_at", 0),
            "pinned": conv.get("pinned", False),
            "workspace_id": conv.get("workspace_id", ""),
            "tags": conv.get("tags", []),
        })
    summary.sort(key=lambda x: (not x["pinned"], -x["updated_at"]))
    return {"conversations": summary}


@app.get("/api/conversations/search")
async def search_conversations(q: str = ""):
    data = load_conversations()
    q_lower = q.lower().strip()
    results = []
    for cid, conv in data["conversations"].items():
        if not q_lower:
            results.append({"id": cid, "title": conv.get("title", ""), "updated_at": conv.get("updated_at", 0)})
            continue
        title = conv.get("title", "").lower()
        msgs_text = " ".join(
            m.get("content", "") for m in conv.get("messages", []) if isinstance(m.get("content"), str)
        ).lower()
        if q_lower in title or q_lower in msgs_text:
            # Find first matching snippet
            idx = msgs_text.find(q_lower)
            snippet = ""
            if idx != -1:
                raw_text = " ".join(
                    m.get("content", "") for m in conv.get("messages", []) if isinstance(m.get("content"), str)
                )
                snippet = raw_text[max(0, idx - 40) : idx + 80]
            results.append({
                "id": cid,
                "title": conv.get("title", ""),
                "updated_at": conv.get("updated_at", 0),
                "snippet": snippet,
            })
    results.sort(key=lambda x: -x["updated_at"])
    return {"results": results[:50]}


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


class PinIn(BaseModel):
    pinned: bool


@app.post("/api/conversations/{cid}/pin")
async def pin_conversation(cid: str, body: PinIn):
    data = load_conversations()
    conv = data["conversations"].get(cid)
    if not conv:
        raise HTTPException(404, "Not found")
    conv["pinned"] = body.pinned
    save_json(CONVERSATIONS_PATH, data)
    return {"ok": True}


class TagIn(BaseModel):
    tags: list[str]


@app.post("/api/conversations/{cid}/tags")
async def tag_conversation(cid: str, body: TagIn):
    data = load_conversations()
    conv = data["conversations"].get(cid)
    if not conv:
        raise HTTPException(404, "Not found")
    conv["tags"] = body.tags
    save_json(CONVERSATIONS_PATH, data)
    return {"ok": True}


class ForkIn(BaseModel):
    fork_at: Optional[int] = None          # index sent by the JS client
    from_message_index: Optional[int] = None  # alias kept for back-compat
    title: Optional[str] = None


@app.post("/api/conversations/{cid}/fork")
async def fork_conversation(cid: str, body: ForkIn):
    data = load_conversations()
    conv = data["conversations"].get(cid)
    if not conv:
        raise HTTPException(404, "Not found")
    messages = conv.get("messages", [])
    if body.fork_at is not None:
        idx = body.fork_at
    elif body.from_message_index is not None:
        idx = body.from_message_index
    else:
        idx = len(messages) - 1
    idx = max(0, min(idx, len(messages) - 1))
    forked_messages = messages[: idx + 1]
    new_cid = uuid.uuid4().hex
    title = body.title or f"{conv.get('title', 'Conversation')} (fork)"
    data["conversations"][new_cid] = {
        "id": new_cid,
        "title": title,
        "messages": forked_messages,
        "parent_id": cid,
        "updated_at": int(time.time()),
        "created_at": int(time.time()),
    }
    save_json(CONVERSATIONS_PATH, data)
    return {"id": new_cid, "title": title}


def save_conversation(cid: str, title: str, messages: list, task_plan: Optional[list] = None, workspace_id: str = "") -> None:
    data = load_conversations()
    existing = data["conversations"].get(cid, {})
    data["conversations"][cid] = {
        **existing,
        "id": cid,
        "title": title,
        "messages": messages,
        "task_plan": task_plan or [],
        "workspace_id": workspace_id,
        "updated_at": int(time.time()),
    }
    if "created_at" not in data["conversations"][cid]:
        data["conversations"][cid]["created_at"] = int(time.time())
    save_json(CONVERSATIONS_PATH, data)


# --- Linting ---

@app.get("/api/lint")
async def lint():
    skill_issues = _lint_skills()
    mcp_issues = _lint_mcp()
    cli_issues = _lint_cli()
    all_issues = skill_issues + mcp_issues + cli_issues
    return {
        "ok": len(all_issues) == 0,
        "issues": all_issues,
        "skills": [i["issue"] for i in skill_issues],
        "mcp": [i["issue"] for i in mcp_issues],
        "cli": [i["issue"] for i in cli_issues],
    }


# --- Branding ---

@app.get("/api/branding")
async def get_branding():
    return load_json(BRANDING_PATH, {"logo": None, "primary_color": None, "welcome": None, "institution": None})


# --- Background tasks ---

@app.get("/api/tasks")
async def list_tasks():
    return {"tasks": [
        {
            "id": tid,
            "title": t.get("title", ""),
            "status": t.get("status", "unknown"),
            "created_at": t.get("created_at", 0),
            "conversation_id": t.get("conversation_id", ""),
        }
        for tid, t in _background_tasks.items()
    ]}


@app.get("/api/tasks/{tid}")
async def get_task(tid: str):
    t = _background_tasks.get(tid)
    if not t:
        raise HTTPException(404, "Task not found")
    return t


# --- Chat (the main loop) ---

class ChatRequest(BaseModel):
    conversation_id: Optional[str] = None
    messages: list
    model: Optional[str] = None
    title: Optional[str] = None
    mode: Optional[str] = None
    background: Optional[bool] = False


_VALID_ROLES = {"system", "user", "assistant", "function", "tool", "developer"}


def to_openai_messages(history: list, system_prompt: str) -> list:
    out = [{"role": "system", "content": system_prompt}]
    for m in history:
        role = m.get("role", "user")
        if role not in _VALID_ROLES:
            continue
        if role == "tool" and not m.get("tool_call_id"):
            role = "user"
            content = f"[Tool result]\n{m.get('content', '')}"
            out.append({"role": role, "content": content})
            continue
        content = m.get("content", "")
        attachments = m.get("attachments") or []
        if attachments and role == "user":
            parts = [{"type": "text", "text": content}] if content else []
            for a in attachments:
                ctype = a.get("content_type", "")
                url = a.get("url", "")
                if ctype.startswith("image/"):
                    parts.append({"type": "image_url", "image_url": {"url": url}})
                else:
                    parts.append({"type": "text", "text": f"[Attachment: {a.get('filename', url)}]"})
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
    workspace = resolve_active_workspace(cfg)
    workspace_id = (workspace or {}).get("id", "")
    # Precedence: per-request mode → workspace.mode → config.chat_mode
    effective_mode = req.mode or (workspace or {}).get("mode") or cfg.get("chat_mode", "approve-each")

    queue: asyncio.Queue = asyncio.Queue()
    # Preserve the existing plan when resuming a conversation
    _existing_conv = load_conversations().get("conversations", {}).get(cid, {})
    current_task_plan: list = _existing_conv.get("task_plan", [])

    async def send_event(event: str, data: dict) -> None:
        await queue.put({"event": event, "data": data})

    async def run_loop() -> None:
        nonlocal current_task_plan
        history = [
            m for m in req.messages
            if isinstance(m, dict) and m.get("role") in {"user", "assistant"}
        ]
        system_prompt = build_system_prompt(cfg, prompts, effective_mode)
        try:
            for _step in range(12):  # higher cap for subagent-heavy tasks
                history, n_dropped = trim_to_context(history, system_prompt)
                if n_dropped:
                    await send_event("notice", {"message": f"Context trimmed: {n_dropped} older message(s) removed."})
                openai_messages = to_openai_messages(history, system_prompt)
                consensus_runs = max(1, min(10, cfg.get("consensus_runs", 1)))
                await send_event("status", {"message": "Thinking…"})
                if consensus_runs > 1:
                    raw_responses = await asyncio.gather(
                        *[chat_complete(openai_messages, model, cfg) for _ in range(consensus_runs)],
                        return_exceptions=True,
                    )
                    valid = [(r[0], r[1]) for r in raw_responses if isinstance(r, tuple)]
                    if len(valid) < 2:
                        text, usage = valid[0] if valid else ("", {})
                    else:
                        numbered = "\n\n".join(f"Response {i+1}:\n{r[0]}" for i, r in enumerate(valid))
                        synthesis_messages = list(openai_messages) + [{
                            "role": "user",
                            "content": (
                                f"The preceding query was answered independently {len(valid)} times. "
                                "Synthesize the responses into a single unified reply. "
                                "Favor content where the responses agree.\n\n" + numbered
                            ),
                        }]
                        text, usage = await chat_complete(synthesis_messages, model, cfg)
                else:
                    text, usage = await chat_complete(openai_messages, model, cfg)

                # Emit telemetry badge
                tokens_in = usage.get("prompt_tokens", 0)
                tokens_out = usage.get("completion_tokens", 0)
                elapsed = usage.get("elapsed_ms", 0)
                await send_event("assistant_text", {
                    "text": text,
                    "telemetry": {
                        "tokens_in": tokens_in,
                        "tokens_out": tokens_out,
                        "elapsed_ms": elapsed,
                    },
                })
                history.append({"role": "assistant", "content": text})

                call = extract_tool_call(text)
                if not call:
                    break

                await send_event("tool_call", {"tool": call["tool"], "args": call["args"]})
                result = await execute_tool(call, cfg, send_event, effective_mode, model)
                await send_event("tool_result", {"tool": call["tool"], "result": result})

                # Persist task plan updates
                if call["tool"] == "update_task_plan":
                    current_task_plan = result.get("items", call["args"].get("items", []))

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
                    if "combined" in result_for_model and len(str(result_for_model.get("combined", ""))) > 8000:
                        result_for_model["combined"] = result_for_model["combined"][:8000] + "… [truncated]"
                history.append({
                    "role": "user",
                    "content": (
                        f"[Tool '{call['tool']}' result]\n"
                        f"```json\n{json.dumps(result_for_model, indent=2)[:8000]}\n```"
                    ),
                })

            title = req.title or (
                history[0]["content"][:60] if history and history[0].get("content") else "Conversation"
            )
            save_conversation(cid, title, history, current_task_plan, workspace_id)
            await send_event("done", {
                "conversation_id": cid,
                "messages": history,
                "task_plan": current_task_plan,
            })
        except HTTPException as exc:
            await send_event("error", {"message": str(exc.detail)})
        except Exception as exc:
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
    lint_issues = _lint_skills() + _lint_mcp() + _lint_cli()
    return {
        "ok": True,
        "platform": platform.system(),
        "shell": detect_shell()[1],
        "skills": len(list_skill_files()),
        "workspaces": len(load_workspaces()["workspaces"]),
        "mcp_servers": len(mcp_status),
        "mcp_running": sum(1 for s in mcp_status if s.get("running")),
        "cli_tools": len(load_cli_tools()["tools"]),
        "lint_issues": len(lint_issues),
        "session_trusted_commands": len(_session_trusted_commands),
    }


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8765"))
    uvicorn.run(app, host=host, port=port, log_level="info")
