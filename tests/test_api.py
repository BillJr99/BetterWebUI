"""
Comprehensive backend API tests for BetterWebUI.

Covers:
  - Config GET / PATCH
  - Health endpoint
  - Skills CRUD + upload
  - System-prompts CRUD
  - Workspaces CRUD + export / import
  - CLI tools CRUD + registry
  - MCP registry
  - Conversations list / get / delete / pin / tag / fork / search
  - Session trust
  - Lint
  - Branding
  - Approve (pending-approval mechanic)
  - File upload
  - Onboarding templates + complete
  - Recommend-model
  - Background tasks
  - Static file serving
"""

import io
import json
import zipfile

from tests.conftest import make_skill, make_prompt, make_workspace, seed_conversation


# ===========================================================================
# Config
# ===========================================================================

class TestConfig:
    def test_get_default_config(self, client):
        r = client.get("/api/config")
        assert r.status_code == 200
        data = r.json()
        assert "api_key_set" in data
        assert data["api_key_set"] is False
        # api_key must never be exposed
        assert data.get("api_key", "") == ""

    def test_patch_base_url(self, client):
        r = client.post("/api/config", json={"base_url": "http://example.com"})
        assert r.status_code == 200
        assert r.json()["base_url"] == "http://example.com"

    def test_patch_strips_trailing_slash(self, client):
        r = client.post("/api/config", json={"base_url": "http://example.com/"})
        assert r.status_code == 200
        assert not r.json()["base_url"].endswith("/")

    def test_patch_api_key_sets_flag(self, client):
        r = client.post("/api/config", json={"api_key": "sk-test"})
        assert r.status_code == 200
        assert r.json()["api_key_set"] is True
        assert r.json().get("api_key", "") == ""   # never leaked

    def test_patch_shell_enabled(self, client):
        r = client.post("/api/config", json={"shell_enabled": False})
        assert r.status_code == 200
        data = client.get("/api/config").json()
        assert data["shell_enabled"] is False

    def test_patch_display_settings(self, client):
        display = {"font_size": "lg", "high_contrast": True, "dyslexic_font": False}
        r = client.post("/api/config", json={"display": display})
        assert r.status_code == 200
        cfg = client.get("/api/config").json()
        assert cfg["display"]["font_size"] == "lg"
        assert cfg["display"]["high_contrast"] is True

    def test_patch_chat_mode(self, client):
        r = client.post("/api/config", json={"chat_mode": "plan"})
        assert r.status_code == 200
        assert client.get("/api/config").json()["chat_mode"] == "plan"

    def test_patch_onboarding_done(self, client):
        r = client.post("/api/config", json={"onboarding_done": True})
        assert r.status_code == 200
        assert client.get("/api/config").json()["onboarding_done"] is True

    def test_patch_consensus_runs(self, client):
        r = client.post("/api/config", json={"consensus_runs": 3})
        assert r.status_code == 200
        assert client.get("/api/config").json()["consensus_runs"] == 3


# ===========================================================================
# Health
# ===========================================================================

