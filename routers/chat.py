"""The main /api/chat SSE loop plus the BWUI_TEST_MODE-only test endpoints.

Extracted from app.py (Phase 3). Route paths, request/response shapes, and
behavior are unchanged. Handlers reach shared state through the services
package (module-attribute access for anything tests monkeypatch via app.py's
forwarding properties).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import verification as _verification
from services import llm, prompt_builder, session, sse_proxy, storage, tools
from services.errors import code_for_status, error_envelope
from services.request_ctx import request_id_of

logger = logging.getLogger("betterwebui.chat")

router = APIRouter()

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
@router.post("/api/chat")
async def chat(req: ChatRequest, request: Request) -> StreamingResponse:
    # /api/chat can drive side-effecting tools (execute_shell, write_file,
    # cli_call) which only require an /api/approve from the same operator.
    # Restricting both endpoints to local callers means a network-exposed
    # server can't be used to ride the operator's approval pipeline.
    session._require_local_caller(request)
    cfg = storage.load_config()
    if not cfg.get("api_key") or not cfg.get("base_url"):
        raise HTTPException(400, "Set your OpenWebUI URL and API key first.")
    model = req.model or cfg.get("default_model")
    if not model:
        raise HTTPException(400, "Pick a model first.")
    prompts = storage.load_prompts()
    cid = req.conversation_id or uuid.uuid4().hex
    workspace = prompt_builder.resolve_active_workspace(cfg)
    workspace_id = (workspace or {}).get("id", "")
    # Precedence: per-request mode → workspace.mode → config.chat_mode
    effective_mode = req.mode or (workspace or {}).get("mode") or cfg.get("chat_mode", "approve-each")

    queue: asyncio.Queue = asyncio.Queue()
    request_id = request_id_of(request)
    # Preserve the existing plan when resuming a conversation
    _existing_conv = storage.load_conversations().get("conversations", {}).get(cid, {})
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

        system_prompt = prompt_builder.build_system_prompt(
            cfg, prompts, effective_mode,
            user_memories=req.user_memories,
            use_vision=bool(req.use_vision),
            web_search_mode=(req.web_search_mode or "off"),
        )
        try:
            for _step in range(12):  # higher cap for subagent-heavy tasks
                history, n_dropped = llm.trim_to_context(history, system_prompt)
                if n_dropped:
                    await send_event("notice", {"message": f"Context trimmed: {n_dropped} older message(s) removed."})
                openai_messages = llm.to_openai_messages(history, system_prompt)
                consensus_runs = max(1, min(10, cfg.get("consensus_runs", 1)))
                await send_event("status", {"message": "Thinking…"})
                if consensus_runs > 1:
                    raw_responses = await asyncio.gather(
                        *[llm.chat_complete(openai_messages, model, cfg, chat_id=f"{cid}-{i}") for i in range(consensus_runs)],
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
                        text, usage = await llm.chat_complete(synthesis_messages, model, cfg, chat_id=cid)
                else:
                    text, usage = await llm.chat_complete(openai_messages, model, cfg, chat_id=cid)

                # Emit telemetry badge
                tokens_in = usage.get("prompt_tokens", 0)
                tokens_out = usage.get("completion_tokens", 0)
                elapsed = usage.get("elapsed_ms", 0)
                await send_event("assistant_text", {
                    "text": text,
                    "delta": text,  # SSE-reader alias used by integration tests
                    "telemetry": {
                        "tokens_in": tokens_in,
                        "tokens_out": tokens_out,
                        "elapsed_ms": elapsed,
                    },
                })
                history.append({"role": "assistant", "content": text})

                call = prompt_builder.extract_tool_call(text)
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

                async def _execute_with_args(args_override: dict) -> dict:
                    new_call = {"tool": call["tool"], "args": args_override}
                    return await tools.execute_tool(new_call, cfg, send_event, effective_mode, model)

                async def _screenshot_provider() -> Optional[str]:
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
                    except Exception as exc:
                        # Optional capability — verification proceeds without
                        # a screenshot, but leave a trace for debugging.
                        logger.debug("Verification screenshot unavailable: %s", exc)
                        return None
                    return None

                first_result = await tools.execute_tool(call, cfg, send_event, effective_mode, model)

                try:
                    result, vtrace = await _verification.verify_and_maybe_retry(
                        tool=call["tool"],
                        args=call["args"],
                        result=first_result,
                        goal=user_goal_for_verif,
                        config=cfg,
                        execute_again=_execute_with_args,
                        chat_complete=llm.chat_complete,
                        screenshot_provider=_screenshot_provider,
                    )
                except Exception as exc:
                    # Verification is best-effort: fall back to the unverified
                    # result rather than failing the whole turn, but say so.
                    logger.warning(
                        "Verification of tool '%s' crashed (%s: %s); using unverified result.",
                        call["tool"], type(exc).__name__, exc,
                    )
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
                        _verif_log_dir = storage.DATA_DIR / "verification"
                        _verif_log_dir.mkdir(parents=True, exist_ok=True)
                        with open(_verif_log_dir / f"{cid}.jsonl", "a", encoding="utf-8") as _vf:
                            _vf.write(json.dumps({
                                "ts": time.time(),
                                "chat_id": cid,
                                "tool": call["tool"],
                                "trace": vtrace.to_dict(),
                            }) + "\n")
                    except Exception as exc:
                        logger.warning("Could not append verification audit log for %s: %s", cid, exc)

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
            storage.save_conversation(cid, title, history, current_task_plan, workspace_id)
            await send_event("done", {
                "_done": True,
                "conversation_id": cid,
                "messages": history,
                "task_plan": current_task_plan,
            })
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else json.dumps(jsonable_encoder(exc.detail))
            logger.warning("Chat turn failed (%s): %s", exc.status_code, detail)
            # "message" is the legacy field the frontend/e2e helpers read;
            # "error" carries the canonical envelope (see services/errors.py).
            await send_event("error", {
                "message": detail,
                **error_envelope(code_for_status(exc.status_code), detail, request_id=request_id),
            })
        except Exception as exc:
            logger.exception("Chat turn crashed")
            message = f"{type(exc).__name__}: {exc}"
            await send_event("error", {
                "message": message,
                **error_envelope(
                    "internal_error",
                    message,
                    hint="Try again; if this keeps happening check the server logs.",
                    request_id=request_id,
                ),
            })
        finally:
            await queue.put(None)

    task = asyncio.create_task(run_loop())

    async def event_stream() -> AsyncGenerator[bytes, None]:
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=sse_proxy.KEEPALIVE_INTERVAL)
                except asyncio.TimeoutError:
                    # Nothing to say yet (model thinking, tool running, or an
                    # approval dialog waiting on the user) — emit an SSE
                    # comment so proxies/browsers don't drop the connection.
                    yield b": keepalive\n\n"
                    continue
                if item is None:
                    break
                yield f"event: {item['event']}\ndata: {json.dumps(item['data'])}\n\n".encode("utf-8")
        finally:
            task.cancel()

    return StreamingResponse(event_stream(), media_type="text/event-stream")
# --- Test-only reset endpoint ---
# Gated behind BWUI_TEST_MODE=1 so it never appears in production. Used by the
# Playwright UI suite to wipe persistent state between specs without restarting
# the server.

@router.post("/api/test/reset")
async def test_reset() -> dict:
    if os.environ.get("BWUI_TEST_MODE") != "1":
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Not Found")
    wiped = []
    for path in (storage.CONVERSATIONS_PATH, storage.WORKSPACES_PATH, storage.PROMPTS_PATH,
                 storage.MCP_PATH, storage.CLI_PATH):
        if path.exists():
            try:
                path.unlink()
                wiped.append(path.name)
            except OSError:
                pass
    # Reset onboarding_done in config WITHOUT deleting config.json — deleting it
    # would race with parallel tests' ensureConfigured() that just set up
    # base_url + api_key, leaving them with a stripped config mid-test.
    if storage.CONFIG_PATH.exists():
        try:
            cfg = storage.load_config()
            if cfg.get("onboarding_done"):
                cfg["onboarding_done"] = False
                storage.save_json(storage.CONFIG_PATH, cfg)
                wiped.append("onboarding_done")
        except Exception:
            pass
    session._session_trusted_commands.clear()
    session._command_explanation_cache.clear()
    return {"ok": True, "wiped": wiped}


@router.post("/api/test/mock-chat")
async def test_mock_chat(request: Request) -> dict:
    """Toggle the chat mock on/off at runtime. Only available when BWUI_TEST_MODE=1.

    Body: {"enabled": true, "response": "optional custom text"}
    Enabling makes llm.chat_complete() return the canned response instantly so UI
    tests exercise rendering/flow without waiting for a real model.
    """
    if os.environ.get("BWUI_TEST_MODE") != "1":
        raise HTTPException(status_code=404, detail="Not Found")
    body = await request.json()
    llm._mock_chat_enabled = bool(body.get("enabled", True))
    if "response" in body:
        llm._mock_chat_text = str(body["response"])
    return {"mock_chat": llm._mock_chat_enabled, "response": llm._mock_chat_text}
