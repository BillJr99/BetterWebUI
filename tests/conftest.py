"""
Shared pytest fixtures for BetterWebUI tests.

Each test gets its own isolated temporary data directory so tests never
touch the real config.json / conversations.json on disk and don't
interfere with each other.

OpenWebUI network calls (discover_profile, fetch_models, chat_complete)
are stubbed out so tests run fully offline.
"""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# Make sure the project root is on sys.path so `import app` works
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Isolated data-directory fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def isolated_dirs(tmp_path, monkeypatch):
    """
    Redirect all app-level path constants to a throw-away tmp directory.
    Also resets in-memory session state that persists between requests.
    """
    data_dir = tmp_path / "data"
    skills_dir = tmp_path / "skills"
    uploads_dir = data_dir / "uploads"
    checkpoints_dir = data_dir / "checkpoints"
    tasks_dir = data_dir / "tasks"

    for d in (data_dir, skills_dir, uploads_dir, checkpoints_dir, tasks_dir):
        d.mkdir(parents=True, exist_ok=True)

    import app as app_module

    monkeypatch.setattr(app_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(app_module, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(app_module, "UPLOADS_DIR", uploads_dir)
    monkeypatch.setattr(app_module, "CHECKPOINTS_DIR", checkpoints_dir)
    monkeypatch.setattr(app_module, "TASKS_DIR", tasks_dir)
    monkeypatch.setattr(app_module, "CONFIG_PATH", data_dir / "config.json")
    monkeypatch.setattr(app_module, "PROMPTS_PATH", data_dir / "system_prompts.json")
    monkeypatch.setattr(app_module, "CONVERSATIONS_PATH", data_dir / "conversations.json")
    monkeypatch.setattr(app_module, "WORKSPACES_PATH", data_dir / "workspaces.json")
    monkeypatch.setattr(app_module, "MCP_PATH", data_dir / "mcp_servers.json")
    monkeypatch.setattr(app_module, "CLI_PATH", data_dir / "cli_tools.json")
    monkeypatch.setattr(app_module, "BRANDING_PATH", data_dir / "branding.json")

    # Clear in-memory session state
    app_module._session_trusted_commands.clear()
    app_module._command_explanation_cache.clear()
    app_module._background_tasks.clear()
    app_module.approvals.__init__()

    return {"data": data_dir, "skills": skills_dir, "tmp": tmp_path}


# ---------------------------------------------------------------------------
# Test client fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(isolated_dirs):
    """Return a synchronous TestClient with patched data paths."""
    from app import app
    # Prevent MCP reconciliation from firing on startup in tests
    with patch("app.mcp_manager.reconcile", new_callable=AsyncMock):
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c


# ---------------------------------------------------------------------------
# Helpers used across multiple test modules
# ---------------------------------------------------------------------------

def make_skill(client, skill_id="test-skill", name="Test Skill",
               description="A test skill", content="Do the test thing."):
    r = client.post("/api/skills", json={
        "id": skill_id,
        "name": name,
        "description": description,
        "content": content,
    })
    assert r.status_code == 200
    return r.json()


def make_prompt(client, name="Test Prompt", content="You are a test assistant."):
    r = client.post("/api/system-prompts", json={"name": name, "content": content})
    assert r.status_code == 200
    return r.json()


def make_workspace(client, name="Test WS", description="A test workspace"):
    r = client.post("/api/workspaces", json={"name": name, "description": description})
    assert r.status_code == 200
    return r.json()


def seed_conversation(isolated_dirs, cid="conv-1", title="Hello", messages=None):
    """Write a conversation directly to disk, bypassing the chat endpoint."""
    import time
    conversations_path = isolated_dirs["data"] / "conversations.json"
    data = json.loads(conversations_path.read_text()) if conversations_path.exists() else {"conversations": {}}
    data["conversations"][cid] = {
        "id": cid,
        "title": title,
        "messages": messages or [{"role": "user", "content": "hi"}],
        "updated_at": int(time.time()),
        "created_at": int(time.time()),
        "pinned": False,
        "tags": [],
        "task_plan": [],
    }
    conversations_path.write_text(json.dumps(data))
    return cid