class TestHealth:
    def test_health_ok(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        data = r.json()
        assert "platform" in data
        assert "shell" in data
        assert "skills" in data
        assert "workspaces" in data
        assert "mcp_servers" in data
        assert "cli_tools" in data

    def test_health_counts_reflect_state(self, client):
        make_skill(client)
        r = client.get("/api/health")
        assert r.json()["skills"] == 1


# ===========================================================================
# Skills
# ===========================================================================

class TestSkills:
    def test_list_empty(self, client):
        r = client.get("/api/skills")
        assert r.status_code == 200
        assert r.json()["skills"] == []

    def test_create_and_list(self, client):
        make_skill(client, "my-skill", "My Skill", "Do X when Y", "Do X.")
        r = client.get("/api/skills")
        skills = r.json()["skills"]
        assert len(skills) == 1
        assert skills[0]["id"] == "my-skill"
        assert skills[0]["name"] == "My Skill"

    def test_get_skill(self, client):
        make_skill(client, "fetch-skill")
        r = client.get("/api/skills/fetch-skill")
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == "fetch-skill"
        assert "content" in data

    def test_get_missing_skill(self, client):
        r = client.get("/api/skills/nonexistent")
        assert r.status_code == 404

    def test_delete_skill(self, client):
        make_skill(client, "del-skill")
        r = client.delete("/api/skills/del-skill")
        assert r.status_code == 200
        assert client.get("/api/skills/del-skill").status_code == 404

    def test_id_sanitisation(self, client):
        r = client.post("/api/skills", json={
            "id": "bad id!!",
            "name": "Bad ID",
            "description": "test",
            "content": "x",
        })
        assert r.status_code == 200
        sid = r.json()["id"]
        # Must contain only alnum / - / _
        assert all(c.isalnum() or c in "-_" for c in sid)

    def test_upload_skill_markdown(self, client, isolated_dirs):
        md = b"---\nname: Uploaded\ndescription: Uploaded skill\n---\n\nDo stuff.\n"
        r = client.post(
            "/api/skills/upload",
            files={"file": ("uploaded.md", io.BytesIO(md), "text/markdown")},
        )
        assert r.status_code == 200
        skills = client.get("/api/skills").json()["skills"]
        assert any(s["id"] == "uploaded" for s in skills)


# ===========================================================================
# System prompts
# ===========================================================================

class TestPrompts:
    def test_list_empty(self, client):
        r = client.get("/api/system-prompts")
        assert r.status_code == 200
        # A "Helpful Assistant" default prompt is always seeded
        prompts = r.json()["prompts"]
        assert isinstance(prompts, list)
        assert any(p["id"] == "default" for p in prompts)

    def test_create_and_list(self, client):
        before = len(client.get("/api/system-prompts").json()["prompts"])
        make_prompt(client, "Grader", "You grade things.")
        r = client.get("/api/system-prompts")
        prompts = r.json()["prompts"]
        assert len(prompts) == before + 1
        assert any(p["name"] == "Grader" for p in prompts)

    def test_upsert_updates_existing(self, client):
        pid = make_prompt(client, "Old Name")["id"]
        client.post("/api/system-prompts", json={"id": pid, "name": "New Name", "content": "updated"})
        prompts = client.get("/api/system-prompts").json()["prompts"]
        assert any(p["name"] == "New Name" for p in prompts)

    def test_delete_prompt(self, client):
        pid = make_prompt(client)["id"]
        r = client.delete(f"/api/system-prompts/{pid}")
        assert r.status_code == 200
        prompts = client.get("/api/system-prompts").json()["prompts"]
        assert not any(p["id"] == pid for p in prompts)


# ===========================================================================
# Workspaces
# ===========================================================================

class TestWorkspaces:
    def test_list_empty(self, client):
        r = client.get("/api/workspaces")
        assert r.status_code == 200
        assert r.json()["workspaces"] == []

    def test_create_and_list(self, client):
        make_workspace(client, "Research", "For research tasks")
        ws_list = client.get("/api/workspaces").json()["workspaces"]
        assert len(ws_list) == 1
        assert ws_list[0]["name"] == "Research"

    def test_get_workspace(self, client):
        wid = make_workspace(client)["id"]
        r = client.get(f"/api/workspaces/{wid}")
        assert r.status_code == 200
        assert r.json()["id"] == wid

    def test_get_missing_workspace(self, client):
        r = client.get("/api/workspaces/nonexistent")
        assert r.status_code == 404

    def test_update_workspace(self, client):
        wid = make_workspace(client, "Old")["id"]
        r = client.post("/api/workspaces", json={"id": wid, "name": "New", "description": "updated"})
        assert r.status_code == 200
        assert client.get(f"/api/workspaces/{wid}").json()["name"] == "New"

    def test_delete_workspace(self, client):
        wid = make_workspace(client)["id"]
        r = client.delete(f"/api/workspaces/{wid}")
        assert r.status_code == 200
        assert client.get(f"/api/workspaces/{wid}").status_code == 404

    def test_workspace_with_skills_and_prompts(self, client):
        pid = make_prompt(client)["id"]
        make_skill(client)
        r = client.post("/api/workspaces", json={
            "name": "Full WS",
            "description": "Has everything",
            "system_prompt_id": pid,
            "active_skills": ["test-skill"],
            "active_mcp_servers": [],
            "active_cli_tools": [],
        })
        assert r.status_code == 200
        wid = r.json()["id"]
        ws = client.get(f"/api/workspaces/{wid}").json()
        assert ws["system_prompt_id"] == pid
        assert "test-skill" in ws["active_skills"]

    def test_export_workspace(self, client):
        wid = make_workspace(client, "Export Me")["id"]
        r = client.get(f"/api/workspaces/{wid}/export")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        # Verify it's a valid zip with a manifest
        buf = io.BytesIO(r.content)
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            assert "manifest.json" in names
            manifest = json.loads(zf.read("manifest.json"))
            assert manifest["name"] == "Export Me"

    def test_export_missing_workspace(self, client):
        r = client.get("/api/workspaces/ghost/export")
        assert r.status_code == 404

    def test_import_workspace(self, client):
        # First create and export
        wid = make_workspace(client, "Import Me")["id"]
        export_resp = client.get(f"/api/workspaces/{wid}/export")
        zip_bytes = export_resp.content

        # Delete original
        client.delete(f"/api/workspaces/{wid}")
        assert client.get("/api/workspaces").json()["workspaces"] == []

        # Import
        r = client.post(
            "/api/workspaces/import",
            files={"file": ("import.bwui", io.BytesIO(zip_bytes), "application/zip")},
        )
        assert r.status_code == 200
        ws_list = client.get("/api/workspaces").json()["workspaces"]
        assert any(w["name"] == "Import Me" for w in ws_list)


# ===========================================================================
# CLI tools
# ===========================================================================

class TestCliTools:
    def test_list_empty(self, client):
        r = client.get("/api/cli/tools")
        assert r.status_code == 200
        assert r.json()["tools"] == []

    def test_registry_non_empty(self, client):
        r = client.get("/api/cli/registry")
        assert r.status_code == 200
        assert len(r.json()["registry"]) > 0

    def test_create_tool(self, client):
        r = client.post("/api/cli/tools", json={
            "id": "pandoc",
            "name": "Pandoc",
            "description": "Convert documents",
            "command_template": "pandoc {args}",
        })
        assert r.status_code == 200
        tools = client.get("/api/cli/tools").json()["tools"]
        assert any(t["id"] == "pandoc" for t in tools)

    def test_delete_tool(self, client):
        client.post("/api/cli/tools", json={
            "id": "del-tool",
            "name": "Del",
            "description": "x",
            "command_template": "del {args}",
        })
        r = client.delete("/api/cli/tools/del-tool")
        assert r.status_code == 200
        tools = client.get("/api/cli/tools").json()["tools"]
        assert not any(t["id"] == "del-tool" for t in tools)


# ===========================================================================
# MCP servers
# ===========================================================================

class TestMcp:
    def test_registry_has_entries(self, client):
        r = client.get("/api/mcp/registry")
        assert r.status_code == 200
        registry = r.json()["registry"]
        assert len(registry) > 0
        # Spot-check known entries
        names = [e["name"] for e in registry]
        assert any("Filesystem" in n or "filesystem" in n.lower() for n in names)

    def test_list_servers_empty(self, client):
        r = client.get("/api/mcp/servers")
        assert r.status_code == 200
        assert r.json()["servers"] == []

    def test_add_and_delete_server(self, client):
        r = client.post("/api/mcp/servers", json={
            "name": "test-mcp",
            "command": "npx",
            "args": ["-y", "@test/server"],
            "env": {},
            "description": "Test server",
            "enabled": False,
        })
        assert r.status_code == 200
        servers = client.get("/api/mcp/servers").json()["servers"]
        assert any(s["name"] == "test-mcp" for s in servers)

        client.delete("/api/mcp/servers/test-mcp")
        servers = client.get("/api/mcp/servers").json()["servers"]
        assert not any(s["name"] == "test-mcp" for s in servers)


# ===========================================================================
# Conversations
# ===========================================================================

class TestConversations:
    def test_list_empty(self, client):
        r = client.get("/api/conversations")
        assert r.status_code == 200
        assert r.json()["conversations"] == []

    def test_get_missing(self, client):
        r = client.get("/api/conversations/nope")
        assert r.status_code == 404

    def test_delete_missing(self, client):
        # Should be idempotent
        r = client.delete("/api/conversations/nope")
        assert r.status_code == 200

    def test_list_seeded(self, client, isolated_dirs):
        seed_conversation(isolated_dirs, "c1", "Alpha")
        seed_conversation(isolated_dirs, "c2", "Beta")
        convs = client.get("/api/conversations").json()["conversations"]
        assert len(convs) == 2
        titles = {c["title"] for c in convs}
        assert titles == {"Alpha", "Beta"}

    def test_get_seeded(self, client, isolated_dirs):
        seed_conversation(isolated_dirs, "cx", "My Chat", [{"role": "user", "content": "hello"}])
        r = client.get("/api/conversations/cx")
        assert r.status_code == 200
        assert r.json()["title"] == "My Chat"
        assert r.json()["messages"][0]["content"] == "hello"

    def test_delete_seeded(self, client, isolated_dirs):
        seed_conversation(isolated_dirs, "del", "Delete me")
        client.delete("/api/conversations/del")
        assert client.get("/api/conversations/del").status_code == 404

    def test_pin_conversation(self, client, isolated_dirs):
        seed_conversation(isolated_dirs, "p1", "Pin me")
        r = client.post("/api/conversations/p1/pin", json={"pinned": True})
        assert r.status_code == 200
        conv = client.get("/api/conversations/p1").json()
        assert conv["pinned"] is True

    def test_unpin_conversation(self, client, isolated_dirs):
        seed_conversation(isolated_dirs, "p2", "Unpin me")
        client.post("/api/conversations/p2/pin", json={"pinned": True})
        client.post("/api/conversations/p2/pin", json={"pinned": False})
        assert client.get("/api/conversations/p2").json()["pinned"] is False

    def test_tag_conversation(self, client, isolated_dirs):
        seed_conversation(isolated_dirs, "t1", "Tag me")
        r = client.post("/api/conversations/t1/tags", json={"tags": ["grading", "test"]})
        assert r.status_code == 200
        conv = client.get("/api/conversations/t1").json()
        assert "grading" in conv["tags"]

    def test_fork_conversation(self, client, isolated_dirs):
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
            {"role": "user", "content": "Follow-up"},
        ]
        seed_conversation(isolated_dirs, "src", "Source", messages)
        r = client.post("/api/conversations/src/fork", json={"fork_at": 1})
        assert r.status_code == 200
        fork_id = r.json()["id"]
        forked = client.get(f"/api/conversations/{fork_id}").json()
        # Should have messages[0:2] (indices 0 and 1 inclusive)
        assert len(forked["messages"]) == 2
        assert forked["messages"][-1]["content"] == "Hi!"

    def test_fork_missing_conversation(self, client):
        r = client.post("/api/conversations/ghost/fork", json={"fork_at": 0})
        assert r.status_code == 404

    def test_search_by_title(self, client, isolated_dirs):
        seed_conversation(isolated_dirs, "s1", "Grading essay 101")
        seed_conversation(isolated_dirs, "s2", "Research notes")
        r = client.get("/api/conversations/search?q=grading")
        assert r.status_code == 200
        results = r.json()["results"]
        assert len(results) == 1
        assert results[0]["title"] == "Grading essay 101"

    def test_search_by_content(self, client, isolated_dirs):
        seed_conversation(isolated_dirs, "s3", "Chat A", [{"role": "user", "content": "photosynthesis process"}])
        seed_conversation(isolated_dirs, "s4", "Chat B", [{"role": "user", "content": "nothing interesting"}])
        r = client.get("/api/conversations/search?q=photosynthesis")
        results = r.json()["results"]
        assert len(results) == 1
        assert results[0]["id"] == "s3"

    def test_search_empty_query_returns_all(self, client, isolated_dirs):
        seed_conversation(isolated_dirs, "a1", "A")
        seed_conversation(isolated_dirs, "a2", "B")
        r = client.get("/api/conversations/search?q=")
        assert len(r.json()["results"]) == 2

    def test_pinned_sorted_first(self, client, isolated_dirs):
        seed_conversation(isolated_dirs, "un", "Unpinned")
        seed_conversation(isolated_dirs, "pi", "Pinned")
        client.post("/api/conversations/pi/pin", json={"pinned": True})
        convs = client.get("/api/conversations").json()["conversations"]
        assert convs[0]["id"] == "pi"


