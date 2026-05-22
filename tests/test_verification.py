"""
Unit tests for verification.py — validators, judge prompt assembly,
and the retry orchestrator.

These tests are intentionally LLM-free: chat_complete is mocked, so
they run offline and deterministically.
"""

from __future__ import annotations

import base64
import io
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import verification as v


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _png_bytes(width: int = 64, height: int = 64) -> bytes:
    """Build a minimal valid PNG. Uses Pillow if available, else a hand-rolled
    fixed-size buffer that satisfies the magic-byte check."""
    try:
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (width, height), (200, 50, 50)).save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        # Pillow not installed: build a minimal sniff-valid stub that the
        # PIL-less path will accept (>= 256 bytes, valid PNG header).
        header = b"\x89PNG\r\n\x1a\n"
        return header + (b"\x00" * 512)


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


# ---------------------------------------------------------------------------
# validate_tool_result
# ---------------------------------------------------------------------------

def test_validate_image_rejects_garbage():
    rep = v.validate_tool_result("generate_image", {}, {"data_b64": "AAAA"})
    assert rep.ok is False
    assert "too small" in rep.reason.lower() or "not a recognised" in rep.reason.lower()


def test_validate_image_rejects_empty():
    rep = v.validate_tool_result("generate_image", {}, {"data_b64": ""})
    assert rep.ok is False


def test_validate_image_passes_valid_png():
    rep = v.validate_tool_result("generate_image", {}, {"data_b64": _b64(_png_bytes())})
    assert rep.ok is True


def test_validate_image_propagates_explicit_error():
    rep = v.validate_tool_result("generate_image", {}, {"error": "upstream timeout"})
    assert rep.ok is False
    assert "timeout" in rep.reason


def test_validate_audio_rejects_small_payload():
    rep = v.validate_tool_result("generate_audio", {}, {"data_b64": _b64(b"\xff\xfb")})
    assert rep.ok is False


def test_validate_audio_accepts_mp3_magic_bytes():
    payload = b"ID3" + b"\x00" * 4096
    rep = v.validate_tool_result("generate_audio", {}, {"data_b64": _b64(payload)})
    assert rep.ok is True


def test_validate_write_file_missing_path():
    rep = v.validate_tool_result("write_file", {"path": "/nonexistent/path/xyz"}, {"path": "/nonexistent/path/xyz"})
    assert rep.ok is False


def test_validate_write_file_existing(tmp_path):
    target = tmp_path / "out.txt"
    target.write_text("hello")
    rep = v.validate_tool_result("write_file", {"path": str(target)}, {"path": str(target)})
    assert rep.ok is True
    assert rep.details.get("size") == len("hello")


def test_validate_shell_bad_exit_code():
    rep = v.validate_tool_result("execute_shell", {}, {"exit_code": 1, "stderr": "no such file", "stdout": ""})
    assert rep.ok is False


def test_validate_shell_panic_in_stderr():
    rep = v.validate_tool_result("execute_shell", {}, {"exit_code": 0, "stderr": "Traceback (most recent call last)", "stdout": ""})
    assert rep.ok is False


def test_validate_shell_clean_run():
    rep = v.validate_tool_result("execute_shell", {}, {"exit_code": 0, "stdout": "ok", "stderr": ""})
    assert rep.ok is True


def test_validate_autogui_empty_summary():
    rep = v.validate_tool_result("autogui_task", {}, {"status": "done", "summary": ""})
    assert rep.ok is False


def test_validate_autogui_with_summary():
    rep = v.validate_tool_result("autogui_task", {}, {"status": "done", "summary": "Opened the calculator."})
    assert rep.ok is True


def test_validate_mcp_error():
    rep = v.validate_tool_result("mcp_call", {}, {"error": "server not running"})
    assert rep.ok is False


def test_validate_unknown_tool_is_permissive():
    rep = v.validate_tool_result("unknown_tool", {}, {"anything": "goes"})
    assert rep.ok is True


# ---------------------------------------------------------------------------
# Judge
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_judge_skipped_when_mode_off():
    chat_complete = AsyncMock(return_value=("{}", {}))
    rep = await v.judge_tool_result(
        goal="x", tool="generate_image", args={}, result={"data_b64": "AAA"},
        config={"verification": {"mode": "validators_only"}},
        chat_complete=chat_complete,
    )
    assert rep.skipped is True
    chat_complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_judge_skipped_when_no_model():
    chat_complete = AsyncMock(return_value=("{}", {}))
    rep = await v.judge_tool_result(
        goal="x", tool="generate_image", args={}, result={},
        config={"verification": {"mode": "validators_and_judge"}, "default_model": ""},
        chat_complete=chat_complete,
    )
    assert rep.skipped is True
    chat_complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_judge_parses_clean_json():
    chat_complete = AsyncMock(return_value=(
        '{"ok": false, "confidence": 0.9, "reason": "broken image", "suggested_fix": "use a bigger size"}',
        {},
    ))
    rep = await v.judge_tool_result(
        goal="generate a cat", tool="generate_image", args={"size": "64x64"}, result={"data_b64": "AAAA"},
        config={"verification": {"mode": "validators_and_judge"}, "default_model": "gpt-x"},
        chat_complete=chat_complete,
    )
    assert rep.skipped is False
    assert rep.ok is False
    assert rep.confidence == pytest.approx(0.9)
    assert rep.suggested_fix == "use a bigger size"


