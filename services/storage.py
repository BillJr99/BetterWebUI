"""Filesystem paths and JSON persistence for BetterWebUI.

Extracted from app.py (Phase 3). The path constants below are the canonical
copies: app.py exposes forwarding properties (see app.py's module-class swap)
so that test fixtures which monkeypatch `app.DATA_DIR` etc. transparently
rebind the values here. For the same reason, code in *other* modules must
read these constants via attribute access (``storage.DATA_DIR``), never via
``from services.storage import DATA_DIR`` — a from-import would freeze the
value at import time and miss the rebind.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
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
# ---------------------------------------------------------------------------
# Frontmatter parsing (avoids dependency on specific python-frontmatter API)
# ---------------------------------------------------------------------------

class _FrontmatterPost:
    def __init__(self, meta: dict, content: str) -> None:
        self._meta = meta
        self.content = content

    def get(self, key: str, default: Any = None) -> Any:
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