# ===========================================================================
# Session trust
# ===========================================================================

class TestSessionTrust:
    def test_list_empty(self, client):
        r = client.get("/api/session/trust")
        assert r.status_code == 200
        assert r.json()["commands"] == []

    def test_add_command(self, client):
        r = client.post("/api/session/trust", json={"command": "ls -la"})
        assert r.status_code == 200
        assert "ls -la" in client.get("/api/session/trust").json()["commands"]

    def test_add_idempotent(self, client):
        client.post("/api/session/trust", json={"command": "ls"})
        client.post("/api/session/trust", json={"command": "ls"})
        cmds = client.get("/api/session/trust").json()["commands"]
        assert cmds.count("ls") == 1

    def test_clear_commands(self, client):
        client.post("/api/session/trust", json={"command": "rm -rf /"})
        client.delete("/api/session/trust")
        assert client.get("/api/session/trust").json()["commands"] == []


# ===========================================================================
# Lint
# ===========================================================================

class TestLint:
    def test_no_issues_when_clean(self, client):
        make_skill(client)   # has valid frontmatter
        r = client.get("/api/lint")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["skills"] == []
        assert data["mcp"] == []
        assert data["cli"] == []

    def test_detects_missing_frontmatter(self, client, isolated_dirs):
        # Write a skill with no frontmatter
        bad = isolated_dirs["skills"] / "broken.md"
        bad.write_text("This skill has no frontmatter.", encoding="utf-8")
        r = client.get("/api/lint")
        data = r.json()
        assert data["ok"] is False
        assert len(data["skills"]) > 0

    def test_detects_cli_missing_args_placeholder(self, client):
        client.post("/api/cli/tools", json={
            "id": "bad-cli",
            "name": "Bad CLI",
            "description": "missing placeholder",
            "command_template": "mytool --flag",   # no {args}
        })
        r = client.get("/api/lint")
        assert r.json()["ok"] is False
        assert len(r.json()["cli"]) > 0


