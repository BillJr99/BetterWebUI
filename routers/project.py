"""Project file tree, file metadata/content, checkpoints, and revert.

Extracted from app.py (Phase 3). Route paths, request/response shapes, and
behavior are unchanged. Handlers reach shared state through the services
package (module-attribute access for anything tests monkeypatch via app.py's
forwarding properties).
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from services import prompt_builder, session, storage, tools

router = APIRouter()

# --- Project (file tree + checkpoints) ---
@router.get("/api/project/tree")
async def project_tree(request: Request, path: str = "") -> dict:
    session._require_local_caller(request)
    cfg = storage.load_config()
    workspace = prompt_builder.resolve_active_workspace(cfg)
    root, clamped = tools._resolve_project_root_info(workspace)
    root_path = Path(root).resolve()

    # Determine which subdirectory to list (support lazy directory expansion)
    if path:
        target = (root_path / path).resolve()
        try:
            target.relative_to(root_path)
        except ValueError:
            raise HTTPException(403, "Path outside project root.")
    else:
        target = root_path

    if not target.exists():
        raise HTTPException(404, "Path not found.")
    if not target.is_dir():
        raise HTTPException(400, f"'{path or '.'}' is not a directory.")

    entries: list[dict] = []
    try:
        for p in sorted(target.iterdir()):
            if p.name.startswith("."):
                continue
            # Skip symlinks entirely so a link inside the project root that
            # points outside doesn't leak the target's metadata (size/mtime)
            # in the listing. /api/project/file already blocks reading them.
            if p.is_symlink():
                continue
            rel = str(p.relative_to(root_path))
            # Use lstat to be explicit about not following links; the
            # is_symlink() check above already filters them, but lstat keeps
            # the metadata accurate for the entry we list.
            st = p.lstat()
            if p.is_dir():
                entries.append({"type": "dir", "name": p.name, "path": rel})
            else:
                entries.append({
                    "type": "file",
                    "name": p.name,
                    "path": rel,
                    "size": st.st_size,
                    "modified_at": int(st.st_mtime),
                    "ext": p.suffix.lower(),
                })
    except Exception as exc:
        raise HTTPException(500, f"Could not list directory: {exc}")
    # Don't leak the absolute filesystem root to the client; the frontend
    # only needs the relative entries to render and request further paths.
    # Two flags so the UI can show three distinct states:
    #   * project_root_set=false                       → no value configured
    #   * project_root_set=false, project_root_clamped → invalid value, fell
    #                                                    back to the safe base
    #   * project_root_set=true                        → configured and honored
    has_value = bool((workspace or {}).get("project_root"))
    project_root_set = has_value and not clamped
    project_root_clamped = has_value and clamped
    return {
        "entries": entries,
        "project_root_set": project_root_set,
        "project_root_clamped": project_root_clamped,
    }


_MAX_PROJECT_FILE_BYTES = 1 * 1024 * 1024  # 1 MB cap for /api/project/file


@router.get("/api/project/file")
async def project_file(request: Request, path: str, include_content: bool = False) -> dict:
    session._require_local_caller(request)
    cfg = storage.load_config()
    workspace = prompt_builder.resolve_active_workspace(cfg)
    root = tools._resolve_project_root(workspace)
    full = Path(root) / path
    try:
        full.resolve().relative_to(Path(root).resolve())
    except ValueError:
        raise HTTPException(403, "Path outside project root.")
    if not full.exists():
        raise HTTPException(404, "File not found.")
    if not full.is_file():
        raise HTTPException(400, "Path is not a file.")
    size = full.stat().st_size
    truncated = size > _MAX_PROJECT_FILE_BYTES
    content = None
    is_binary = False
    if include_content:
        try:
            # Read cap+1 bytes so we can detect truncation from the read itself
            # (not just from stat, which can lie on streaming filesystems or fifos).
            with full.open("rb") as fh:
                raw = fh.read(_MAX_PROJECT_FILE_BYTES + 1)
            truncated = len(raw) > _MAX_PROJECT_FILE_BYTES
            if truncated:
                raw = raw[:_MAX_PROJECT_FILE_BYTES]
            # NUL byte heuristic + strict UTF-8 decode: anything that fails either
            # check is treated as binary so the diff modal's binary guard works.
            if b"\x00" in raw:
                content = base64.b64encode(raw).decode("ascii")
                is_binary = True
            else:
                try:
                    content = raw.decode("utf-8", errors="strict")
                    is_binary = False
                except UnicodeDecodeError:
                    content = base64.b64encode(raw).decode("ascii")
                    is_binary = True
        except Exception as exc:
            raise HTTPException(500, f"Could not read file: {exc}")
    else:
        # Metadata-only path: sniff a small header to flag binary so the UI can
        # decide whether to do a follow-up include_content=true fetch. This
        # avoids reading the full 1 MB body when the caller only wants metadata.
        try:
            with full.open("rb") as fh:
                header = fh.read(4096)
            if b"\x00" in header:
                is_binary = True
            else:
                try:
                    header.decode("utf-8", errors="strict")
                    is_binary = False
                except UnicodeDecodeError:
                    is_binary = True
        except Exception:
            # If even the header read fails, default to "looks binary" so the
            # UI shows the preview-not-available branch instead of attempting
            # a follow-up content fetch that will likely also fail.
            is_binary = True
    # The frontend treats binary files as "preview not available" and never
    # reads the bytes, so omit the base64 content by default to keep responses
    # small. Callers that genuinely need the bytes can pass include_content=true.
    payload = {
        "path": path,
        "is_binary": is_binary,
        "size": size,
        "modified_at": int(full.stat().st_mtime),
        "truncated": truncated,
    }
    # include_content gates the body for both text and binary so the frontend
    # can do a cheap metadata-only first request before deciding to fetch the
    # bytes. Defaults to false (set in the route signature).
    if include_content:
        payload["content"] = content
    return payload


@router.get("/api/project/checkpoints")
async def list_project_checkpoints(request: Request, filename: Optional[str] = None) -> dict:
    session._require_local_caller(request)
    if not filename:
        return {"checkpoints": []}
    cfg = storage.load_config()
    workspace = prompt_builder.resolve_active_workspace(cfg)
    wid = (workspace or {}).get("id", "default")
    return {"checkpoints": tools._list_checkpoints(wid, filename)}


class RevertIn(BaseModel):
    filename: str
    checkpoint_id: str


@router.post("/api/project/revert")
async def revert_project_file(r: RevertIn, request: Request) -> dict:
    session._require_local_caller(request)
    cfg = storage.load_config()
    workspace = prompt_builder.resolve_active_workspace(cfg)
    wid = (workspace or {}).get("id", "default")
    content = tools._get_checkpoint(wid, r.filename, r.checkpoint_id)
    if content is None:
        raise HTTPException(404, "Checkpoint not found.")
    root = tools._resolve_project_root(workspace)
    dest = Path(root) / r.filename
    # Reject path traversal: ensure dest stays under the resolved project root
    try:
        dest.resolve().relative_to(Path(root).resolve())
    except ValueError:
        raise HTTPException(403, "Path outside project root.")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        # content is raw bytes — binary checkpoints round-trip without loss.
        dest.write_bytes(content)
    except Exception as exc:
        raise HTTPException(500, f"Could not write file: {exc}")
    return {"ok": True, "filename": r.filename, "bytes": len(content)}
