"""
BetterWebUI — a friendlier OpenWebUI front-end with skills, custom system
prompts, multimodal generation, MCP-style tooling, gated shell execution,
visible task plans, file-tree/diff/checkpoints, plan mode, subagents,
workspace bundles, conversation search/pinning/forking,
per-turn telemetry, onboarding wizard, and accessibility features.

This module is the composition root: FastAPI init, middleware, exception
handlers, lifespan, static mounts, and router registration. Domain logic
lives in services/ and route handlers in routers/ (Phase 3 decomposition).

Backwards compatibility: the test suite (and any external caller) may import
helpers from `app` or monkeypatch module attributes like `app.DATA_DIR` and
`app.chat_complete`. Plain re-exports below keep `from app import X` working;
the module-class swap at the bottom forwards attribute *writes* for the
monkeypatched names to the services module that now owns them, so patching
`app.X` still changes the value every handler reads at call time.
"""

import asyncio
import json
import logging
import logging.handlers
import os
import sys
import types
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from routers import ALL_ROUTERS
from services import llm as _llm_module
from services import sse_proxy as _sse_proxy_module
from services import storage as _storage_module
from services import transient as _transient_module

# ---------------------------------------------------------------------------
# Re-exports (import compatibility). Everything below used to be defined in
# this file; tests and external callers import them from `app`. The objects
# are re-exported by identity, so in-place mutation (approvals.__init__(),
# _session_trusted_commands.clear(), ApprovalState.wait patching, ...) is
# seen by the handlers too. Names that tests REBIND (setattr / mock.patch)
# are deliberately NOT re-exported here — they are served by the forwarding
# properties installed at the bottom of this file instead.
# ---------------------------------------------------------------------------
from services.catalog import (  # noqa: F401
    CLI_REGISTRY,
    ENDPOINT_PROFILES,
    MCP_REGISTRY,
    ONBOARDING_TEMPLATES,
)
from services.errors import code_for_status, error_envelope
from services.llm import (  # noqa: F401
    CONTEXT_TOKEN_LIMIT,
    active_profile,
    discover_profile,
    fetch_models,
    normalize_base_url,
    to_openai_messages,
    trim_to_context,
)
from services.mcp import MCPManager, MCPStdioClient, mcp_manager  # noqa: F401
from services.prompt_builder import (  # noqa: F401
    PLAN_MODE_BLOCK,
    RENDERING_PROTOCOL,
    TOOL_PROTOCOL,
    build_system_prompt,
    extract_tool_call,
    resolve_active_workspace,
)
from services.request_ctx import RequestIdFilter, request_id_ctx, request_id_of
from services.scheduled import (  # noqa: F401
    _emit_scheduled_notification,
    _run_scheduled_task,
    _scheduled_notifications,
)
from services.session import (  # noqa: F401
    ApprovalState,
    FileResponseStore,
    _command_explanation_cache,
    _is_local_caller,
    _require_local_caller,
    _session_trusted_commands,
    approvals,
    file_responses,
)
from services.skills import (  # noqa: F401
    _lint_cli,
    _lint_mcp,
    _lint_skills,
    list_skill_files,
    load_skill_content,
)
from services.storage import (  # noqa: F401
    ROOT,
    _FrontmatterPost,
    _load_frontmatter,
    load_cli_tools,
    load_config,
    load_conversations,
    load_json,
    load_mcp_servers,
    load_prompts,
    load_workspaces,
    save_conversation,
    save_json,
)
from services.tools import (  # noqa: F401
    _checkpoint_file,
    _get_checkpoint,
    _list_checkpoints,
    _resolve_project_root,
    _resolve_project_root_info,
    _should_skip_approval,
    _slug,
    call_fetch_url,
    call_openwebui_audio,
    call_openwebui_image,
    call_web_search,
    detect_shell,
    execute_tool,
    run_shell,
    run_subagent_loop,
    validate_image_bytes,
)
from services.transient import _sweep_transient_uploads, _transient_root  # noqa: F401

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_LOG_DIR = ROOT / "logs"