# ===========================================================================
# Branding
# ===========================================================================

class TestBranding:
    def test_default_branding(self, client):
        r = client.get("/api/branding")
        assert r.status_code == 200
        data = r.json()
        assert "logo" in data
        assert "primary_color" in data

    def test_custom_branding(self, client, isolated_dirs):
        branding = {"logo": None, "primary_color": "#ff0000", "welcome": "Hello", "institution": "ACME"}
        (isolated_dirs["data"] / "branding.json").write_text(json.dumps(branding), encoding="utf-8")
        r = client.get("/api/branding")
        assert r.json()["primary_color"] == "#ff0000"
        assert r.json()["institution"] == "ACME"


# ===========================================================================
# Onboarding
# ===========================================================================

class TestOnboarding:
    def test_templates_endpoint(self, client):
        r = client.get("/api/onboarding/templates")
        assert r.status_code == 200
        templates = r.json()["templates"]
        assert len(templates) > 0
        ids = [t["id"] for t in templates]
        assert "grading" in ids or "research" in ids

    def test_complete_onboarding(self, client):
        # Use template_id (the actual field name), not use_case
        r = client.post("/api/onboarding/complete", json={"template_id": "grading"})
        assert r.status_code == 200
        data = r.json()
        assert data.get("ok") is True
        assert "workspace_id" in data
        # onboarding_done should be set
        cfg = client.get("/api/config").json()
        assert cfg["onboarding_done"] is True