@pytest.mark.asyncio
async def test_judge_handles_garbage_response():
    chat_complete = AsyncMock(return_value=("not json at all", {}))
    rep = await v.judge_tool_result(
        goal="x", tool="generate_image", args={}, result={},
        config={"verification": {"mode": "validators_and_judge"}, "default_model": "gpt-x"},
        chat_complete=chat_complete,
    )
    assert rep.skipped is True
    assert "unparseable" in rep.skip_reason.lower()


@pytest.mark.asyncio
async def test_judge_handles_code_fence():
    chat_complete = AsyncMock(return_value=(
        '```json\n{"ok": true, "confidence": 0.8, "reason": "looks good"}\n```',
        {},
    ))
    rep = await v.judge_tool_result(
        goal="x", tool="generate_image", args={}, result={},
        config={"verification": {"mode": "validators_and_judge"}, "default_model": "gpt-x"},
        chat_complete=chat_complete,
    )
    assert rep.skipped is False
    assert rep.ok is True


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orchestrator_passes_through_when_disabled():
    async def runner(args):
        return {"data_b64": "AAA"}
    result, trace = await v.verify_and_maybe_retry(
        tool="generate_image", args={}, result={"data_b64": "AAA"}, goal="x",
        config={"verification": {"enabled": False}}, execute_again=runner,
    )
    assert result == {"data_b64": "AAA"}
    assert trace.events == []


@pytest.mark.asyncio
async def test_orchestrator_passes_through_when_tool_gated_off():
    async def runner(args):
        return {"data_b64": "AAA"}
    cfg = {"verification": {"enabled": True, "mode": "validators_only", "tools": {"generate_image": False}}}
    result, trace = await v.verify_and_maybe_retry(
        tool="generate_image", args={}, result={"data_b64": "AAA"}, goal="x",
        config=cfg, execute_again=runner,
    )
    assert trace.events == []


@pytest.mark.asyncio
async def test_orchestrator_retries_until_max_then_returns_last_result():
    calls = {"n": 0}

    async def runner(args):
        calls["n"] += 1
        return {"data_b64": "AAA"}  # always invalid

    cfg = {"verification": {"enabled": True, "mode": "validators_only", "retries": 2}}
    result, trace = await v.verify_and_maybe_retry(
        tool="generate_image", args={}, result={"data_b64": "AAA"}, goal="x",
        config=cfg, execute_again=runner,
    )
    assert calls["n"] == 2  # one retry per round above the original
    assert trace.final_ok is False
    assert trace.final_attempt == 3


@pytest.mark.asyncio
async def test_orchestrator_retries_then_succeeds():
    calls = {"n": 0}
    good_b64 = _b64(_png_bytes())

    async def runner(args):
        calls["n"] += 1
        return {"data_b64": good_b64}

    cfg = {"verification": {"enabled": True, "mode": "validators_only", "retries": 1}}
    result, trace = await v.verify_and_maybe_retry(
        tool="generate_image", args={}, result={"data_b64": "AAA"}, goal="x",
        config=cfg, execute_again=runner,
    )
    assert trace.final_ok is True
    assert trace.final_attempt == 2
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_orchestrator_validator_pass_no_retry():
    good_b64 = _b64(_png_bytes())
    runs = {"n": 0}

    async def runner(args):
        runs["n"] += 1
        return {"data_b64": good_b64}

    cfg = {"verification": {"enabled": True, "mode": "validators_only", "retries": 3}}
    result, trace = await v.verify_and_maybe_retry(
        tool="generate_image", args={}, result={"data_b64": good_b64}, goal="x",
        config=cfg, execute_again=runner,
    )
    assert runs["n"] == 0
    assert trace.final_ok is True
    assert trace.final_attempt == 1


@pytest.mark.asyncio
async def test_orchestrator_judge_failure_triggers_retry():
    good_b64 = _b64(_png_bytes())
    calls = {"n": 0}

    async def runner(args):
        calls["n"] += 1
        return {"data_b64": good_b64}

    chat_complete = AsyncMock(side_effect=[
        ('{"ok": false, "confidence": 0.95, "reason": "wrong subject", "suggested_fix": "add cat"}', {}),
        ('{"ok": true, "confidence": 0.9, "reason": "correct now"}', {}),
    ])
    cfg = {
        "verification": {"enabled": True, "mode": "validators_and_judge", "retries": 1,
                         "judge_confidence_threshold": 0.7},
        "default_model": "gpt-x",
    }
    result, trace = await v.verify_and_maybe_retry(
        tool="generate_image", args={"prompt": "draw a cat"}, result={"data_b64": good_b64},
        goal="draw a cat", config=cfg, execute_again=runner,
        chat_complete=chat_complete,
    )
    assert calls["n"] == 1  # one retry
    assert trace.final_ok is True
    # Judge events should appear twice
    judge_events = [e for e in trace.events if e.kind == "judge"]
    assert len(judge_events) == 2
