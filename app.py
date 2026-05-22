"""
BetterWebUI — a friendlier OpenWebUI front-end with skills, custom system
prompts, multimodal generation, MCP-style tooling, gated shell execution,
visible task plans, file-tree/diff/checkpoints, plan mode, subagents,
workspace bundles, conversation search/pinning/forking,
per-turn telemetry, onboarding wizard, and accessibility features.
"""

import asyncio
import base64
import hashlib
import io
import ipaddress
import json
import os
import platform
import re
import shutil
import time
import uuid
import zipfile
import logging
import logging.handlers
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

import aiofiles
import httpx
import yaml
from fastapi import FastAPI, HTTPException, UploadFile, File, Request
from fastapi.responses import FileResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import verification as _verification

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
SCHEDULED_TASKS_PATH = DATA_DIR / "scheduled_tasks.json"

# WORKSPACE_DIR is the default directory for shell execution and file I/O.
# Set via the WORKSPACE_DIR environment variable (Docker mounts a host folder
# here). Falls back to a local "workspace/" subfolder when running without Docker.
WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", str(ROOT / "workspace")))

for d in (DATA_DIR, SKILLS_DIR, UPLOADS_DIR, CHECKPOINTS_DIR, TASKS_DIR, WORKSPACE_DIR):
    d.mkdir(parents=True, exist_ok=True)

