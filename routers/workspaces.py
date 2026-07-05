"""Workspace CRUD, export/import bundles, and bundle manifests.

Extracted from app.py (Phase 3). Route paths, request/response shapes, and
behavior are unchanged. Handlers reach shared state through the services
package (module-attribute access for anything tests monkeypatch via app.py's
forwarding properties).
"""
from __future__ import annotations

import io
import json
import time
import uuid
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from services import session, skills, storage, tools

router = APIRouter()

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


@router.get("/api/workspaces")
async def list_workspaces_endpoint(request: Request) -> dict:
    session._require_local_caller(request)
    return storage.load_workspaces()


@router.get("/api/workspaces/{wid}")
async def get_workspace(request: Request, wid: str) -> dict:
    session._require_local_caller(request)
    data = storage.load_workspaces()
    w = next((x for x in data["workspaces"] if x["id"] == wid), None)
    if not w:
        raise HTTPException(404, "Workspace not found")
    return w


@router.post("/api/workspaces")
async def upsert_workspace(w: WorkspaceIn, request: Request) -> dict:
    session._require_local_caller(request)
    # Reject project_root values that escape storage.WORKSPACE_DIR up front so the user
    # gets actionable feedback. Relative paths are resolved against
    # storage.WORKSPACE_DIR (e.g., "my-project" → "<workspace_dir>/my-project") rather
    # than the server process CWD. _resolve_project_root still clamps as
    # defense-in-depth.
    if w.project_root:
        base = Path(storage.WORKSPACE_DIR).resolve()
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
        # surprises. The path is already proven safe (under storage.WORKSPACE_DIR).
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
    data = storage.load_workspaces()
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
    storage.save_json(storage.WORKSPACES_PATH, data)
    return {"id": wid}


@router.delete("/api/workspaces/{wid}")
async def delete_workspace(request: Request, wid: str) -> dict:
    session._require_local_caller(request)
    data = storage.load_workspaces()
    data["workspaces"] = [x for x in data["workspaces"] if x["id"] != wid]
    storage.save_json(storage.WORKSPACES_PATH, data)
    cfg = storage.load_config()
    if cfg.get("active_workspace_id") == wid:
        cfg["active_workspace_id"] = ""
        storage.save_json(storage.CONFIG_PATH, cfg)
    return {"ok": True}


@router.get("/api/workspaces/{wid}/export")
async def export_workspace(request: Request, wid: str) -> Response:
    session._require_local_caller(request)
    data = storage.load_workspaces()
    w = next((x for x in data["workspaces"] if x["id"] == wid), None)
    if not w:
        raise HTTPException(404, "Workspace not found")
    prompts = storage.load_prompts()
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
            skill = skills.load_skill_content(sid)
            if skill:
                path = storage.SKILLS_DIR / f"{sid}.md"
                if path.exists():
                    zf.write(path, f"skills/{sid}.md")
        # MCP stubs (no secrets)
        mcp_data = storage.load_mcp_servers()
        mcp_stubs = []
        for sname in w.get("active_mcp_servers", []):
            s = next((x for x in mcp_data.get("servers", []) if x["name"] == sname), None)
            if s:
                stub = {k: v for k, v in s.items() if k not in ("env",)}
                stub["env_keys"] = list((s.get("env") or {}).keys())
                mcp_stubs.append(stub)
        zf.writestr("mcp_servers.json", json.dumps({"servers": mcp_stubs}, indent=2))
        # CLI tools
        cli_data = storage.load_cli_tools()
        cli_items = [c for c in cli_data.get("tools", []) if c["id"] in w.get("active_cli_tools", [])]
        zf.writestr("cli_tools.json", json.dumps({"tools": cli_items}, indent=2))
        # Bundle manifest (filenames + hashes provided by client via query param)
        # The actual file bytes are NOT included — the manifest is just metadata so
        # the recipient knows which bundles existed and can provide the files themselves.
        bundle_manifest = w.get("bundle_manifest")
        if bundle_manifest and isinstance(bundle_manifest, list):
            zf.writestr("bundle_manifest.json", json.dumps(bundle_manifest, indent=2))
    buf.seek(0)
    safe_name = tools._slug(w["name"], "workspace")
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.bwui"'},
    )


@router.post("/api/workspaces/{wid}/bundle-manifest")
async def set_workspace_bundle_manifest(request: Request, wid: str) -> dict:
    """Client posts bundle metadata (filenames + hashes) to be included in exports."""
    session._require_local_caller(request)
    body = await request.json()
    manifest = body.get("manifest") if isinstance(body, dict) else None
    if not isinstance(manifest, list):
        raise HTTPException(400, "Expected {'manifest': [...]} body.")
    data = storage.load_workspaces()
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
    storage.save_json(storage.WORKSPACES_PATH, data)
    return {"ok": True, "bundle_count": len(safe_manifest)}


_MAX_BUNDLE_BYTES = 10 * 1024 * 1024       # 10 MB compressed
_MAX_MEMBER_BYTES = 2 * 1024 * 1024        # 2 MB per uncompressed member
_MAX_BUNDLE_MEMBERS = 500                  # cap member count to bound iteration cost
_MAX_BUNDLE_TOTAL_BYTES = 50 * 1024 * 1024  # cap total uncompressed bytes (zip-bomb guard)


@router.post("/api/workspaces/import")
async def import_workspace(request: Request, file: UploadFile = File(...)) -> dict:
    session._require_local_caller(request)
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
                p_data = storage.load_prompts()
                existing = next((x for x in p_data["prompts"] if x["id"] == p["id"]), None)
                if not existing:
                    p_data["prompts"].append(p)
                    storage.save_json(storage.PROMPTS_PATH, p_data)
                prompt_id = p["id"]
            # Import skills. Skip any skill whose target filename already
            # exists in storage.SKILLS_DIR so a bundle can't silently overwrite a
            # user's local skill with the same name. The names of the
            # skipped files come back in the response so the UI can show
            # the user what wasn't imported.
            imported_skills: list[str] = []
            skipped_skills: list[str] = []
            for name in names:
                if name.startswith("skills/") and name.endswith(".md"):
                    skill_bytes = zf.read(name)
                    dest = storage.SKILLS_DIR / Path(name).name
                    if dest.exists():
                        skipped_skills.append(dest.name)
                        continue
                    dest.write_bytes(skill_bytes)
                    imported_skills.append(dest.name)
            # Import CLI tools
            if "cli_tools.json" in names:
                imported_cli = json.loads(zf.read("cli_tools.json"))
                cli_data = storage.load_cli_tools()
                existing_ids = {c["id"] for c in cli_data.get("tools", [])}
                for c in imported_cli.get("tools", []):
                    if c["id"] not in existing_ids:
                        cli_data["tools"].append(c)
                storage.save_json(storage.CLI_PATH, cli_data)
            # Import MCP server stubs (safe fields only — env keys noted but not restored)
            if "mcp_servers.json" in names:
                imported_mcp = json.loads(zf.read("mcp_servers.json"))
                mcp_data = storage.load_mcp_servers()
                existing_names = {s["name"] for s in mcp_data.get("servers", [])}
                for s in imported_mcp.get("servers", []):
                    if s.get("name") and s["name"] not in existing_names:
                        # env_keys is informational; don't restore actual env values
                        stub = {k: v for k, v in s.items() if k != "env_keys"}
                        mcp_data["servers"].append(stub)
                storage.save_json(storage.MCP_PATH, mcp_data)
        # Create the workspace
        wid = uuid.uuid4().hex[:8]
        ws_data = storage.load_workspaces()
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
        storage.save_json(storage.WORKSPACES_PATH, ws_data)
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
