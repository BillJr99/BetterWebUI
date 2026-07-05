"""Admin/diagnostic routes: config, models, lint, branding, health, explain-command, memory extraction.

Extracted from app.py (Phase 3). Route paths, request/response shapes, and
behavior are unchanged. Handlers reach shared state through the services
package (module-attribute access for anything tests monkeypatch via app.py's
forwarding properties).
"""
from __future__ import annotations

import hashlib
import platform
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import verification as _verification
from services import llm, mcp, session, skills, storage, tools

router = APIRouter()

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
        safe["workspace_dir"] = str(Path(storage.WORKSPACE_DIR).resolve())
    return safe
@router.get("/api/config")
async def get_config(request: Request) -> dict:
    return _public_config(storage.load_config(), include_paths=session._is_local_caller(request))


@router.post("/api/config")
async def set_config(patch: ConfigPatch, request: Request) -> dict:
    cfg = storage.load_config()
    payload = patch.model_dump(exclude_none=True)
    url_changed = False
    key_changed = False
    for k, v in payload.items():
        if k == "base_url":
            new_url = llm.normalize_base_url(v)
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
            profile = await llm.discover_profile(cfg["base_url"], cfg["api_key"])
            if profile:
                cfg["api_profile"] = profile
        except Exception:
            pass
    storage.save_json(storage.CONFIG_PATH, cfg)
    return _public_config(cfg, include_paths=session._is_local_caller(request))


# --- Models ---

@router.get("/api/models")
async def get_models() -> dict:
    cfg = storage.load_config()
    if not cfg.get("api_key") or not cfg.get("base_url"):
        return {"models": [], "error": "Set your OpenWebUI URL and API key first."}
    try:
        models = await llm.fetch_models(cfg)
    except HTTPException as exc:
        return {"models": [], "error": str(exc.detail)}
    return {"models": models}


@router.get("/api/recommend-model")
async def recommend_model(use_case: str = "general") -> dict:
    cfg = storage.load_config()
    try:
        models = await llm.fetch_models(cfg)
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
        for lite in light:
            m = next((x for x in models if lite in x["id"].lower()), None)
            if m:
                return {"recommendation": m, "reason": f"This efficient model works great for {use_case}."}
    return {"recommendation": models[0], "reason": "Using the first available model."}
# --- Linting ---

@router.get("/api/lint")
async def lint() -> dict:
    skill_issues = skills._lint_skills()
    mcp_issues = skills._lint_mcp()
    cli_issues = skills._lint_cli()
    all_issues = skill_issues + mcp_issues + cli_issues
    return {
        "ok": len(all_issues) == 0,
        "issues": all_issues,
        "skills": [i["issue"] for i in skill_issues],
        "mcp": [i["issue"] for i in mcp_issues],
        "cli": [i["issue"] for i in cli_issues],
    }


# --- Branding ---

@router.get("/api/branding")
async def get_branding() -> dict:
    return storage.load_json(storage.BRANDING_PATH, {"logo": None, "primary_color": None, "welcome": None, "institution": None})
# --- Command explanation ---

class ExplainCommandIn(BaseModel):
    command: str


@router.post("/api/explain-command")
async def explain_command(body: ExplainCommandIn, request: Request) -> dict:
    session._require_local_caller(request)
    cmd = body.command.strip()
    if not cmd:
        raise HTTPException(400, "Command is required.")
    key = hashlib.md5(cmd.encode()).hexdigest()[:16]
    if key in session._command_explanation_cache:
        return {"explanation": session._command_explanation_cache[key], "cached": True}
    cfg = storage.load_config()
    model = cfg.get("default_model", "")
    if not model:
        return {"explanation": "No model configured — cannot explain commands."}
    messages = [
        {"role": "system", "content": "You are a plain-English explainer for shell commands. Keep your explanation to one or two sentences that a non-technical person can understand. Do not include any code or technical jargon."},
        {"role": "user", "content": f"Explain this command:\n\n{cmd}"},
    ]
    try:
        text, _ = await llm.chat_complete(messages, model, cfg)
        session._command_explanation_cache[key] = text
        return {"explanation": text}
    except Exception as exc:
        return {"explanation": f"Could not explain: {exc}"}
# --- Memory extraction (client-side stored, server only synthesizes). ---

class MemoryExtractIn(BaseModel):
    user_message: str
    assistant_message: str
    model: Optional[str] = None


@router.post("/api/memory/extract")
async def memory_extract(body: MemoryExtractIn, request: Request) -> dict:
    session._require_local_caller(request)
    cfg = storage.load_config()
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
        text, _usage = await llm.chat_complete(messages, model, cfg)
    except Exception as exc:
        return {"candidates": [], "error": str(exc)[:200]}
    parsed = _verification._safe_json_parse(text)
    if not isinstance(parsed, dict):
        return {"candidates": []}
    raw_candidates = parsed.get("candidates")
    if not isinstance(raw_candidates, list):
        raw_candidates = []
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
# --- Health ---

@router.get("/api/health")
async def health() -> dict:
    mcp_status = mcp.mcp_manager.status()
    lint_issues = skills._lint_skills() + skills._lint_mcp() + skills._lint_cli()
    return {
        "ok": True,
        "platform": platform.system(),
        "shell": tools.detect_shell()[1],
        "skills": len(skills.list_skill_files()),
        "workspaces": len(storage.load_workspaces()["workspaces"]),
        "mcp_servers": len(mcp_status),
        "mcp_running": sum(1 for s in mcp_status if s.get("running")),
        "cli_tools": len(storage.load_cli_tools()["tools"]),
        "lint_issues": len(lint_issues),
        "session_trusted_commands": len(session._session_trusted_commands),
    }
