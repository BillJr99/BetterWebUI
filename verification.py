"""
verification.py — output validators, verifier-LLM judge, and bounded retry
loop used by the main agent loop in app.py.

Three public entry points:
- validate_tool_result(tool, args, result) — deterministic per-tool checks.
- judge_tool_result(goal, tool, args, result, config, screenshot_b64=None)
  — opt-in LLM call asking whether the tool achieved the user's intent.
- verify_and_maybe_retry(tool, args, result, goal, config, execute_again)
  — orchestrator: runs the validator, optionally the judge, retries
    bounded by config.verification.retries, returns the final result plus
    a VerificationTrace the UI can render.

All paths gracefully degrade. Verification failures never crash the
caller: they downgrade to a permissive trace and pass the original result
through. This file has no app.py imports — it takes a config dict and an
async callable so it can be unit-tested without the FastAPI app.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger("betterwebui.verification")

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except Exception:
    _PIL_AVAILABLE = False


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ValidationReport:
    ok: bool
    reason: str = ""
    suggested_fix: Optional[str] = None
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class JudgeReport:
    ok: bool
    confidence: float  # 0.0 - 1.0
    reason: str = ""
    suggested_fix: Optional[str] = None
    skipped: bool = False
    skip_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VerificationEvent:
    kind: str  # "validator" | "judge" | "retry" | "final"
    ok: bool
    detail: str
    attempt: int = 1
    extras: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VerificationTrace:
    tool: str
    events: list[VerificationEvent] = field(default_factory=list)
    final_ok: bool = True
    final_attempt: int = 1
    elapsed_ms: int = 0

    def add(self, ev: VerificationEvent) -> None:
        self.events.append(ev)

    def to_dict(self) -> dict:
        return {
            "tool": self.tool,
            "final_ok": self.final_ok,
            "final_attempt": self.final_attempt,
            "elapsed_ms": self.elapsed_ms,
            "events": [e.to_dict() for e in self.events],
        }


# ---------------------------------------------------------------------------
# Image / audio helpers
# ---------------------------------------------------------------------------

def _sniff_image(raw: bytes) -> Optional[str]:
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


def _sniff_audio(raw: bytes) -> Optional[str]:
    if len(raw) < 4:
        return None
    if raw[:3] == b"ID3" or raw[:2] == b"\xff\xfb" or raw[:2] == b"\xff\xf3":
        return "audio/mpeg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WAVE":
        return "audio/wav"
    if raw[:4] == b"OggS":
        return "audio/ogg"
    if raw[:4] == b"fLaC":
        return "audio/flac"
    return None


def _decode_b64(s: str) -> Optional[bytes]:
    try:
        return base64.b64decode(s or "", validate=False)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Per-tool validators
# ---------------------------------------------------------------------------

def _validate_image(args: dict, result: dict) -> ValidationReport:
    if isinstance(result, dict) and result.get("error"):
        return ValidationReport(ok=False, reason=str(result["error"]),
                                suggested_fix="Rephrase the prompt or try a smaller size.")
    raw_b64 = result.get("data_b64") if isinstance(result, dict) else None
    if not raw_b64:
        return ValidationReport(ok=False, reason="No image bytes returned.",
                                suggested_fix="Try again — the upstream returned no data.")
    raw = _decode_b64(raw_b64)
    if raw is None:
        return ValidationReport(ok=False, reason="Image base64 failed to decode.",
                                suggested_fix="Retry with a different model or size.")
    if len(raw) < 64:
        return ValidationReport(ok=False, reason=f"Image payload too small ({len(raw)} bytes).",
                                suggested_fix="Retry the generation.")
    mime = _sniff_image(raw)
    if mime is None:
        return ValidationReport(ok=False, reason="Returned bytes are not a recognised image format.",
                                suggested_fix="Retry, or pick a different image model.")
    details: dict = {"bytes": len(raw), "sniffed_mime": mime}
    if _PIL_AVAILABLE:
        try:
            with Image.open(io.BytesIO(raw)) as im:
                im.verify()
            with Image.open(io.BytesIO(raw)) as im:
                w, h = im.size
            details["width"], details["height"] = w, h
            if w < 64 or h < 64:
                return ValidationReport(ok=False, reason=f"Image too small ({w}x{h}).",
                                        suggested_fix="Request a larger size.", details=details)
        except Exception as exc:
            return ValidationReport(ok=False, reason=f"Image bytes did not decode: {exc}",
                                    suggested_fix="Retry the generation.", details=details)
    return ValidationReport(ok=True, reason="Image looks valid.", details=details)


def _validate_audio(args: dict, result: dict) -> ValidationReport:
    if isinstance(result, dict) and result.get("error"):
        return ValidationReport(ok=False, reason=str(result["error"]),
                                suggested_fix="Try a shorter text or different voice.")
    raw_b64 = result.get("data_b64") if isinstance(result, dict) else None
    if not raw_b64:
        return ValidationReport(ok=False, reason="No audio bytes returned.")
    raw = _decode_b64(raw_b64)
    if raw is None or len(raw) < 1024:
        return ValidationReport(ok=False, reason=f"Audio payload too small ({len(raw) if raw else 0} bytes).",
                                suggested_fix="Retry the speech synthesis.")
    mime = _sniff_audio(raw)
    if mime is None:
        return ValidationReport(ok=False, reason="Returned bytes are not a recognised audio format.")
    return ValidationReport(ok=True, reason="Audio looks valid.",
                            details={"bytes": len(raw), "sniffed_mime": mime})


def _validate_write_file(args: dict, result: dict) -> ValidationReport:
    if isinstance(result, dict) and result.get("error"):
        return ValidationReport(ok=False, reason=str(result["error"]))
    path = (result or {}).get("path") or (args or {}).get("path")
    if not path:
        return ValidationReport(ok=False, reason="write_file returned no path.")
    try:
        import os
        if not os.path.exists(path):
            return ValidationReport(ok=False, reason=f"File '{path}' does not exist after write.",
                                    suggested_fix="Retry the write — the target directory may not exist.")
        size = os.path.getsize(path)
        return ValidationReport(ok=True, reason="File written.",
                                details={"path": path, "size": size})
    except Exception as exc:
        return ValidationReport(ok=False, reason=f"Could not stat written file: {exc}")


_PANIC_RE = re.compile(r"\b(Traceback|panic:|fatal error|Segmentation fault|UnhandledPromiseRejection)\b")


def _validate_execute_shell(args: dict, result: dict) -> ValidationReport:
    if not isinstance(result, dict):
        return ValidationReport(ok=False, reason="execute_shell returned non-dict.")
    if result.get("error"):
        return ValidationReport(ok=False, reason=str(result["error"]))
    exit_code = result.get("exit_code")
    stderr = result.get("stderr") or ""
    stdout = result.get("stdout") or ""
    if exit_code not in (None, 0):
        return ValidationReport(ok=False, reason=f"Command exited with code {exit_code}.",
                                suggested_fix="Inspect stderr and adjust the command.")
    if stderr and _PANIC_RE.search(stderr):
        return ValidationReport(ok=False, reason="Command stderr contains a panic / traceback.",
                                suggested_fix="Fix the underlying error before retrying.")
    if stdout and _PANIC_RE.search(stdout):
        return ValidationReport(ok=False, reason="Command stdout contains a panic / traceback.")
    return ValidationReport(ok=True, reason="Shell command finished cleanly.",
                            details={"exit_code": exit_code or 0})


def _validate_autogui(args: dict, result: dict) -> ValidationReport:
    if not isinstance(result, dict):
        return ValidationReport(ok=False, reason="autogui_task returned non-dict.")
    if result.get("error"):
        return ValidationReport(ok=False, reason=str(result["error"]))
    status = result.get("status") or ""
    if status == "error":
        return ValidationReport(ok=False, reason="AutoGUI reported an error status.",
                                suggested_fix="Re-issue the task with a clearer prompt.")
    summary = result.get("summary") or ""
    if not summary:
        return ValidationReport(ok=False, reason="AutoGUI produced no text events.",
                                suggested_fix="Re-issue the task — it may have timed out.")
    return ValidationReport(ok=True, reason="AutoGUI task completed.",
                            details={"status": status, "summary_chars": len(summary)})


def _validate_mcp(args: dict, result: dict) -> ValidationReport:
    if not isinstance(result, dict):
        return ValidationReport(ok=False, reason="mcp_call returned non-dict.")
    if result.get("error"):
        return ValidationReport(ok=False, reason=str(result["error"]))
    return ValidationReport(ok=True, reason="MCP call succeeded.")


_VALIDATORS: dict[str, Callable[[dict, dict], ValidationReport]] = {
    "generate_image": _validate_image,
    "generate_audio": _validate_audio,
    "write_file": _validate_write_file,
    "execute_shell": _validate_execute_shell,
    "cli_call": _validate_execute_shell,  # same shape
    "autogui_task": _validate_autogui,
    "mcp_call": _validate_mcp,
}


def validate_tool_result(tool: str, args: dict, result: Any) -> ValidationReport:
    """Deterministic per-tool check. Returns a permissive ok=True for tools
    without a validator so unknown tools never block the loop."""
    fn = _VALIDATORS.get(tool)
    if fn is None:
        return ValidationReport(ok=True, reason="No validator for this tool.")
    try:
        return fn(args or {}, result if isinstance(result, dict) else {})
    except Exception as exc:
        log.warning("Validator for %s crashed: %s", tool, exc)
        return ValidationReport(ok=True, reason=f"Validator crashed: {exc}")


# ---------------------------------------------------------------------------
# Judge LLM
# ---------------------------------------------------------------------------

def _summarise_result_for_judge(tool: str, result: Any) -> str:
    if not isinstance(result, dict):
        return str(result)[:1000]
    if tool in ("generate_image", "generate_audio"):
        meta = {k: result.get(k) for k in ("filename", "mime", "prompt", "voice", "source_url")
                if result.get(k) is not None}
        raw = _decode_b64(result.get("data_b64") or "") if result.get("data_b64") else None
        if raw:
            meta["bytes"] = len(raw)
            sniffed = _sniff_image(raw) if tool == "generate_image" else _sniff_audio(raw)
            if sniffed:
                meta["sniffed"] = sniffed
        return json.dumps(meta)
    if tool in ("execute_shell", "cli_call"):
        return json.dumps({
            "exit_code": result.get("exit_code"),
            "stdout": (result.get("stdout") or "")[:1024],
            "stderr": (result.get("stderr") or "")[:1024],
        })
    if tool == "autogui_task":
        return json.dumps({
            "status": result.get("status"),
            "summary": (result.get("summary") or "")[:1500],
            "errors": result.get("errors"),
        })
    # Fallback: stringify and truncate.
    try:
        s = json.dumps(result)
    except Exception:
        s = str(result)
    return s[:1500]


JUDGE_PROMPT = (
    "You are a verification judge. The user asked the assistant to do something, "
    "and the assistant ran a tool to do it. Decide whether the tool achieved "
    "the user's intent.\n\n"
    "User's goal: {goal}\n\n"
    "Tool: {tool}\n"
    "Tool args: {args}\n"
    "Tool result: {result_summary}\n"
    "{vision_note}"
    "\nRespond with JSON ONLY (no prose, no markdown fence) using exactly this schema:\n"
    "{{\"ok\": true|false, \"confidence\": 0.0-1.0, \"reason\": \"...\", \"suggested_fix\": \"...\" or null}}"
)


def _safe_json_parse(text: str) -> Optional[dict]:
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    try:
        return json.loads(text)
    except Exception:
        # try to find the first {...} block
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
        return None


async def judge_tool_result(
    goal: str,
    tool: str,
    args: dict,
    result: Any,
    config: dict,
    chat_complete: Callable[..., Awaitable[tuple[str, dict]]],
    screenshot_b64: Optional[str] = None,
) -> JudgeReport:
    """LLM judge. chat_complete is injected so this module doesn't import app.py."""
    verif = (config or {}).get("verification") or {}
    if verif.get("mode") != "validators_and_judge":
        return JudgeReport(ok=True, confidence=0.0, skipped=True, skip_reason="Judge disabled in config.")
    model = verif.get("judge_model") or config.get("default_model") or ""
    if not model:
        return JudgeReport(ok=True, confidence=0.0, skipped=True, skip_reason="No judge model configured.")
    result_summary = _summarise_result_for_judge(tool, result)
    user_parts: list = []
    prompt = JUDGE_PROMPT.format(
        goal=str(goal or "")[:1500],
        tool=tool,
        args=json.dumps(args or {})[:800],
        result_summary=result_summary,
        vision_note=("A screenshot of the current screen is attached for context.\n" if screenshot_b64 else ""),
    )
    if screenshot_b64:
        user_parts = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}},
        ]
        messages = [
            {"role": "system", "content": "Respond with JSON only."},
            {"role": "user", "content": user_parts},
        ]
    else:
        messages = [
            {"role": "system", "content": "Respond with JSON only."},
            {"role": "user", "content": prompt},
        ]
    try:
        text, _usage = await chat_complete(messages, model, config)
    except Exception as exc:
        log.warning("Judge call failed: %s", exc)
        return JudgeReport(ok=True, confidence=0.0, skipped=True, skip_reason=f"Judge call failed: {exc}")
    parsed = _safe_json_parse(text)
    if not parsed:
        return JudgeReport(ok=True, confidence=0.0, skipped=True,
                           skip_reason="Judge returned unparseable JSON.")
    ok = bool(parsed.get("ok", True))
    try:
        conf = float(parsed.get("confidence", 0.5))
    except Exception:
        conf = 0.5
    conf = max(0.0, min(1.0, conf))
    return JudgeReport(
        ok=ok,
        confidence=conf,
        reason=str(parsed.get("reason", ""))[:500],
        suggested_fix=(str(parsed.get("suggested_fix") or "")[:500] or None),
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def verify_and_maybe_retry(
    tool: str,
    args: dict,
    result: Any,
    goal: str,
    config: dict,
    execute_again: Callable[[dict], Awaitable[Any]],
    chat_complete: Optional[Callable[..., Awaitable[tuple[str, dict]]]] = None,
    screenshot_provider: Optional[Callable[[], Awaitable[Optional[str]]]] = None,
) -> tuple[Any, VerificationTrace]:
    """Run validators, then (optional) judge, then (optional) retry.

    - `execute_again(args_override)` re-runs the tool; it MUST return the
      same shape as the original result. The orchestrator passes a copy
      of the original args with a `verification_hint` key added so the
      executor (or the model) can adjust.
    - `chat_complete` is required when verification.mode == validators_and_judge.
    - `screenshot_provider` is an async callable returning a base64-encoded
      PNG of the current screen for autogui_task judging; None if unavailable.

    Returns the final (possibly-retried) result and a trace.
    """
    start = time.time()
    trace = VerificationTrace(tool=tool)
    verif = (config or {}).get("verification") or {}
    if not verif.get("enabled", True):
        trace.final_ok = True
        trace.elapsed_ms = int((time.time() - start) * 1000)
        return result, trace
    tool_gate = (verif.get("tools") or {}).get(tool, True)
    if not tool_gate:
        trace.final_ok = True
        trace.elapsed_ms = int((time.time() - start) * 1000)
        return result, trace
    mode = verif.get("mode", "validators_only")
    max_retries = int(verif.get("retries", 1) or 0)
    judge_threshold = float(verif.get("judge_confidence_threshold", 0.7) or 0.0)

    current_result: Any = result
    attempt = 1
    last_suggested_fix: Optional[str] = None

    while True:
        vr = validate_tool_result(tool, args, current_result)
        trace.add(VerificationEvent(
            kind="validator", ok=vr.ok, detail=vr.reason, attempt=attempt,
            extras={"details": vr.details, "suggested_fix": vr.suggested_fix},
        ))
        if not vr.ok:
            last_suggested_fix = vr.suggested_fix or last_suggested_fix

        judge_should_run = (
            vr.ok
            and mode == "validators_and_judge"
            and chat_complete is not None
        )
        jr: Optional[JudgeReport] = None
        if judge_should_run:
            screenshot_b64: Optional[str] = None
            if tool == "autogui_task" and screenshot_provider is not None:
                try:
                    screenshot_b64 = await screenshot_provider()
                except Exception as exc:
                    log.warning("Screenshot provider failed: %s", exc)
            try:
                jr = await judge_tool_result(
                    goal=goal, tool=tool, args=args, result=current_result,
                    config=config, chat_complete=chat_complete,
                    screenshot_b64=screenshot_b64,
                )
            except Exception as exc:
                log.warning("Judge orchestration failed: %s", exc)
                jr = JudgeReport(ok=True, confidence=0.0, skipped=True,
                                 skip_reason=f"Judge orchestration failed: {exc}")
            trace.add(VerificationEvent(
                kind="judge", ok=jr.ok if not jr.skipped else True,
                detail=(jr.skip_reason if jr.skipped else jr.reason),
                attempt=attempt,
                extras={
                    "confidence": jr.confidence,
                    "skipped": jr.skipped,
                    "suggested_fix": jr.suggested_fix,
                },
            ))
            if (not jr.skipped) and (not jr.ok) and jr.confidence >= judge_threshold:
                last_suggested_fix = jr.suggested_fix or last_suggested_fix

        validator_failed = not vr.ok
        judge_failed = (
            jr is not None and (not jr.skipped) and (not jr.ok)
            and jr.confidence >= judge_threshold
        )
        should_retry = (validator_failed or judge_failed) and attempt <= max_retries
        if not should_retry:
            trace.final_ok = (not validator_failed) and (not judge_failed)
            trace.final_attempt = attempt
            trace.add(VerificationEvent(
                kind="final", ok=trace.final_ok,
                detail=("All checks passed." if trace.final_ok else "Verification failed after all attempts."),
                attempt=attempt,
            ))
            trace.elapsed_ms = int((time.time() - start) * 1000)
            return current_result, trace

        attempt += 1
        retry_args = dict(args or {})
        if last_suggested_fix:
            retry_args["verification_hint"] = last_suggested_fix
        trace.add(VerificationEvent(
            kind="retry", ok=True,
            detail=f"Retrying (attempt {attempt}). Hint: {last_suggested_fix or 'none'}.",
            attempt=attempt,
        ))
        try:
            current_result = await execute_again(retry_args)
        except Exception as exc:
            trace.add(VerificationEvent(
                kind="final", ok=False,
                detail=f"Retry failed to execute: {exc}", attempt=attempt,
            ))
            trace.final_ok = False
            trace.final_attempt = attempt
            trace.elapsed_ms = int((time.time() - start) * 1000)
            return result, trace