_LOG_DIR = ROOT / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.handlers.RotatingFileHandler(
            _LOG_DIR / "betterwebui.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        ),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("betterwebui")


# ---------------------------------------------------------------------------
# Frontmatter parsing (avoids dependency on specific python-frontmatter API)
# ---------------------------------------------------------------------------

class _FrontmatterPost:
    def __init__(self, meta: dict, content: str) -> None:
        self._meta = meta
        self.content = content

    def get(self, key: str, default=None):
        return self._meta.get(key, default)


def _load_frontmatter(path: Path) -> _FrontmatterPost:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return _FrontmatterPost({}, text)
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == "---":
            end_idx = i
            break
    if end_idx is None:
        return _FrontmatterPost({}, text)
    front_text = "".join(lines[1:end_idx])
    content = "".join(lines[end_idx + 1:])
    try:
        raw = yaml.safe_load(front_text)
    except yaml.YAMLError:
        raw = None
    meta = raw if isinstance(raw, dict) else {}
    return _FrontmatterPost(meta, content)


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
            "verification": {
                "enabled": True,
                "mode": "validators_only",
                "retries": 1,
                "judge_model": "",
                "judge_confidence_threshold": 0.7,
                "tools": {
                    "generate_image": True,
                    "generate_audio": True,
                    "autogui_task": True,
                    "execute_shell": False,
                    "write_file": True,
                    "mcp_call": False,
                },
            },
            "web_search": {
                "provider": "",         # "tavily" | "brave" | "serpapi" | "custom" | ""
                "api_key": "",
                "custom_url": "",
            },
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
    # ---- Cloud services (community-maintained MCP servers) ----
    {
        "id": "gdrive",
        "name": "Google Drive",
        "description": "Browse, search, and read files from Google Drive.",
        "homepage": "https://github.com/modelcontextprotocol/servers-archived/tree/main/src/gdrive",
        "command": "npx",
        "args_template": ["-y", "@modelcontextprotocol/server-gdrive"],
        "env_template": {
            "GDRIVE_CREDENTIALS_PATH": "{credentials_path}",
        },
        "fields": [
            {"name": "credentials_path", "label": "Path to gcp-oauth.keys.json", "type": "path"},
        ],
        "requires": "Node.js plus a Google Cloud OAuth credentials JSON. Run the server once interactively to mint a refresh token.",
        "category": "cloud",
    },
    {
        "id": "google-workspace",
        "name": "Google Workspace",
        "description": "Read Gmail, manage Google Calendar events, and search Drive in one server.",
        "homepage": "https://github.com/taylorwilsdon/google_workspace_mcp",
        "command": "uvx",
        "args_template": ["google-workspace-mcp"],
        "env_template": {
            "GOOGLE_OAUTH_CLIENT_ID": "{client_id}",
            "GOOGLE_OAUTH_CLIENT_SECRET": "{client_secret}",
        },
        "fields": [
            {"name": "client_id", "label": "Google OAuth client ID", "type": "text"},
            {"name": "client_secret", "label": "Google OAuth client secret", "type": "password"},
        ],
        "requires": "Python with uv installed plus a Google Cloud OAuth client. Follow the server's README for the consent-screen setup.",
        "category": "cloud",
    },
    {
        "id": "microsoft-graph",
        "name": "Microsoft 365 (Graph)",
        "description": "Outlook mail, calendar, OneDrive, SharePoint, and Teams via Microsoft Graph.",
        "homepage": "https://github.com/softeria/ms-365-mcp-server",
        "command": "npx",
        "args_template": ["-y", "@softeria/ms-365-mcp-server"],
        "env_template": {
            "MS365_MCP_CLIENT_ID": "{client_id}",
            "MS365_MCP_TENANT_ID": "{tenant_id}",
        },
        "fields": [
            {"name": "client_id", "label": "Azure AD app client ID", "type": "text"},
            {"name": "tenant_id", "label": "Tenant ID (or 'common')", "type": "text"},
        ],
        "requires": "Node.js plus an Azure AD app registration with Microsoft Graph delegated permissions.",
        "category": "cloud",
    },
    {
        "id": "slack",
        "name": "Slack",
        "description": "Read channels, post messages, search history.",
        "homepage": "https://github.com/modelcontextprotocol/servers-archived/tree/main/src/slack",
        "command": "npx",
        "args_template": ["-y", "@modelcontextprotocol/server-slack"],
        "env_template": {
            "SLACK_BOT_TOKEN": "{bot_token}",
            "SLACK_TEAM_ID": "{team_id}",
        },
        "fields": [
            {"name": "bot_token", "label": "Slack bot token (xoxb-...)", "type": "password"},
            {"name": "team_id", "label": "Slack team / workspace ID", "type": "text"},
        ],
        "requires": "Node.js plus a Slack app installed in your workspace with the required scopes.",
        "category": "cloud",
    },
    {
        "id": "notion",
        "name": "Notion",
        "description": "Search, read, and update Notion pages and databases.",
        "homepage": "https://github.com/makenotion/notion-mcp-server",
        "command": "npx",
        "args_template": ["-y", "@notionhq/notion-mcp-server"],
        "env_template": {
            "OPENAPI_MCP_HEADERS": "{headers_json}",
        },
        "fields": [
            {"name": "headers_json", "label": "Headers JSON (e.g. {\"Authorization\":\"Bearer ntn_...\",\"Notion-Version\":\"2022-06-28\"})", "type": "password"},
        ],
        "requires": "Node.js plus a Notion integration token with workspace access.",
        "category": "cloud",
    },
    {
        "id": "linear",
        "name": "Linear",
        "description": "Browse and update Linear issues, projects, and cycles.",
        "homepage": "https://github.com/jerhadf/linear-mcp-server",
        "command": "npx",
        "args_template": ["-y", "linear-mcp-server"],
        "env_template": {
            "LINEAR_API_KEY": "{api_key}",
        },
        "fields": [
            {"name": "api_key", "label": "Linear personal API key", "type": "password"},
        ],
        "requires": "Node.js plus a Linear API key from Settings → API.",
        "category": "cloud",
    },
    {
        "id": "asana",
        "name": "Asana",
        "description": "Read and update Asana tasks, projects, and workspaces.",
        "homepage": "https://github.com/cristip73/mcp-server-asana",
        "command": "npx",
        "args_template": ["-y", "@cristip73/mcp-server-asana"],
        "env_template": {
            "ASANA_ACCESS_TOKEN": "{access_token}",
        },
        "fields": [
            {"name": "access_token", "label": "Asana personal access token", "type": "password"},
        ],
        "requires": "Node.js plus an Asana personal access token from My Settings → Apps.",
        "category": "cloud",
    },
    {
        "id": "jira",
        "name": "Jira",
        "description": "Search, read, and update Jira issues.",
        "homepage": "https://github.com/sooperset/mcp-atlassian",
        "command": "uvx",
        "args_template": ["mcp-atlassian"],
        "env_template": {
            "JIRA_URL": "{jira_url}",
            "JIRA_USERNAME": "{username}",
            "JIRA_API_TOKEN": "{api_token}",
        },
        "fields": [
            {"name": "jira_url", "label": "Jira base URL (e.g. https://acme.atlassian.net)", "type": "text"},
            {"name": "username", "label": "Atlassian account email", "type": "text"},
            {"name": "api_token", "label": "Atlassian API token", "type": "password"},
        ],
        "requires": "Python with uv installed plus an Atlassian API token.",
        "category": "cloud",
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
            env = dict(s.get("env") or {})
            # Substitute OAuth access tokens: {oauth.google.access_token} etc.
            try:
                from services.oauth import get_oauth_token as _get_oauth_tok
                for k, v in list(env.items()):
                    if "{oauth." in str(v):
                        for provider in ("google", "microsoft"):
                            placeholder = f"{{oauth.{provider}.access_token}}"
                            if placeholder in str(v):
                                tok = _get_oauth_tok(provider, DATA_DIR)
                                if tok and tok.get("access_token"):
                                    env[k] = str(v).replace(placeholder, tok["access_token"])
            except Exception:
                pass
            client = MCPStdioClient(
                name=name,
                command=s.get("command", ""),
                args=s.get("args", []),
                env=env,
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

# ---------------------------------------------------------------------------
# Skill loading
# ---------------------------------------------------------------------------

def list_skill_files() -> list[dict]:
    skills = []
    for path in sorted(SKILLS_DIR.glob("*.md")):
        try:
            post = _load_frontmatter(path)
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
    post = _load_frontmatter(path)
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
            post = _load_frontmatter(path)
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

- write_file: write a file to the workspace project folder (REQUIRES
  APPROVAL unless mode is "trusted"). The file lands at
  <project_root>/<filename> — falling back to the server's WORKSPACE_DIR
  when the workspace has no project_root configured. Any pre-existing
  file at that path is snapshotted into checkpoints before being
  overwritten, so the user can revert from the UI. On success the file
  is visible in the Files pane; data_b64 is only returned when the
  on-disk write fails so the user can still retrieve the content.
  Args: {"filename": "name.ext", "content":
  "...", "mime": "text/plain"}.

- delete_file: permanently delete a file from the workspace project folder
  (REQUIRES APPROVAL — the user must confirm before the file is removed).
  Args: {"filename": "name.ext", "reason": "why this file should be deleted"}.

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

- web_search: search the public web. Use only when the user has enabled
  web search for this turn (the system prompt will say so). Args:
  {"query": "...", "max_results": 5}. Returns a list of
  {title, url, snippet} items.

- fetch_url: download and extract the readable text content of a web page.
  Useful after web_search to read the full article. Requires user approval
  unless chat mode is trusted. Args: {"url": "https://..."}.
  Returns {url, title, text, word_count} or {error: "..."}.
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


def build_system_prompt(
    config: dict,
    prompts: dict,
    mode: str = "approve-each",
    *,
    user_memories: Optional[list[str]] = None,
    use_vision: bool = False,
    web_search_mode: str = "off",
) -> str:
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

    # User memories — durable preferences/facts/constraints stored client-side
    # in the browser and injected here on every turn. Subject to context trim.
    if user_memories:
        cleaned = [m.strip() for m in user_memories if isinstance(m, str) and m.strip()]
        if cleaned:
            parts.append(
                "Things to remember about the user:\n"
                + "\n".join(f"- {m}" for m in cleaned[:50])  # hard cap so a runaway list can't blow the budget
            )

    # Per-turn capability hints
    if use_vision:
        parts.append(
            "The user has explicitly asked you to USE VISION on this turn. "
            "If any images are attached, analyse them in detail and incorporate "
            "what you see into your reply."
        )
    if web_search_mode == "required":
        parts.append(
            "The user requires web search on this turn. You MUST call the "
            "web_search tool before answering so your reply reflects current "
            "information."
        )
    elif web_search_mode == "if_needed":
        parts.append(
            "If answering accurately requires current or specialised information "
            "you don't have, call the web_search tool first."
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

    # 5. Integrated services — only advertised when enabled
    from services import state as svc_state
    service_lines: list[str] = []
    if svc_state.is_enabled("autogui"):
        service_lines.append(
            "- autogui_task: drive the desktop GUI (move the mouse, click, type, "
            "open apps, work in any window — including Notepad on Windows or any "
            "other native app) via AutoGUI's ReAct loop. PREFER this over "
            "execute_shell when the user asks to operate a GUI application. "
            "Args: {\"task\": \"natural-language description of what to do\", "
            "\"dry_run\": false}."
        )
    if svc_state.is_enabled("osso"):
        service_lines.append(
            "- screen_windows: list every open window on the user's desktop. "
            "Args: {}."
        )
        service_lines.append(
            "- screen_description: describe a window's contents via accessibility "
            "tree or vision. Args: {\"window_index\": 0, \"mode\": "
            "\"accessibility|vision\"}."
        )
        service_lines.append(
            "- screen_screenshot: capture a screenshot of a window. "
            "Args: {\"window_index\": 0}."
        )
        service_lines.append(
            "- screen_action: perform a precise screen action (click, type, key "
            "press). REQUIRES APPROVAL. Args: {\"action\": \"click|type|key\", "
            "\"x\": 0, \"y\": 0, \"text\": \"text-to-type-or-key-name\"}."
        )
    if svc_state.is_enabled("clk"):
        service_lines.append(
            "- clk_research: start a CognitiveLoopKernel research / reasoning "
            "workflow for deep multi-step analysis. REQUIRES APPROVAL. "
            "Args: {\"command\": \"run\", \"workflow\": \"optional workflow name\", "
            "\"args\": [], \"workspace_id\": \"optional\"}."
        )
    if service_lines and effective_mode != "plan":
        parts.append(
            "Integrated services available as tools (call them like any other "
            "tool via the ```tool block). These extend what you can do beyond "
            "execute_shell:\n" + "\n".join(service_lines)
        )

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
    if not isinstance(raw_args, dict):
        raw_args = {}
    # Some models omit the "args" wrapper and place fields at the top level.
    # Merge any unknown top-level keys into args so the tool handler sees them.
    if not raw_args:
        raw_args = {k: v for k, v in call.items() if k not in ("tool", "args")}
    return {
        "tool": call["tool"],
        "args": raw_args,
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
    ckpt_dir = CHECKPOINTS_DIR / workspace_id / _ckpt_key(filename)
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    ckpt_id = f"{int(time.time())}_{uuid.uuid4().hex[:6]}"
    ckpt_path = ckpt_dir / f"{ckpt_id}.bin"
    ckpt_path.write_bytes(content)
    return ckpt_id


def _list_checkpoints(workspace_id: str, filename: str) -> list[dict]:
    ckpt_dir = CHECKPOINTS_DIR / workspace_id / _ckpt_key(filename)
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
    base = CHECKPOINTS_DIR / workspace_id / _ckpt_key(filename)
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
            results = body.get("results") if isinstance(body, dict) else body
            if not isinstance(results, list):
                return {"error": "Custom search did not return a 'results' list."}
            return {"query": query, "provider": "custom", "results": results[:max_results]}

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


async def chat_complete(messages: list, model: str, config: dict, chat_id: str = "") -> tuple[str, dict]:
    """Returns (text, usage_dict)."""
    base = normalize_base_url(config["base_url"])
    profile = active_profile(config)
    headers = {"Authorization": f"Bearer {config.get('api_key', '')}"}
    payload: dict = {"model": model, "messages": messages, "stream": False}
    if chat_id and profile.get("name") == "openwebui":
        payload["chat_id"] = chat_id
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
            except Exception:
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
        from services.clients import get_clk_client
        from services import state as svc_state
        import httpx as _httpx
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
        from services.clients import get_autogui_client
        from services import state as svc_state
        import httpx as _httpx
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
            client = get_autogui_client()
            return await client.start_task(task=task_desc, model=model or None, dry_run=dry_run)
        except (_httpx.ConnectError, _httpx.TimeoutException, _httpx.TransportError) as e:
            return {"error": f"AutoGUI is enabled but could not be reached. ({e})"}

    if tool == "screen_windows":
        from services.clients import get_osso_client
        from services import state as svc_state
        import httpx as _httpx
        if not svc_state.is_enabled("osso"):
            return {"error": "OSScreenObserver is disabled. Enable it in Settings > Services."}
        try:
            return await get_osso_client().windows()
        except (_httpx.ConnectError, _httpx.TimeoutException, _httpx.TransportError) as e:
            return {"error": f"OSScreenObserver is enabled but could not be reached. ({e})"}

    if tool == "screen_description":
        from services.clients import get_osso_client
        from services import state as svc_state
        import httpx as _httpx
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
        from services.clients import get_osso_client
        from services import state as svc_state
        import httpx as _httpx
        if not svc_state.is_enabled("osso"):
            return {"error": "OSScreenObserver is disabled. Enable it in Settings > Services."}
        try:
            return await get_osso_client().screenshot(window_index=args.get("window_index"))
        except (_httpx.ConnectError, _httpx.TimeoutException, _httpx.TransportError) as e:
            return {"error": f"OSScreenObserver is enabled but could not be reached. ({e})"}

    if tool == "screen_action":
        from services.clients import get_osso_client
        from services import state as svc_state
        import httpx as _httpx
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


_transient_sweep_task: Optional[asyncio.Task] = None
_scheduler_task: Optional[asyncio.Task] = None


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


@app.on_event("startup")
async def _startup() -> None:
    global _transient_sweep_task, _scheduler_task
    try:
        await mcp_manager.reconcile()
    except Exception as exc:
        print(f"[BetterWebUI] MCP startup error: {exc}")
    # One sweep at boot so test fixtures get a clean state.
    try:
        _sweep_transient_uploads()
    except Exception:
        pass
    _transient_sweep_task = asyncio.create_task(_transient_sweep_loop())
    try:
        from scheduler import start_scheduler
        _scheduler_task = asyncio.create_task(start_scheduler(
            tasks_path=SCHEDULED_TASKS_PATH,
            run_callback=_run_scheduled_task,
            send_notification=_emit_scheduled_notification,
        ))
    except Exception as exc:
        logging.getLogger("betterwebui.scheduler").warning("Scheduler failed to start: %s", exc)


@app.on_event("shutdown")
async def _shutdown() -> None:
    if _transient_sweep_task is not None:
        _transient_sweep_task.cancel()
    if _scheduler_task is not None:
        _scheduler_task.cancel()
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
    verification: Optional[dict] = None
    web_search: Optional[dict] = None


def _public_config(cfg: dict, include_paths: bool = False) -> dict:
    safe = dict(cfg)
    safe["api_key_set"] = bool(safe.get("api_key"))
    safe["api_key"] = ""
    profile = safe.get("api_profile")
    if isinstance(profile, dict):
        safe["api_profile_label"] = profile.get("label", profile.get("name", ""))
    else:
        safe["api_profile_label"] = ""
    # workspace_dir is the absolute server path; only return it to local
    # callers (UI hint) so a network-exposed server doesn't leak server
    # filesystem layout in every config response.
    if include_paths:
        safe["workspace_dir"] = str(Path(WORKSPACE_DIR).resolve())
    return safe


def _is_local_caller(request: Request) -> bool:
    try:
        _require_local_caller(request)
        return True
    except HTTPException:
        return False


@app.get("/api/config")
async def get_config(request: Request):
    return _public_config(load_config(), include_paths=_is_local_caller(request))


@app.post("/api/config")
async def set_config(patch: ConfigPatch, request: Request):
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
    return _public_config(cfg, include_paths=_is_local_caller(request))


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
async def approve(a: ApprovalIn, request: Request):
    # Approving an in-flight tool call can release shell/file-write side
    # effects, so restrict the endpoint to local callers up front (matching
    # /api/project/* and /api/session/trust). Remote clients can't approve
    # tool calls without the operator's local browser.
    _require_local_caller(request)
    ok = approvals.resolve(a.approval_id, a.approved)
    if not ok:
        raise HTTPException(404, "Unknown approval id")
    if a.approved and a.trust_session and a.command:
        _session_trusted_commands.add(a.command)
    return {"ok": True}


class SessionTrustIn(BaseModel):
    command: str


_DOCKER_BRIDGE_RANGE = ipaddress.ip_network("172.16.0.0/12")


def _require_local_caller(request: Request) -> None:
    """Reject requests that don't come from a local client.

    Session-trust state and the project-file endpoints can affect on-disk
    state, so they shouldn't be reachable from arbitrary remote hosts when
    the server is bound to 0.0.0.0. Default: loopback only. Docker bridge
    and broader LAN access are opt-in via env vars:
      BETTERWEBUI_ALLOW_DOCKER=true → also accept 172.16.0.0/12 (the
                                      Docker bridge range). docker-compose.yml
                                      sets this by default so the
                                      containerized UI just works.
      BETTERWEBUI_ALLOW_LAN=true    → accept any RFC1918 private IP
      BETTERWEBUI_REQUIRE_LOCAL=strict → loopback only (overrides both above)

    The narrower default protects bare-metal deployments on real LANs that
    happen to use 172.16/12 — they no longer leak local-only endpoints to
    LAN peers just because the network's CIDR overlaps Docker's range.
    """
    client_host = (request.client.host if request.client else "") or ""
    if client_host in ("127.0.0.1", "::1", "localhost", "testclient"):
        return
    mode = os.environ.get("BETTERWEBUI_REQUIRE_LOCAL", "").lower()
    if mode == "strict":
        raise HTTPException(403, "This endpoint is limited to local callers.")
    try:
        addr = ipaddress.ip_address(client_host)
    except ValueError:
        raise HTTPException(403, "This endpoint is limited to local callers.")
    if addr.is_loopback:
        return
    # Docker bridge range is opt-in (default off) so a bare-metal install on
    # a real 172.16/12 LAN doesn't accidentally expose local-only endpoints.
    allow_docker = os.environ.get("BETTERWEBUI_ALLOW_DOCKER", "").lower() in ("1", "true", "yes")
    if allow_docker and isinstance(addr, ipaddress.IPv4Address) and addr in _DOCKER_BRIDGE_RANGE:
        return
    # Broader LAN access is also opt-in.
    allow_lan = os.environ.get("BETTERWEBUI_ALLOW_LAN", "").lower() in ("1", "true", "yes")
    if allow_lan and addr.is_private:
        return
    raise HTTPException(403, "This endpoint is limited to local callers.")


@app.post("/api/session/trust")
async def session_trust(t: SessionTrustIn, request: Request):
    _require_local_caller(request)
    _session_trusted_commands.add(t.command)
    return {"ok": True, "trusted_count": len(_session_trusted_commands)}


@app.get("/api/session/trust")
async def list_session_trust(request: Request):
    _require_local_caller(request)
    return {"commands": list(_session_trusted_commands)}


@app.delete("/api/session/trust")
async def clear_session_trust(request: Request):
    _require_local_caller(request)
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
async def list_workspaces_endpoint(request: Request):
    _require_local_caller(request)
    return load_workspaces()


@app.get("/api/workspaces/{wid}")
async def get_workspace(request: Request, wid: str):
    _require_local_caller(request)
    data = load_workspaces()
    w = next((x for x in data["workspaces"] if x["id"] == wid), None)
    if not w:
        raise HTTPException(404, "Workspace not found")
    return w


@app.post("/api/workspaces")
async def upsert_workspace(w: WorkspaceIn, request: Request):
    _require_local_caller(request)
    # Reject project_root values that escape WORKSPACE_DIR up front so the user
    # gets actionable feedback. Relative paths are resolved against
    # WORKSPACE_DIR (e.g., "my-project" → "<workspace_dir>/my-project") rather
    # than the server process CWD. _resolve_project_root still clamps as
    # defense-in-depth.
    if w.project_root:
        base = Path(WORKSPACE_DIR).resolve()
        candidate_path = Path(w.project_root)
        if not candidate_path.is_absolute():
            candidate_path = base / candidate_path
        try:
            normalized = candidate_path.resolve()
            normalized.relative_to(base)
        except (ValueError, OSError):
            # Don't include the resolved base path in the error message: this
            # endpoint is gated by _require_local_caller, but defense-in-depth
            # is cheap and we still avoid serializing absolute filesystem
            # paths in any HTTP response body.
            raise HTTPException(
                400,
                "project_root must be inside the configured workspace directory.",
            )
        # Create the directory if it doesn't exist yet so /api/project/tree
        # and execute_shell can use it immediately without "no such directory"
        # surprises. The path is already proven safe (under WORKSPACE_DIR).
        try:
            normalized.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(
                400,
                f"Could not create project_root: {exc}",
            )
        if not normalized.is_dir():
            raise HTTPException(
                400,
                "project_root exists but is not a directory.",
            )
        # Persist the normalized absolute path so downstream code uses a
        # consistent value regardless of what the user typed.
        w.project_root = str(normalized)
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
async def delete_workspace(request: Request, wid: str):
    _require_local_caller(request)
    data = load_workspaces()
    data["workspaces"] = [x for x in data["workspaces"] if x["id"] != wid]
    save_json(WORKSPACES_PATH, data)
    cfg = load_config()
    if cfg.get("active_workspace_id") == wid:
        cfg["active_workspace_id"] = ""
        save_json(CONFIG_PATH, cfg)
    return {"ok": True}


@app.get("/api/workspaces/{wid}/export")
async def export_workspace(request: Request, wid: str):
    _require_local_caller(request)
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
        # Bundle manifest (filenames + hashes provided by client via query param)
        # The actual file bytes are NOT included — the manifest is just metadata so
        # the recipient knows which bundles existed and can provide the files themselves.
        bundle_manifest = w.get("bundle_manifest")
        if bundle_manifest and isinstance(bundle_manifest, list):
            zf.writestr("bundle_manifest.json", json.dumps(bundle_manifest, indent=2))
    buf.seek(0)
    safe_name = _slug(w["name"], "workspace")
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.bwui"'},
    )


@app.post("/api/workspaces/{wid}/bundle-manifest")
async def set_workspace_bundle_manifest(request: Request, wid: str):
    """Client posts bundle metadata (filenames + hashes) to be included in exports."""
    _require_local_caller(request)
    body = await request.json()
    manifest = body.get("manifest") if isinstance(body, dict) else None
    if not isinstance(manifest, list):
        raise HTTPException(400, "Expected {'manifest': [...]} body.")
    data = load_workspaces()
    w = next((x for x in data["workspaces"] if x["id"] == wid), None)
    if not w:
        raise HTTPException(404, "Workspace not found")
    safe_manifest = []
    for entry in manifest[:200]:
        if not isinstance(entry, dict):
            continue
        safe_manifest.append({
            "bundle_id": str(entry.get("bundle_id", ""))[:64],
            "name": str(entry.get("name", ""))[:128],
            "files": [
                {"filename": str(f.get("filename", ""))[:256], "sha256": str(f.get("sha256", ""))[:64]}
                for f in (entry.get("files") or [])[:500]
                if isinstance(f, dict)
            ],
        })
    idx = next((i for i, x in enumerate(data["workspaces"]) if x["id"] == wid), None)
    data["workspaces"][idx]["bundle_manifest"] = safe_manifest
    save_json(WORKSPACES_PATH, data)
    return {"ok": True, "bundle_count": len(safe_manifest)}


_MAX_BUNDLE_BYTES = 10 * 1024 * 1024       # 10 MB compressed
_MAX_MEMBER_BYTES = 2 * 1024 * 1024        # 2 MB per uncompressed member
_MAX_BUNDLE_MEMBERS = 500                  # cap member count to bound iteration cost
_MAX_BUNDLE_TOTAL_BYTES = 50 * 1024 * 1024  # cap total uncompressed bytes (zip-bomb guard)


@app.post("/api/workspaces/import")
async def import_workspace(request: Request, file: UploadFile = File(...)):
    _require_local_caller(request)
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
            # Import skills. Skip any skill whose target filename already
            # exists in SKILLS_DIR so a bundle can't silently overwrite a
            # user's local skill with the same name. The names of the
            # skipped files come back in the response so the UI can show
            # the user what wasn't imported.
            imported_skills: list[str] = []
            skipped_skills: list[str] = []
            for name in names:
                if name.startswith("skills/") and name.endswith(".md"):
                    skill_bytes = zf.read(name)
                    dest = SKILLS_DIR / Path(name).name
                    if dest.exists():
                        skipped_skills.append(dest.name)
                        continue
                    dest.write_bytes(skill_bytes)
                    imported_skills.append(dest.name)
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
        return {
            "id": wid,
            "name": manifest.get("name"),
            "imported_skills": imported_skills,
            "skipped_skills": skipped_skills,
        }
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

    # If a template was requested, validate it before flipping onboarding_done
    # so a bad template_id can't permanently skip the wizard.
    if body.template_id:
        tmpl = next((t for t in ONBOARDING_TEMPLATES if t["id"] == body.template_id), None)
        if not tmpl:
            raise HTTPException(400, f"Unknown onboarding template '{body.template_id}'.")
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
        # Set as active and only now flip onboarding_done, so partial failures
        # above re-raise (caller can retry) instead of locking out the wizard.
        cfg["active_workspace_id"] = wid
        cfg["onboarding_done"] = True
        save_json(CONFIG_PATH, cfg)
        return {"ok": True, "workspace_id": wid, "workspace_name": ws_name}

    cfg["onboarding_done"] = True
    save_json(CONFIG_PATH, cfg)
    return {"ok": True}


# --- Project (file tree + checkpoints) ---

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
    base = Path(WORKSPACE_DIR).resolve()
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


@app.get("/api/project/tree")
async def project_tree(request: Request, path: str = ""):
    _require_local_caller(request)
    cfg = load_config()
    workspace = resolve_active_workspace(cfg)
    root, clamped = _resolve_project_root_info(workspace)
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
            # Skip symlinks entirely so a link inside the project root that
            # points outside doesn't leak the target's metadata (size/mtime)
            # in the listing. /api/project/file already blocks reading them.
            if p.is_symlink():
                continue
            rel = str(p.relative_to(root_path))
            # Use lstat to be explicit about not following links; the
            # is_symlink() check above already filters them, but lstat keeps
            # the metadata accurate for the entry we list.
            st = p.lstat()
            if p.is_dir():
                entries.append({"type": "dir", "name": p.name, "path": rel})
            else:
                entries.append({
                    "type": "file",
                    "name": p.name,
                    "path": rel,
                    "size": st.st_size,
                    "modified_at": int(st.st_mtime),
                    "ext": p.suffix.lower(),
                })
    except Exception as exc:
        raise HTTPException(500, f"Could not list directory: {exc}")
    # Don't leak the absolute filesystem root to the client; the frontend
    # only needs the relative entries to render and request further paths.
    # Two flags so the UI can show three distinct states:
    #   * project_root_set=false                       → no value configured
    #   * project_root_set=false, project_root_clamped → invalid value, fell
    #                                                    back to the safe base
    #   * project_root_set=true                        → configured and honored
    has_value = bool((workspace or {}).get("project_root"))
    project_root_set = has_value and not clamped
    project_root_clamped = has_value and clamped
    return {
        "entries": entries,
        "project_root_set": project_root_set,
        "project_root_clamped": project_root_clamped,
    }


_MAX_PROJECT_FILE_BYTES = 1 * 1024 * 1024  # 1 MB cap for /api/project/file


@app.get("/api/project/file")
async def project_file(request: Request, path: str, include_content: bool = False):
    _require_local_caller(request)
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
    if not full.is_file():
        raise HTTPException(400, "Path is not a file.")
    size = full.stat().st_size
    truncated = size > _MAX_PROJECT_FILE_BYTES
    content = None
    is_binary = False
    if include_content:
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
        except Exception as exc:
            raise HTTPException(500, f"Could not read file: {exc}")
    else:
        # Metadata-only path: sniff a small header to flag binary so the UI can
        # decide whether to do a follow-up include_content=true fetch. This
        # avoids reading the full 1 MB body when the caller only wants metadata.
        try:
            with full.open("rb") as fh:
                header = fh.read(4096)
            if b"\x00" in header:
                is_binary = True
            else:
                try:
                    header.decode("utf-8", errors="strict")
                    is_binary = False
                except UnicodeDecodeError:
                    is_binary = True
        except Exception:
            # If even the header read fails, default to "looks binary" so the
            # UI shows the preview-not-available branch instead of attempting
            # a follow-up content fetch that will likely also fail.
            is_binary = True
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
    # include_content gates the body for both text and binary so the frontend
    # can do a cheap metadata-only first request before deciding to fetch the
    # bytes. Defaults to false (set in the route signature).
    if include_content:
        payload["content"] = content
    return payload


@app.get("/api/project/checkpoints")
async def list_project_checkpoints(request: Request, filename: str):
    _require_local_caller(request)
    cfg = load_config()
    workspace = resolve_active_workspace(cfg)
    wid = (workspace or {}).get("id", "default")
    return {"checkpoints": _list_checkpoints(wid, filename)}


class RevertIn(BaseModel):
    filename: str
    checkpoint_id: str


@app.post("/api/project/revert")
async def revert_project_file(r: RevertIn, request: Request):
    _require_local_caller(request)
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
        # content is raw bytes — binary checkpoints round-trip without loss.
        dest.write_bytes(content)
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


# --- Transient uploads (per-chat, TTL-swept). Used for file bundles that
# live in browser IndexedDB and are streamed up only for the duration of
# the chat turn — keeps sensitive bytes off the server long-term.

_TRANSIENT_TTL_SECONDS = 24 * 3600


def _transient_root() -> Path:
    """Resolve the transient-uploads directory lazily from the current
    UPLOADS_DIR. Lazy resolution lets test fixtures rebind UPLOADS_DIR
    without these endpoints pointing at the stale module-load value."""
    root = UPLOADS_DIR / "transient"
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


@app.post("/api/uploads/transient")
async def upload_transient_file(request: Request, file: UploadFile = File(...)):
    """Accept a file scoped to a single chat. Files older than the TTL
    are swept automatically. The caller passes chat_id as a query param."""
    _require_local_caller(request)
    chat_id = request.query_params.get("chat_id") or "anon"
    # Sanitize chat_id: alphanumeric / dash / underscore only.
    chat_id = re.sub(r"[^A-Za-z0-9_-]+", "_", chat_id)[:64].strip("._-") or "anon"
    chat_dir = _transient_root() / chat_id
    chat_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex}_{Path(file.filename or 'file').name}"
    dest = chat_dir / safe_name
    async with aiofiles.open(dest, "wb") as f:
        while chunk := await file.read(1024 * 64):
            await f.write(chunk)
    return {
        "url": f"/uploads/transient/{chat_id}/{safe_name}",
        "filename": file.filename,
        "content_type": file.content_type,
    }


@app.delete("/api/uploads/transient/{chat_id}")
async def delete_transient_chat(chat_id: str, request: Request):
    _require_local_caller(request)
    chat_id = re.sub(r"[^A-Za-z0-9_-]+", "_", chat_id)[:64].strip("._-")
    if not chat_id:
        raise HTTPException(400, "Invalid chat_id.")
    chat_dir = _transient_root() / chat_id
    if chat_dir.exists():
        shutil.rmtree(chat_dir, ignore_errors=True)
    return {"ok": True, "chat_id": chat_id}


# --- Scheduled tasks ---

# Queue of pending scheduled-task notifications. The /api/scheduled-tasks/notifications/stream
# SSE endpoint drains this and pushes to the browser; once delivered the
# in-memory list is cleared. We persist nothing — recently-missed
# notifications can still be read from each task's history field.
_scheduled_notifications: list[dict] = []


async def _emit_scheduled_notification(task: dict, result: dict) -> None:
    _scheduled_notifications.append({
        "id": task.get("id"),
        "name": task.get("name"),
        "ok": bool(result.get("ok", True)),
        "summary": (result.get("summary") or "")[:500],
        "ts": time.time(),
    })


async def _run_scheduled_task(task: dict) -> dict:
    """Execute a scheduled task by running its prompt through the same code
    path as /api/chat. Returns {ok, summary}."""
    cfg = load_config()
    if not cfg.get("api_key") or not cfg.get("base_url"):
        return {"ok": False, "summary": "BetterWebUI is not connected to a backend."}
    # Honour the task's workspace if it specifies one.
    if task.get("workspace_id"):
        cfg = dict(cfg)
        cfg["active_workspace_id"] = task["workspace_id"]
    prompts = load_prompts()
    model = cfg.get("default_model") or ""
    if not model:
        return {"ok": False, "summary": "No default model configured."}
    system_prompt = build_system_prompt(cfg, prompts, mode="trusted")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": (task.get("prompt") or "").strip() or "(no prompt)"},
    ]
    try:
        text, _usage = await chat_complete(messages, model, cfg)
    except Exception as exc:
        return {"ok": False, "summary": f"LLM call failed: {exc}"}
    return {"ok": True, "summary": (text or "").strip()[:1000]}


class ScheduledTaskIn(BaseModel):
    id: Optional[str] = None
    name: str
    prompt: str
    workspace_id: Optional[str] = ""
    schedule: dict
    enabled: Optional[bool] = True


@app.get("/api/scheduled-tasks")
async def list_scheduled_tasks(request: Request):
    _require_local_caller(request)
    from scheduler import list_tasks
    return {"tasks": list_tasks(SCHEDULED_TASKS_PATH)}


@app.post("/api/scheduled-tasks")
async def create_or_update_scheduled_task(body: ScheduledTaskIn, request: Request):
    _require_local_caller(request)
    from scheduler import upsert_task
    task = body.model_dump()
    if not task.get("id"):
        task["id"] = uuid.uuid4().hex
    task.setdefault("history", [])
    task.setdefault("last_run_at", None)
    return upsert_task(SCHEDULED_TASKS_PATH, task)


@app.delete("/api/scheduled-tasks/{task_id}")
async def delete_scheduled_task(task_id: str, request: Request):
    _require_local_caller(request)
    from scheduler import delete_task
    ok = delete_task(SCHEDULED_TASKS_PATH, task_id)
    if not ok:
        raise HTTPException(404, "Task not found.")
    return {"ok": True}


@app.get("/api/verification/{chat_id}")
async def get_verification_log(chat_id: str, request: Request):
    """Return verification trace entries for a chat (one per tool call)."""
    _require_local_caller(request)
    chat_id = re.sub(r"[^A-Za-z0-9_-]+", "_", chat_id)[:128].strip("._-")
    if not chat_id:
        raise HTTPException(400, "Invalid chat_id.")
    path = DATA_DIR / "verification" / f"{chat_id}.jsonl"
    if not path.exists():
        return {"entries": []}
    entries: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except Exception:
                    continue
    except Exception as exc:
        raise HTTPException(500, f"Could not read verification log: {exc}")
    return {"entries": entries}


@app.get("/api/scheduled-tasks/notifications")
async def drain_scheduled_notifications(request: Request):
    """Poll-style: returns and clears pending notifications. The frontend
    polls this on a short interval rather than holding an SSE open."""
    _require_local_caller(request)
    pending = list(_scheduled_notifications)
    _scheduled_notifications.clear()
    return {"notifications": pending}


# --- Memory extraction (client-side stored, server only synthesizes). ---

class MemoryExtractIn(BaseModel):
    user_message: str
    assistant_message: str
    model: Optional[str] = None


@app.post("/api/memory/extract")
async def memory_extract(body: MemoryExtractIn, request: Request):
    _require_local_caller(request)
    cfg = load_config()
    model = body.model or cfg.get("default_model") or ""
    if not model or not cfg.get("api_key") or not cfg.get("base_url"):
        return {"candidates": []}
    user_msg = (body.user_message or "")[:4000]
    assistant_msg = (body.assistant_message or "")[:2000]
    extraction_prompt = (
        "Examine this single user message and identify any DURABLE preferences, "
        "facts, or constraints the user revealed that would help in future chats. "
        "Examples of good memories: 'User is vegetarian', 'User prefers Python', "
        "'User's company is named Acme'. Skip ephemeral things like a question "
        "they just asked or a one-off task.\n\n"
        f"User message:\n{user_msg}\n\n"
        f"Assistant reply (for context):\n{assistant_msg}\n\n"
        "Respond with JSON ONLY in this exact shape: "
        '{"candidates": [{"text": "User ...", "category": "preference|fact|constraint|other"}]} '
        "or {\"candidates\": []} if nothing notable."
    )
    messages = [
        {"role": "system", "content": "You are a careful assistant that returns JSON only."},
        {"role": "user", "content": extraction_prompt},
    ]
    try:
        text, _usage = await chat_complete(messages, model, cfg)
    except Exception as exc:
        return {"candidates": [], "error": str(exc)[:200]}
    parsed = _verification._safe_json_parse(text)
    if not isinstance(parsed, dict):
        return {"candidates": []}
    raw_candidates = parsed.get("candidates") if isinstance(parsed.get("candidates"), list) else []
    cleaned: list[dict] = []
    for c in raw_candidates[:5]:
        if not isinstance(c, dict):
            continue
        t = (c.get("text") or "").strip()
        if not t or len(t) > 280:
            continue
        cat = (c.get("category") or "other").strip().lower()
        if cat not in {"preference", "fact", "constraint", "other"}:
            cat = "other"
        cleaned.append({"text": t, "category": cat})
    return {"candidates": cleaned}


# --- OAuth helper endpoints ---

@app.get("/api/oauth/status/{provider}")
async def oauth_status(provider: str, request: Request):
    _require_local_caller(request)
    from services.oauth import get_oauth_status
    return get_oauth_status(provider, DATA_DIR)


@app.post("/api/oauth/connect/{provider}")
async def oauth_connect(provider: str, request: Request):
    """Return an authorization URL for the user to open in their browser."""
    _require_local_caller(request)
    cfg = load_config()
    try:
        from services.oauth import start_oauth_flow
        auth_url = await start_oauth_flow(provider, cfg, DATA_DIR)
        return {"auth_url": auth_url}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"OAuth connect failed: {exc}")


@app.delete("/api/oauth/disconnect/{provider}")
async def oauth_disconnect(provider: str, request: Request):
    """Remove stored OAuth token."""
    _require_local_caller(request)
    from services.oauth import revoke_oauth_token
    removed = revoke_oauth_token(provider, DATA_DIR)
    return {"removed": removed}


# --- Voice transcription ---

_MAX_TRANSCRIBE_BYTES = 25 * 1024 * 1024  # 25 MB cap for /api/transcribe uploads


@app.post("/api/transcribe")
async def transcribe_audio(request: Request, file: UploadFile = File(...)):
    # Proxies user-API-key requests to the backend, so restrict to local
    # callers (matches /api/tts and /api/explain-command).
    _require_local_caller(request)
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
async def tts_endpoint(body: TtsIn, request: Request):
    _require_local_caller(request)
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
async def explain_command(body: ExplainCommandIn, request: Request):
    _require_local_caller(request)
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
async def list_conversations(request: Request):
    _require_local_caller(request)
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


@app.get("/api/conversations/recent")
async def recent_conversations(request: Request, limit: int = 3):
    """Return the most-recently-updated conversations with their one-line summaries."""
    _require_local_caller(request)
    data = load_conversations()
    convs = sorted(
        [{"id": cid, **conv} for cid, conv in data["conversations"].items()],
        key=lambda x: -x.get("updated_at", 0),
    )[:max(1, min(10, limit))]
    return {"recent": [
        {
            "id": c["id"],
            "title": c.get("title", "Untitled"),
            "updated_at": c.get("updated_at", 0),
            "summary": c.get("summary", ""),
            "message_count": len(c.get("messages", [])),
        }
        for c in convs
    ]}


@app.post("/api/conversations/{cid}/summary")
async def set_conversation_summary(request: Request, cid: str):
    """Store a one-line summary for a conversation (generated client-side or by the LLM)."""
    _require_local_caller(request)
    body = await request.json()
    summary = str(body.get("summary", ""))[:300].strip()
    data = load_conversations()
    conv = data["conversations"].get(cid)
    if not conv:
        raise HTTPException(404, "Not found")
    conv["summary"] = summary
    save_json(CONVERSATIONS_PATH, data)
    return {"ok": True}


@app.get("/api/conversations/search")
async def search_conversations(request: Request, q: str = ""):
    _require_local_caller(request)
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
async def get_conversation(request: Request, cid: str):
    _require_local_caller(request)
    data = load_conversations()
    conv = data["conversations"].get(cid)
    if not conv:
        raise HTTPException(404, "Not found")
    return conv


@app.delete("/api/conversations/{cid}")
async def delete_conversation(request: Request, cid: str):
    _require_local_caller(request)
    data = load_conversations()
    data["conversations"].pop(cid, None)
    save_json(CONVERSATIONS_PATH, data)
    return {"ok": True}


class PinIn(BaseModel):
    pinned: bool


@app.post("/api/conversations/{cid}/pin")
async def pin_conversation(request: Request, cid: str, body: PinIn):
    _require_local_caller(request)
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
async def tag_conversation(request: Request, cid: str, body: TagIn):
    _require_local_caller(request)
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
async def fork_conversation(request: Request, cid: str, body: ForkIn):
    _require_local_caller(request)
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
    # Copy schema-shaped fields from the source so the forked conversation
    # matches what save_conversation/load_conversations produce elsewhere.
    # Forks start unpinned with the parent's tags, workspace, and current
    # task plan snapshot — but as a brand-new conversation otherwise.
    now = int(time.time())
    data["conversations"][new_cid] = {
        "id": new_cid,
        "title": title,
        "messages": forked_messages,
        "parent_id": cid,
        "pinned": False,
        "tags": list(conv.get("tags", [])),
        "task_plan": list(conv.get("task_plan", [])),
        "workspace_id": conv.get("workspace_id", ""),
        "updated_at": now,
        "created_at": now,
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


# --- Chat (the main loop) ---

class ChatRequest(BaseModel):
    conversation_id: Optional[str] = None
    messages: list
    model: Optional[str] = None
    title: Optional[str] = None
    mode: Optional[str] = None
    # Per-turn capability switches set by the composer toggles.
    use_vision: Optional[bool] = None
    web_search_mode: Optional[str] = None  # "off" | "if_needed" | "required"
    user_memories: Optional[list[str]] = None
    bundle_attachments: Optional[list[dict]] = None


_VALID_ROLES = {"system", "user", "assistant", "function", "tool", "developer"}


def to_openai_messages(history: list, system_prompt: str) -> list:
    out = [{"role": "system", "content": system_prompt}]
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
            parts = [{"type": "text", "text": content}] if content else []
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


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request):
    # /api/chat can drive side-effecting tools (execute_shell, write_file,
    # cli_call) which only require an /api/approve from the same operator.
    # Restricting both endpoints to local callers means a network-exposed
    # server can't be used to ride the operator's approval pipeline.
    _require_local_caller(request)
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
        history = []
        for m in req.messages:
            if not isinstance(m, dict) or m.get("role") not in {"user", "assistant"}:
                continue
            if m.get("content") is None:
                m = {**m, "content": ""}
            history.append(m)

        # Merge bundle_attachments from mounted file workspaces into the most
        # recent user message so the model has access without the user having
        # to re-attach. We splice rather than replace so per-message attachments
        # the user added in the composer survive.
        if req.bundle_attachments and history:
            last_user_idx = next(
                (i for i in range(len(history) - 1, -1, -1) if history[i].get("role") == "user"),
                None,
            )
            if last_user_idx is not None:
                msg = dict(history[last_user_idx])
                existing = list(msg.get("attachments") or [])
                extras = [a for a in req.bundle_attachments if isinstance(a, dict) and a.get("url")]
                msg["attachments"] = existing + extras
                history[last_user_idx] = msg

        system_prompt = build_system_prompt(
            cfg, prompts, effective_mode,
            user_memories=req.user_memories,
            use_vision=bool(req.use_vision),
            web_search_mode=(req.web_search_mode or "off"),
        )
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
                        *[chat_complete(openai_messages, model, cfg, chat_id=f"{cid}-{i}") for i in range(consensus_runs)],
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
                        text, usage = await chat_complete(synthesis_messages, model, cfg, chat_id=cid)
                else:
                    text, usage = await chat_complete(openai_messages, model, cfg, chat_id=cid)

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

                # Capture the user's most recent message as the goal for the
                # verification judge. Falls back to empty string if absent.
                user_goal_for_verif = ""
                for _m in reversed(history):
                    if _m.get("role") == "user":
                        user_goal_for_verif = (_m.get("content") or "")[:2000]
                        break

                async def _execute_with_args(args_override: dict):
                    new_call = {"tool": call["tool"], "args": args_override}
                    return await execute_tool(new_call, cfg, send_event, effective_mode, model)

                async def _screenshot_provider():
                    try:
                        from services import state as _svc_state
                        if not _svc_state.is_enabled("osso"):
                            return None
                        from services.clients import get_osso_client
                        shot = await get_osso_client().screenshot()
                        if isinstance(shot, dict) and shot.get("image_b64"):
                            return shot["image_b64"]
                        if isinstance(shot, dict) and shot.get("data_b64"):
                            return shot["data_b64"]
                    except Exception:
                        return None
                    return None

                first_result = await execute_tool(call, cfg, send_event, effective_mode, model)

                try:
                    result, vtrace = await _verification.verify_and_maybe_retry(
                        tool=call["tool"],
                        args=call["args"],
                        result=first_result,
                        goal=user_goal_for_verif,
                        config=cfg,
                        execute_again=_execute_with_args,
                        chat_complete=chat_complete,
                        screenshot_provider=_screenshot_provider,
                    )
                except Exception:
                    result, vtrace = first_result, None

                # Emit tool_result first so the UI's checkpoint cache is
                # populated before the verification card (which may want to
                # render an Undo button) arrives.
                await send_event("tool_result", {"tool": call["tool"], "result": result})

                if vtrace is not None and vtrace.events:
                    await send_event("verification", vtrace.to_dict())
                    # Append one JSONL line per verification decision so
                    # power users / debugging can audit after the fact.
                    try:
                        _verif_log_dir = DATA_DIR / "verification"
                        _verif_log_dir.mkdir(parents=True, exist_ok=True)
                        with open(_verif_log_dir / f"{cid}.jsonl", "a", encoding="utf-8") as _vf:
                            _vf.write(json.dumps({
                                "ts": time.time(),
                                "chat_id": cid,
                                "tool": call["tool"],
                                "trace": vtrace.to_dict(),
                            }) + "\n")
                    except Exception:
                        pass

                # Auto-engage consensus when the judge fails repeatedly on
                # the same turn — surfaced via a notice, then we recompute.
                if (
                    vtrace is not None
                    and not vtrace.final_ok
                    and cfg.get("verification", {}).get("mode") == "validators_and_judge"
                    and cfg.get("consensus_runs", 1) <= 1
                ):
                    await send_event("notice", {
                        "message": "I wasn't confident in that result. I'll double-check on the next turn.",
                    })

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


# ─── Services integration ────────────────────────────────────────────────────

from services.routes import register_routes as _register_service_routes

_register_service_routes(app)


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
