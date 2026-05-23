"""
tests/test_setup_wizard.py — Unit tests for scripts/setup_wizard.py.

All tests run fully offline: urllib calls are mocked, file I/O uses
pytest's tmp_path, and interactive prompts are monkeypatched.
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Import the wizard as a module ─────────────────────────────────────────────
# The wizard lives outside the normal package tree, so we import it by path.
WIZARD_PATH = Path(__file__).resolve().parent.parent / "scripts" / "setup_wizard.py"


def _load_wizard():
    spec = importlib.util.spec_from_file_location("setup_wizard", WIZARD_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def wiz():
    return _load_wizard()


# ══════════════════════════════════════════════════════════════════════════════
# load_env
# ══════════════════════════════════════════════════════════════════════════════

class TestLoadEnv:
    def test_missing_file_returns_empty(self, wiz, tmp_path):
        assert wiz.load_env(tmp_path / "nonexistent.env") == {}

    def test_parses_key_value(self, wiz, tmp_path):
        p = tmp_path / ".env"
        p.write_text("FOO=bar\nBAZ=qux\n")
        assert wiz.load_env(p) == {"FOO": "bar", "BAZ": "qux"}

    def test_ignores_blank_lines(self, wiz, tmp_path):
        p = tmp_path / ".env"
        p.write_text("\nFOO=bar\n\n")
        assert wiz.load_env(p) == {"FOO": "bar"}

    def test_ignores_comments(self, wiz, tmp_path):
        p = tmp_path / ".env"
        p.write_text("# comment\nFOO=bar\n# another\n")
        assert wiz.load_env(p) == {"FOO": "bar"}

    def test_value_with_equals_sign(self, wiz, tmp_path):
        p = tmp_path / ".env"
        p.write_text("URL=http://host:3000/path?a=1\n")
        assert wiz.load_env(p)["URL"] == "http://host:3000/path?a=1"

    def test_empty_value(self, wiz, tmp_path):
        p = tmp_path / ".env"
        p.write_text("EMPTY=\n")
        assert wiz.load_env(p) == {"EMPTY": ""}


# ══════════════════════════════════════════════════════════════════════════════
# save_env
# ══════════════════════════════════════════════════════════════════════════════

class TestSaveEnv:
    def test_creates_file_if_missing(self, wiz, tmp_path):
        p = tmp_path / "sub" / ".env"
        wiz.save_env(p, {"KEY": "val"})
        assert p.exists()
        assert "KEY=val" in p.read_text()

    def test_upserts_existing_key(self, wiz, tmp_path):
        p = tmp_path / ".env"
        p.write_text("FOO=old\nBAR=keep\n")
        wiz.save_env(p, {"FOO": "new"})
        text = p.read_text()
        assert "FOO=new" in text
        assert "FOO=old" not in text
        assert "BAR=keep" in text

    def test_appends_new_key(self, wiz, tmp_path):
        p = tmp_path / ".env"
        p.write_text("FOO=bar\n")
        wiz.save_env(p, {"NEWKEY": "newval"})
        text = p.read_text()
        assert "FOO=bar" in text
        assert "NEWKEY=newval" in text

    def test_preserves_comments(self, wiz, tmp_path):
        p = tmp_path / ".env"
        p.write_text("# my comment\nFOO=bar\n")
        wiz.save_env(p, {"FOO": "baz"})
        text = p.read_text()
        assert "# my comment" in text
        assert "FOO=baz" in text

    def test_multiple_keys_in_one_call(self, wiz, tmp_path):
        p = tmp_path / ".env"
        wiz.save_env(p, {"A": "1", "B": "2", "C": "3"})
        env = wiz.load_env(p)
        assert env == {"A": "1", "B": "2", "C": "3"}

    def test_roundtrip(self, wiz, tmp_path):
        p = tmp_path / ".env"
        original = {"OPENWEBUI_BASE_URL": "http://localhost:3000", "OPENWEBUI_API_KEY": "sk-abc", "PORT": "8765"}
        wiz.save_env(p, original)
        assert wiz.load_env(p) == original


# ══════════════════════════════════════════════════════════════════════════════
# validate_connection
# ══════════════════════════════════════════════════════════════════════════════

def _make_urlopen_ok(json_data: dict):
    """Return a mock urlopen context manager that yields json_data."""
    resp = MagicMock()
    resp.read.return_value = json.dumps(json_data).encode()
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestValidateConnection:
    def test_success_returns_true(self, wiz):
        resp = _make_urlopen_ok({"data": []})
        with patch("urllib.request.urlopen", return_value=resp):
            ok, err = wiz.validate_connection("http://localhost:3000", "sk-key")
        assert ok is True
        assert err == ""

    def test_401_returns_false_auth_message(self, wiz):
        http_err = urllib.error.HTTPError(
            url="http://x", code=401, msg="Unauthorized", hdrs=None, fp=None
        )
        with patch("urllib.request.urlopen", side_effect=http_err):
            ok, err = wiz.validate_connection("http://localhost:3000", "bad-key")
        assert ok is False
        assert "API key" in err or "Authentication" in err

    def test_connection_error_returns_false(self, wiz):
        with patch("urllib.request.urlopen", side_effect=OSError("refused")):
            ok, err = wiz.validate_connection("http://localhost:9999", "")
        assert ok is False
        assert err != ""

    def test_tries_multiple_paths(self, wiz):
        """Should try fallback paths before giving up."""
        call_count = 0
        http_err_500 = urllib.error.HTTPError(
            url="http://x", code=500, msg="err", hdrs=None, fp=None
        )

        def side_effect(req, timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count < 4:
                raise http_err_500
            return _make_urlopen_ok({"data": []})

        with patch("urllib.request.urlopen", side_effect=side_effect):
            ok, _ = wiz.validate_connection("http://localhost:3000", "k")
        assert ok is True
        assert call_count == 4  # tried all 4 paths

    def test_empty_api_key_still_tries(self, wiz):
        resp = _make_urlopen_ok({"data": []})
        with patch("urllib.request.urlopen", return_value=resp) as m:
            ok, _ = wiz.validate_connection("http://localhost:3000", "")
        assert ok is True
        # Authorization header should NOT have been added for empty key
        req_obj = m.call_args[0][0]
        assert req_obj.get_header("Authorization") is None


# ══════════════════════════════════════════════════════════════════════════════
# fetch_models
# ══════════════════════════════════════════════════════════════════════════════

class TestFetchModels:
    def test_returns_sorted_model_ids(self, wiz):
        payload = {"data": [{"id": "llama3.2:3b"}, {"id": "phi4:latest"}, {"id": "codellama:13b"}]}
        resp = _make_urlopen_ok(payload)
        with patch("urllib.request.urlopen", return_value=resp):
            models = wiz.fetch_models("http://localhost:3000", "sk-key")
        assert models == sorted(["llama3.2:3b", "phi4:latest", "codellama:13b"])

    def test_returns_empty_on_connection_failure(self, wiz):
        with patch("urllib.request.urlopen", side_effect=OSError("refused")):
            models = wiz.fetch_models("http://localhost:9999", "k")
        assert models == []

    def test_handles_name_field_fallback(self, wiz):
        """Some OpenWebUI versions use 'name' instead of 'id'."""
        payload = {"data": [{"name": "mistral:7b"}]}
        resp = _make_urlopen_ok(payload)
        with patch("urllib.request.urlopen", return_value=resp):
            models = wiz.fetch_models("http://localhost:3000", "k")
        assert "mistral:7b" in models

    def test_deduplicates_empty_ids(self, wiz):
        payload = {"data": [{"id": ""}, {"id": "valid-model"}, {}]}
        resp = _make_urlopen_ok(payload)
        with patch("urllib.request.urlopen", return_value=resp):
            models = wiz.fetch_models("http://localhost:3000", "k")
        assert models == ["valid-model"]

    def test_list_response_format(self, wiz):
        """Some endpoints return a plain list instead of {"data": [...]}."""
        payload = [{"id": "model-a"}, {"id": "model-b"}]
        resp = _make_urlopen_ok(payload)
        with patch("urllib.request.urlopen", return_value=resp):
            models = wiz.fetch_models("http://localhost:3000", "k")
        assert set(models) == {"model-a", "model-b"}


# ══════════════════════════════════════════════════════════════════════════════
# _read_config_json
# ══════════════════════════════════════════════════════════════════════════════

class TestReadConfigJson:
    def test_missing_file_returns_empty(self, wiz, tmp_path, monkeypatch):
        monkeypatch.setattr(wiz, "ROOT", tmp_path)
        assert wiz._read_config_json() == {}

    def test_reads_base_url_and_key(self, wiz, tmp_path, monkeypatch):
        monkeypatch.setattr(wiz, "ROOT", tmp_path)
        cfg = {"base_url": "http://ow:3000", "api_key": "sk-abc", "default_model": "llama3.2:3b"}
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "config.json").write_text(json.dumps(cfg))
        result = wiz._read_config_json()
        assert result["base_url"] == "http://ow:3000"
        assert result["api_key"] == "sk-abc"
        assert result["default_model"] == "llama3.2:3b"

    def test_corrupt_json_returns_empty(self, wiz, tmp_path, monkeypatch):
        monkeypatch.setattr(wiz, "ROOT", tmp_path)
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "config.json").write_text("{bad json")
        assert wiz._read_config_json() == {}


# ══════════════════════════════════════════════════════════════════════════════
# Wizard flow — integration-style with mocked I/O
# ══════════════════════════════════════════════════════════════════════════════

class TestWizardFlow:
    """
    Tests for the end-to-end wizard flow.  All user input, urllib calls,
    and curses are mocked so tests run non-interactively.
    """

    def _mock_good_conn(self, model_list=None):
        """Return a urlopen mock that simulates a healthy OpenWebUI instance."""
        models = model_list or ["llama3.2:3b", "phi4:latest"]
        payload = {"data": [{"id": m} for m in models]}
        resp = _make_urlopen_ok(payload)
        return resp

    def test_valid_config_exits_zero_no_prompts(self, wiz, tmp_path, monkeypatch, capsys):
        """When deploy/.env is fully valid, wizard exits 0 and never calls input()."""
        env_file = tmp_path / "deploy" / ".env"
        env_file.parent.mkdir()
        env_file.write_text(
            "OPENWEBUI_BASE_URL=http://localhost:3000\n"
            "OPENWEBUI_API_KEY=sk-good\n"
            "OPENWEBUI_MODEL=llama3.2:3b\n"
            "PORT=8765\nCLK_PORT=8001\nAUTOGUI_PORT=8002\nOSSO_PORT=5001\n"
            "CLK_WORKSPACES_DIR=./data/clk-workspaces\n"
        )
        monkeypatch.setattr(wiz, "ENV_PATH", env_file)
        monkeypatch.setattr(wiz, "ROOT", tmp_path)

        resp = self._mock_good_conn(["llama3.2:3b"])
        with patch("urllib.request.urlopen", return_value=resp):
            with patch("builtins.input", side_effect=AssertionError("input() called unexpectedly")):
                rc = wiz.main()

        assert rc == 0

    def test_missing_env_prompts_for_url_and_key(self, wiz, tmp_path, monkeypatch):
        """When deploy/.env is absent, wizard prompts for URL and key."""
        env_file = tmp_path / "deploy" / ".env"
        env_file.parent.mkdir()
        monkeypatch.setattr(wiz, "ENV_PATH", env_file)
        monkeypatch.setattr(wiz, "ROOT", tmp_path)

        resp = self._mock_good_conn(["phi4:latest"])
        # Only the URL prompt goes through input(); API key uses getpass.
        # Ports section requires no input (shown silently when not --reconfigure).
        with patch("urllib.request.urlopen", return_value=resp):
            with patch("builtins.input", return_value="http://localhost:3000"):
                with patch("getpass.getpass", return_value="sk-mykey"):
                    with patch.object(wiz, "pick_from_list", return_value="phi4:latest"):
                        rc = wiz.main()

        assert rc == 0
        saved = wiz.load_env(env_file)
        assert saved["OPENWEBUI_BASE_URL"] == "http://localhost:3000"
        assert saved["OPENWEBUI_API_KEY"] == "sk-mykey"
        assert saved["OPENWEBUI_MODEL"] == "phi4:latest"

    def test_bad_model_in_env_triggers_reprompt(self, wiz, tmp_path, monkeypatch):
        """When stored model is not in the model list, wizard prompts for a new one."""
        env_file = tmp_path / "deploy" / ".env"
        env_file.parent.mkdir()
        env_file.write_text(
            "OPENWEBUI_BASE_URL=http://localhost:3000\n"
            "OPENWEBUI_API_KEY=sk-good\n"
            "OPENWEBUI_MODEL=deleted-model:latest\n"
        )
        monkeypatch.setattr(wiz, "ENV_PATH", env_file)
        monkeypatch.setattr(wiz, "ROOT", tmp_path)

        resp = self._mock_good_conn(["llama3.2:3b", "phi4:latest"])
        with patch("urllib.request.urlopen", return_value=resp):
            with patch("builtins.input", return_value="n"):  # skip port reconfigure
                with patch.object(wiz, "pick_from_list", return_value="llama3.2:3b"):
                    rc = wiz.main()

        assert rc == 0
        saved = wiz.load_env(env_file)
        assert saved["OPENWEBUI_MODEL"] == "llama3.2:3b"

    def test_connection_failure_prompts_for_new_url(self, wiz, tmp_path, monkeypatch):
        """When the stored URL is unreachable, wizard prompts for a new one."""
        env_file = tmp_path / "deploy" / ".env"
        env_file.parent.mkdir()
        env_file.write_text(
            "OPENWEBUI_BASE_URL=http://badhost:9999\n"
            "OPENWEBUI_API_KEY=sk-good\n"
        )
        monkeypatch.setattr(wiz, "ENV_PATH", env_file)
        monkeypatch.setattr(wiz, "ROOT", tmp_path)

        good_resp = self._mock_good_conn(["llama3.2:3b"])
        call_count = 0

        def urlopen_side_effect(req, timeout=None):
            nonlocal call_count
            call_count += 1
            # First batch of calls (validating existing URL) all fail
            if "badhost" in req.get_full_url():
                raise OSError("refused")
            return good_resp

        # User types new URL; then skips ports
        inputs = iter(["http://localhost:3000", "n"])
        with patch("urllib.request.urlopen", side_effect=urlopen_side_effect):
            with patch("builtins.input", side_effect=inputs):
                with patch("getpass.getpass", return_value="sk-good"):
                    with patch.object(wiz, "pick_from_list", return_value="llama3.2:3b"):
                        rc = wiz.main()

        assert rc == 0
        saved = wiz.load_env(env_file)
        assert saved["OPENWEBUI_BASE_URL"] == "http://localhost:3000"

    def test_keyboard_interrupt_returns_1(self, wiz, tmp_path, monkeypatch):
        """Ctrl-C during prompts exits with code 1 and writes nothing."""
        env_file = tmp_path / "deploy" / ".env"
        env_file.parent.mkdir()
        monkeypatch.setattr(wiz, "ENV_PATH", env_file)
        monkeypatch.setattr(wiz, "ROOT", tmp_path)

        with patch("urllib.request.urlopen", side_effect=OSError("refused")):
            with patch("builtins.input", side_effect=KeyboardInterrupt):
                rc = wiz.main()

        assert rc == 1
        assert not env_file.exists()

    def test_force_flag_prompts_even_when_valid(self, wiz, tmp_path, monkeypatch):
        """--reconfigure forces prompts even when the existing config is valid."""
        env_file = tmp_path / "deploy" / ".env"
        env_file.parent.mkdir()
        env_file.write_text(
            "OPENWEBUI_BASE_URL=http://localhost:3000\n"
            "OPENWEBUI_API_KEY=sk-good\n"
            "OPENWEBUI_MODEL=llama3.2:3b\n"
        )
        monkeypatch.setattr(wiz, "ENV_PATH", env_file)
        monkeypatch.setattr(wiz, "ROOT", tmp_path)

        resp = self._mock_good_conn(["llama3.2:3b"])
        # With --reconfigure, wizard prompts URL (input), key (getpass), model,
        # and port/path settings (5 more inputs with defaults accepted via Enter).
        url_and_ports = ["http://localhost:3000", "", "", "", "", ""]
        with patch("urllib.request.urlopen", return_value=resp):
            with patch("builtins.input", side_effect=url_and_ports):
                with patch("getpass.getpass", return_value="sk-good"):
                    with patch.object(wiz, "pick_from_list", return_value="llama3.2:3b"):
                        with patch("sys.argv", ["setup_wizard.py", "--reconfigure"]):
                            rc = wiz.main()

        assert rc == 0

    def test_saves_port_settings_when_changed(self, wiz, tmp_path, monkeypatch):
        """With --reconfigure, custom port values are saved to .env."""
        env_file = tmp_path / "deploy" / ".env"
        env_file.parent.mkdir()
        env_file.write_text(
            "OPENWEBUI_BASE_URL=http://localhost:3000\n"
            "OPENWEBUI_API_KEY=sk-good\n"
            "OPENWEBUI_MODEL=llama3.2:3b\n"
        )
        monkeypatch.setattr(wiz, "ENV_PATH", env_file)
        monkeypatch.setattr(wiz, "ROOT", tmp_path)

        resp = self._mock_good_conn(["llama3.2:3b"])
        # With --reconfigure: URL prompt + 5 port/path prompts
        inputs = [
            "http://localhost:3000",  # OpenWebUI URL
            "9000",                   # BetterWebUI port
            "9001",                   # CLK port
            "9002",                   # AutoGUI port
            "9003",                   # OSSO port
            "/tmp/clk-ws",            # CLK workspaces dir
        ]
        with patch("urllib.request.urlopen", return_value=resp):
            with patch("builtins.input", side_effect=inputs):
                with patch("getpass.getpass", return_value="sk-good"):
                    with patch.object(wiz, "pick_from_list", return_value="llama3.2:3b"):
                        with patch("sys.argv", ["setup_wizard.py", "--reconfigure"]):
                            rc = wiz.main()

        assert rc == 0
        saved = wiz.load_env(env_file)
        assert saved["PORT"] == "9000"
        assert saved["CLK_PORT"] == "9001"
        assert saved["AUTOGUI_PORT"] == "9002"
        assert saved["OSSO_PORT"] == "9003"
        assert saved["CLK_WORKSPACES_DIR"] == "/tmp/clk-ws"

    def test_config_json_used_as_default(self, wiz, tmp_path, monkeypatch):
        """When .env is absent, wizard pre-fills defaults from data/config.json."""
        env_file = tmp_path / "deploy" / ".env"
        env_file.parent.mkdir()
        monkeypatch.setattr(wiz, "ENV_PATH", env_file)
        monkeypatch.setattr(wiz, "ROOT", tmp_path)

        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "config.json").write_text(json.dumps({
            "base_url": "http://from-config:3000",
            "api_key": "sk-from-config",
            "default_model": "from-config-model",
        }))

        resp = self._mock_good_conn(["from-config-model"])

        # input() called for URL prompt — we press Enter to accept the pre-filled default
        # Then "n" to skip port reconfigure
        inputs = iter(["", "n"])
        with patch("urllib.request.urlopen", return_value=resp):
            with patch("builtins.input", side_effect=inputs):
                with patch("getpass.getpass", return_value=""):  # accept default key
                    with patch.object(wiz, "pick_from_list", return_value="from-config-model"):
                        rc = wiz.main()

        assert rc == 0
        saved = wiz.load_env(env_file)
        assert saved["OPENWEBUI_BASE_URL"] == "http://from-config:3000"


# ══════════════════════════════════════════════════════════════════════════════
# _numbered_menu
# ══════════════════════════════════════════════════════════════════════════════

class TestNumberedMenu:
    def test_select_by_number(self, wiz):
        options = ["alpha", "beta", "gamma"]
        with patch("builtins.input", side_effect=["2"]):
            result = wiz._numbered_menu(options, "Pick one")
        assert result == "beta"

    def test_select_by_exact_name(self, wiz):
        options = ["alpha", "beta", "gamma"]
        with patch("builtins.input", side_effect=["gamma"]):
            result = wiz._numbered_menu(options, "Pick one")
        assert result == "gamma"

    def test_skip_returns_empty(self, wiz):
        options = ["alpha", "beta"]
        with patch("builtins.input", side_effect=["s"]):
            result = wiz._numbered_menu(options, "Pick one")
        assert result == ""

    def test_invalid_then_valid(self, wiz):
        options = ["alpha", "beta"]
        with patch("builtins.input", side_effect=["999", "alpha"]):
            result = wiz._numbered_menu(options, "Pick one")
        assert result == "alpha"

    def test_pagination(self, wiz):
        options = [f"model-{i}" for i in range(25)]
        # "n" advances to page 2 (items 21-25 shown with global numbers 21-25).
        # Typing the global number 25 selects model-24 (index 24).
        with patch("builtins.input", side_effect=["n", "25"]):
            result = wiz._numbered_menu(options, "Pick one")
        assert result == "model-24"


# ══════════════════════════════════════════════════════════════════════════════
# pick_from_list dispatch
# ══════════════════════════════════════════════════════════════════════════════

class TestPickFromList:
    def test_returns_empty_for_empty_list(self, wiz):
        assert wiz.pick_from_list([], "title") == ""

    def test_dispatches_to_numbered_on_win32(self, wiz):
        options = ["a", "b"]
        with patch.object(wiz, "_IS_WIN", True):
            with patch.object(wiz, "_numbered_menu", return_value="a") as m:
                result = wiz.pick_from_list(options, "title")
        m.assert_called_once_with(options, "title", "")
        assert result == "a"

    def test_dispatches_to_numbered_when_not_tty(self, wiz):
        options = ["x", "y"]
        with patch.object(wiz, "_IS_TTY", False):
            with patch.object(wiz, "_numbered_menu", return_value="x") as m:
                result = wiz.pick_from_list(options, "title", current="x")
        m.assert_called_once()
        assert result == "x"

    def test_falls_back_to_numbered_if_curses_raises(self, wiz):
        options = ["a", "b"]
        with patch.object(wiz, "_IS_TTY", True):
            with patch.object(wiz, "_IS_WIN", False):
                with patch.object(wiz, "_curses_menu", side_effect=Exception("no term")):
                    with patch.object(wiz, "_numbered_menu", return_value="b") as m:
                        result = wiz.pick_from_list(options, "title")
        m.assert_called_once()
        assert result == "b"


# ══════════════════════════════════════════════════════════════════════════════
# Subsystem fan-out (SUBSYSTEM_ENV_MAP, fanout_env)
# ══════════════════════════════════════════════════════════════════════════════

class TestSubsystemEnvMap:
    def test_map_covers_all_four_subsystems(self, wiz):
        assert set(wiz.SUBSYSTEM_ENV_MAP.keys()) == {"betterwebui", "clk", "autogui", "osso"}

    def test_fanout_includes_all_three_values(self, wiz):
        out = wiz.fanout_env("http://ow.example", "sk-abc", "llama3:70b")
        # canonical names appear (via the betterwebui entry)
        assert out["OPENWEBUI_BASE_URL"] == "http://ow.example"
        assert out["OPENWEBUI_API_KEY"]  == "sk-abc"
        assert out["OPENWEBUI_MODEL"]    == "llama3:70b"
        # CLK / OSSO use the CLK_OPENWEBUI_* names
        assert out["CLK_OPENWEBUI_ENDPOINT"] == "http://ow.example"
        assert out["CLK_OPENWEBUI_API_KEY"]  == "sk-abc"
        assert out["CLK_OPENWEBUI_MODEL"]    == "llama3:70b"
        # Default provider is openwebui (backward-compat)
        assert out["CLK_PROVIDER"]  == "openwebui"
        assert out["LLM_PROVIDER"]  == "openwebui"

    def test_fanout_propagates_provider(self, wiz):
        out = wiz.fanout_env("http://x", "k", "m", provider="ollama")
        assert out["LLM_PROVIDER"] == "ollama"
        assert out["CLK_PROVIDER"] == "ollama"

    def test_fanout_handles_empty_model(self, wiz):
        out = wiz.fanout_env("http://x", "k", "")
        assert out["OPENWEBUI_MODEL"] == ""
        assert out["CLK_OPENWEBUI_MODEL"] == ""


# ══════════════════════════════════════════════════════════════════════════════
# Provider presets + picker
# ══════════════════════════════════════════════════════════════════════════════

class TestProviderPresets:
    def test_presets_include_expected_providers(self, wiz):
        assert set(wiz.PROVIDER_PRESETS.keys()) >= {
            "openwebui", "ollama", "openai", "anthropic", "custom",
        }

    def test_ollama_does_not_require_key(self, wiz):
        assert wiz.PROVIDER_PRESETS["ollama"]["key_required"] is False

    def test_anthropic_skips_validation(self, wiz):
        # Anthropic uses x-api-key, so our Bearer-based probe can't validate it.
        assert wiz.PROVIDER_PRESETS["anthropic"]["validate"] is False

    def test_pick_provider_returns_chosen_key(self, wiz):
        with patch.object(wiz, "pick_from_list", side_effect=lambda opts, *a, **k: opts[1]):
            chosen = wiz.pick_provider(current="openwebui")
        # Index 1 in the preset order is "ollama" (per dict insertion order)
        keys = list(wiz.PROVIDER_PRESETS.keys())
        assert chosen == keys[1]

    def test_pick_provider_falls_back_to_current_on_skip(self, wiz):
        with patch.object(wiz, "pick_from_list", return_value=""):
            chosen = wiz.pick_provider(current="ollama")
        assert chosen == "ollama"


# ══════════════════════════════════════════════════════════════════════════════
# --print-env (subprocess test — exercises the actual CLI surface)
# ══════════════════════════════════════════════════════════════════════════════

WIZARD = Path(__file__).resolve().parent.parent / "scripts" / "setup_wizard.py"


class TestPrintEnv:
    def test_emits_parseable_kv_lines(self, wiz, tmp_path):
        env = tmp_path / ".env"
        env.write_text(
            "OPENWEBUI_BASE_URL=http://ow:3000\n"
            "OPENWEBUI_API_KEY=sk-test\n"
            "OPENWEBUI_MODEL=llama3:8b\n"
        )
        result = subprocess.run(
            [sys.executable, str(WIZARD), "--print-env", "--env-file", str(env)],
            capture_output=True, text=True, check=True,
        )
        # Round-trip via load_env to confirm output is well-formed.
        out_file = tmp_path / "out.env"
        out_file.write_text(result.stdout)
        loaded = wiz.load_env(out_file)
        assert loaded["OPENWEBUI_BASE_URL"]      == "http://ow:3000"
        assert loaded["CLK_OPENWEBUI_ENDPOINT"]  == "http://ow:3000"
        assert loaded["CLK_OPENWEBUI_API_KEY"]   == "sk-test"
        assert loaded["CLK_OPENWEBUI_MODEL"]     == "llama3:8b"
        assert loaded["CLK_PROVIDER"]            == "openwebui"

    def test_exits_2_when_url_missing(self, tmp_path):
        env = tmp_path / ".env"  # absent
        result = subprocess.run(
            [sys.executable, str(WIZARD), "--print-env", "--env-file", str(env)],
            capture_output=True, text=True,
        )
        assert result.returncode == 2
        assert "OPENWEBUI_BASE_URL" in result.stderr

    def test_falls_back_to_process_env(self, tmp_path):
        env = tmp_path / ".env"  # absent
        result = subprocess.run(
            [sys.executable, str(WIZARD), "--print-env", "--env-file", str(env)],
            capture_output=True, text=True,
            env={
                "PATH": "/usr/bin:/bin",
                "OPENWEBUI_BASE_URL": "http://from-env",
                "OPENWEBUI_API_KEY":  "k",
                "OPENWEBUI_MODEL":    "m",
            },
        )
        assert result.returncode == 0, result.stderr
        assert "OPENWEBUI_BASE_URL=http://from-env" in result.stdout


# ══════════════════════════════════════════════════════════════════════════════
# --non-interactive
# ══════════════════════════════════════════════════════════════════════════════

class TestNonInteractive:
    def test_missing_url_fails_fast_with_no_prompts(self, tmp_path):
        env = tmp_path / ".env"  # absent — should trigger missing-required path
        result = subprocess.run(
            [sys.executable, str(WIZARD), "--non-interactive", "--env-file", str(env)],
            capture_output=True, text=True, timeout=5,
        )
        assert result.returncode == 2
        assert "missing required" in result.stderr.lower()

    def test_all_present_exits_0(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text(
            "OPENWEBUI_BASE_URL=http://x\n"
            "OPENWEBUI_API_KEY=k\n"
            "OPENWEBUI_MODEL=m\n"
        )
        result = subprocess.run(
            [sys.executable, str(WIZARD), "--non-interactive", "--env-file", str(env)],
            capture_output=True, text=True, timeout=5,
        )
        assert result.returncode == 0, result.stderr

    def test_ollama_provider_does_not_require_api_key(self, tmp_path):
        """Ollama mode passes --non-interactive with no API key set."""
        env = tmp_path / ".env"
        env.write_text(
            "LLM_PROVIDER=ollama\n"
            "OPENWEBUI_BASE_URL=http://localhost:11434\n"
            "OPENWEBUI_MODEL=llama3:8b\n"
        )
        result = subprocess.run(
            [sys.executable, str(WIZARD), "--non-interactive", "--env-file", str(env)],
            capture_output=True, text=True, timeout=5,
        )
        assert result.returncode == 0, result.stderr


# ══════════════════════════════════════════════════════════════════════════════
# --env-file
# ══════════════════════════════════════════════════════════════════════════════

class TestEnvFileOverride:
    def test_print_env_honors_override(self, wiz, tmp_path):
        custom = tmp_path / "custom.env"
        custom.write_text(
            "OPENWEBUI_BASE_URL=http://custom\n"
            "OPENWEBUI_API_KEY=k\n"
            "OPENWEBUI_MODEL=m\n"
        )
        result = subprocess.run(
            [sys.executable, str(WIZARD), "--print-env", "--env-file", str(custom)],
            capture_output=True, text=True, check=True,
        )
        assert "OPENWEBUI_BASE_URL=http://custom" in result.stdout

    def test_equals_form_accepted(self, tmp_path):
        custom = tmp_path / "c.env"
        custom.write_text("OPENWEBUI_BASE_URL=http://eq\nOPENWEBUI_API_KEY=k\nOPENWEBUI_MODEL=m\n")
        result = subprocess.run(
            [sys.executable, str(WIZARD), "--print-env", f"--env-file={custom}"],
            capture_output=True, text=True, check=True,
        )
        assert "OPENWEBUI_BASE_URL=http://eq" in result.stdout