def _configure_logging() -> None:
    """Configure process-wide logging exactly once, at import time.

    - Level comes from BWUI_LOG_LEVEL (default INFO) so operators can turn on
      DEBUG without code changes.
    - Two handlers: a rotating file under logs/ and stderr.
    - The request-id filter is attached to every root handler, so the rid=
      field is stamped consistently on all records — request-path records get
      the middleware-assigned id (background tasks log rid=- since they run
      outside any request context).
    All betterwebui.* loggers propagate to root; none add their own handlers.
    """
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    level_name = os.environ.get("BWUI_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, None)
    if not isinstance(level, int):
        level = logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s [rid=%(request_id)s]: %(message)s",
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
    for handler in logging.getLogger().handlers:
        handler.addFilter(RequestIdFilter())


_configure_logging()
logger = logging.getLogger("betterwebui")


# ---------------------------------------------------------------------------
# Lifespan: MCP reconcile, transient-upload sweeping, scheduler
# ---------------------------------------------------------------------------

_transient_sweep_task: "asyncio.Task | None" = None
_scheduler_task: "asyncio.Task | None" = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _transient_sweep_task, _scheduler_task
    # ── startup ────────────────────────────────────────────────────────────
    try:
        await mcp_manager.reconcile()
    except Exception:
        # Startup continues without MCP servers; each server can still be
        # (re)started later from the UI.
        logging.getLogger("betterwebui.mcp").exception("MCP startup reconcile failed")
    # One sweep at boot so test fixtures get a clean state.
    try:
        _sweep_transient_uploads()
    except Exception as exc:
        logging.getLogger("betterwebui.uploads").warning("Boot-time upload sweep failed: %s", exc)
    _transient_sweep_task = asyncio.create_task(_transient_module._transient_sweep_loop())
    try:
        from scheduler import start_scheduler
        _scheduler_task = asyncio.create_task(start_scheduler(
            tasks_path=_storage_module.SCHEDULED_TASKS_PATH,
            run_callback=_run_scheduled_task,
            send_notification=_emit_scheduled_notification,
        ))
    except Exception as exc:
        logging.getLogger("betterwebui.scheduler").warning("Scheduler failed to start: %s", exc)
    yield
    # ── shutdown ───────────────────────────────────────────────────────────
    if _transient_sweep_task is not None:
        _transient_sweep_task.cancel()
    if _scheduler_task is not None:
        _scheduler_task.cancel()
    for name, client in list(mcp_manager.clients.items()):
        try:
            await client.stop()
        except Exception as exc:
            logging.getLogger("betterwebui.mcp").warning("Shutdown of MCP server '%s' failed: %s", name, exc)


app = FastAPI(title="BetterWebUI", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Request IDs + structured error envelopes
#
# Every request gets a correlation id (client-supplied X-Request-ID is
# honored, otherwise a fresh uuid). It is echoed in the X-Request-ID response
# header, tagged onto log records via services.request_ctx, and embedded in
# every error envelope so a user-visible failure can be matched to server
# logs.
#
# Error responses use the canonical envelope from services/errors.py:
#     {"error": {"code", "message", "hint", "request_id"}}
# The legacy top-level "detail" field is preserved for backward compatibility
# (the frontend and older callers read it).
# ---------------------------------------------------------------------------

@app.middleware("http")
async def _request_id_middleware(request: Request, call_next):
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    request.state.request_id = rid
    token = request_id_ctx.set(rid)
    try:
        response = await call_next(request)
    finally:
        request_id_ctx.reset(token)
    response.headers["X-Request-ID"] = rid
    return response


@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
    rid = request_id_of(request)
    detail = exc.detail
    message = detail if isinstance(detail, str) else json.dumps(jsonable_encoder(detail))
    body = error_envelope(code_for_status(exc.status_code), message, request_id=rid)
    body["detail"] = jsonable_encoder(detail)
    headers = {**(exc.headers or {}), "X-Request-ID": rid}
    return JSONResponse(body, status_code=exc.status_code, headers=headers)


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(request: Request, exc: RequestValidationError):
    rid = request_id_of(request)
    errors = jsonable_encoder(exc.errors())
    first = errors[0] if errors else {}
    loc = ".".join(str(p) for p in first.get("loc", []))
    hint = f"Check '{loc}': {first.get('msg', '')}" if loc else None
    body = error_envelope("validation_error", "Request validation failed.", hint=hint, request_id=rid)
    body["detail"] = errors
    return JSONResponse(body, status_code=422, headers={"X-Request-ID": rid})


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    rid = request_id_of(request)
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    body = error_envelope(
        "internal_error",
        f"{type(exc).__name__}: {exc}",
        hint="Try again; if this keeps happening check the server logs.",
        request_id=rid,
    )
    body["detail"] = body["error"]["message"]
    return JSONResponse(body, status_code=500, headers={"X-Request-ID": rid})


# ---------------------------------------------------------------------------
# Index, static mounts, and routers
# ---------------------------------------------------------------------------

@app.get("/")
async def index():
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
app.mount("/uploads", StaticFiles(directory=_storage_module.UPLOADS_DIR), name="uploads")

for _router in ALL_ROUTERS:
    app.include_router(_router)

# ─── Services integration ────────────────────────────────────────────────────

# Imported late on purpose: routes need the fully-initialised `app` above.
from services.routes import register_routes as _register_service_routes  # noqa: E402

_register_service_routes(app)


# ---------------------------------------------------------------------------
# Monkeypatch-compatible attribute forwarding.
#
# The test suite rebinds a handful of names on THIS module —
# monkeypatch.setattr(app_module, "DATA_DIR", tmp), plain assignment
# (app_module.WORKSPACE_DIR = ws), and mock.patch("app.chat_complete", ...).
# Those values now live in services modules, and every handler reads them
# from there via module-attribute access at call time. To keep the patches
# effective, this module's class is swapped for a ModuleType subclass whose
# data-descriptor properties forward get/set/delete for each such name to
# the owning services module.
#
# The delete path exists for mock.patch: on __exit__ for a non-__dict__
# attribute it calls delattr() and then, only if the attribute no longer
# exists, restores the saved original via setattr(). fdel therefore parks a
# sentinel that makes fget raise AttributeError so that restore fires.
# ---------------------------------------------------------------------------

_DELETED_SENTINEL = object()


def _forwarded_property(owner: types.ModuleType, attr: str) -> property:
    def fget(_mod: types.ModuleType) -> Any:
        value = getattr(owner, attr)
        if value is _DELETED_SENTINEL:
            raise AttributeError(attr)
        return value

    def fset(_mod: types.ModuleType, value: Any) -> None:
        setattr(owner, attr, value)

    def fdel(_mod: types.ModuleType) -> None:
        setattr(owner, attr, _DELETED_SENTINEL)

    return property(fget, fset, fdel)


class _CompatForwardingModule(types.ModuleType):
    """`app` module with property-forwarded legacy attributes."""


_FORWARDED_ATTRS: dict[str, tuple[types.ModuleType, str]] = {
    # Path constants (rebindable per-test via tests/conftest.py).
    "DATA_DIR": (_storage_module, "DATA_DIR"),
    "SKILLS_DIR": (_storage_module, "SKILLS_DIR"),
    "WORKSPACE_DIR": (_storage_module, "WORKSPACE_DIR"),
    "UPLOADS_DIR": (_storage_module, "UPLOADS_DIR"),
    "CHECKPOINTS_DIR": (_storage_module, "CHECKPOINTS_DIR"),
    "TASKS_DIR": (_storage_module, "TASKS_DIR"),
    "CONFIG_PATH": (_storage_module, "CONFIG_PATH"),
    "PROMPTS_PATH": (_storage_module, "PROMPTS_PATH"),
    "CONVERSATIONS_PATH": (_storage_module, "CONVERSATIONS_PATH"),
    "WORKSPACES_PATH": (_storage_module, "WORKSPACES_PATH"),
    "MCP_PATH": (_storage_module, "MCP_PATH"),
    "CLI_PATH": (_storage_module, "CLI_PATH"),
    "BRANDING_PATH": (_storage_module, "BRANDING_PATH"),
    "SCHEDULED_TASKS_PATH": (_storage_module, "SCHEDULED_TASKS_PATH"),
    # The chat model call (mock.patch'ed by SSE/memory/scheduler tests).
    "chat_complete": (_llm_module, "chat_complete"),
    # SSE keepalive cadence (mock.patch'ed by the keepalive test).
    "_sse_keepalive_interval": (_sse_proxy_module, "KEEPALIVE_INTERVAL"),
}

for _public_name, (_owner_module, _owner_attr) in _FORWARDED_ATTRS.items():
    setattr(_CompatForwardingModule, _public_name, _forwarded_property(_owner_module, _owner_attr))

sys.modules[__name__].__class__ = _CompatForwardingModule


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8765"))
    uvicorn.run(app, host=host, port=port, log_level="info")