# ===========================================================================
# Recommend model
# ===========================================================================

class TestRecommendModel:
    def test_returns_recommendation(self, client):
        r = client.get("/api/recommend-model?use_case=grading")
        assert r.status_code == 200
        assert "recommendation" in r.json() or "model" in r.json()

    def test_unknown_use_case_still_responds(self, client):
        r = client.get("/api/recommend-model?use_case=unknownxyz")
        assert r.status_code == 200


# ===========================================================================
# File upload
# ===========================================================================

class TestUpload:
    def test_upload_text_file(self, client):
        r = client.post(
            "/api/upload",
            files={"file": ("hello.txt", io.BytesIO(b"hello world"), "text/plain")},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["filename"] == "hello.txt"
        assert "url" in data
        assert data["content_type"] == "text/plain"

    def test_upload_binary_file(self, client):
        r = client.post(
            "/api/upload",
            files={"file": ("img.png", io.BytesIO(b"\x89PNG\r\n"), "image/png")},
        )
        assert r.status_code == 200
        data = r.json()
        assert "url" in data
        assert data["content_type"] == "image/png"


# ===========================================================================
# Static files
# ===========================================================================

class TestStatic:
    def test_index_html(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert b"BetterWebUI" in r.content

    def test_app_js_served(self, client):
        r = client.get("/static/app.js")
        assert r.status_code == 200
        assert "javascript" in r.headers["content-type"]

    def test_style_css_served(self, client):
        r = client.get("/static/style.css")
        assert r.status_code == 200
        assert "css" in r.headers["content-type"]


# ===========================================================================
# Approve mechanic
# ===========================================================================

class TestApprove:
    def test_approve_unknown_id_returns_404(self, client):
        r = client.post("/api/approve", json={"approval_id": "ghost", "approved": True})
        assert r.status_code == 404

    def test_approve_sets_session_trust(self, client):
        """Approving with trust_session=True should add the command to the trusted set."""
        import app as app_module
        # Manually register a pending approval in the events dict
        aid = "test-approval-id"
        loop_event = __import__("asyncio").Event()
        app_module.approvals.events[aid] = loop_event

        r = client.post("/api/approve", json={
            "approval_id": aid,
            "approved": True,
            "trust_session": True,
            "command": "echo hello",
        })
        assert r.status_code == 200
        assert "echo hello" in app_module._session_trusted_commands


# ===========================================================================
# Project file APIs
# ===========================================================================

class TestProjectApi:
    def _setup_workspace_dir(self, isolated_dirs):
        """Point WORKSPACE_DIR to a temp dir with a test file inside it."""
        import app as app_module
        ws = isolated_dirs["tmp"] / "workspace"
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "hello.txt").write_text("hello world", encoding="utf-8")
        (ws / "sub").mkdir(exist_ok=True)
        (ws / "sub" / "nested.txt").write_text("nested", encoding="utf-8")
        app_module.WORKSPACE_DIR = ws
        return ws

    def test_tree_lists_files(self, client, isolated_dirs):
        self._setup_workspace_dir(isolated_dirs)
        r = client.get("/api/project/tree")
        assert r.status_code == 200
        data = r.json()
        assert "entries" in data
        names = {e["name"] for e in data["entries"]}
        assert "hello.txt" in names

    def test_tree_expands_subdirectory(self, client, isolated_dirs):
        self._setup_workspace_dir(isolated_dirs)
        r = client.get("/api/project/tree?path=sub")
        assert r.status_code == 200
        data = r.json()
        names = {e["name"] for e in data["entries"]}
        assert "nested.txt" in names

    def test_file_read(self, client, isolated_dirs):
        self._setup_workspace_dir(isolated_dirs)
        # Default request returns metadata only; content requires opt-in.
        meta = client.get("/api/project/file?path=hello.txt")
        assert meta.status_code == 200
        meta_data = meta.json()
        assert meta_data["is_binary"] is False
        assert "content" not in meta_data
        r = client.get("/api/project/file?path=hello.txt&include_content=true")
        assert r.status_code == 200
        data = r.json()
        assert data["content"] == "hello world"
        assert data["is_binary"] is False

    def test_file_not_found(self, client, isolated_dirs):
        self._setup_workspace_dir(isolated_dirs)
        r = client.get("/api/project/file?path=missing.txt")
        assert r.status_code == 404

    def test_path_traversal_denied(self, client, isolated_dirs):
        self._setup_workspace_dir(isolated_dirs)
        r = client.get("/api/project/file?path=../config.json")
        assert r.status_code == 403

    def test_tree_path_traversal_denied(self, client, isolated_dirs):
        self._setup_workspace_dir(isolated_dirs)
        r = client.get("/api/project/tree?path=../")
        assert r.status_code == 403

    def test_tree_non_directory_target(self, client, isolated_dirs):
        self._setup_workspace_dir(isolated_dirs)
        r = client.get("/api/project/tree?path=hello.txt")
        assert r.status_code == 400

    def test_checkpoints_empty(self, client, isolated_dirs):
        self._setup_workspace_dir(isolated_dirs)
        r = client.get("/api/project/checkpoints?filename=hello.txt")
        assert r.status_code == 200
        assert r.json()["checkpoints"] == []

    def test_revert_missing_checkpoint(self, client, isolated_dirs):
        self._setup_workspace_dir(isolated_dirs)
        r = client.post("/api/project/revert", json={
            "filename": "hello.txt",
            "checkpoint_id": "nonexistent",
        })
        assert r.status_code == 404
