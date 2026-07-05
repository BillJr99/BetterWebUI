"""File uploads (persistent + transient), voice transcription, and TTS.

Extracted from app.py (Phase 3). Route paths, request/response shapes, and
behavior are unchanged. Handlers reach shared state through the services
package (module-attribute access for anything tests monkeypatch via app.py's
forwarding properties).
"""
from __future__ import annotations

import base64
import re
import shutil
import uuid
from pathlib import Path
from typing import Optional

import aiofiles
import httpx
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from services import llm, session, storage, tools, transient

router = APIRouter()

# --- File uploads ---

@router.post("/api/upload")
async def upload_file(file: UploadFile = File(...)) -> dict:
    safe_name = f"{uuid.uuid4().hex}_{Path(file.filename or 'file').name}"
    dest = storage.UPLOADS_DIR / safe_name
    async with aiofiles.open(dest, "wb") as f:
        while chunk := await file.read(1024 * 64):
            await f.write(chunk)
    return {"url": f"/uploads/{safe_name}", "filename": file.filename, "content_type": file.content_type}


@router.post("/api/uploads/transient")
async def upload_transient_file(request: Request, file: UploadFile = File(...)) -> dict:
    """Accept a file scoped to a single chat. Files older than the TTL
    are swept automatically. The caller passes chat_id as a query param."""
    session._require_local_caller(request)
    chat_id = request.query_params.get("chat_id") or "anon"
    # Sanitize chat_id: alphanumeric / dash / underscore only.
    chat_id = re.sub(r"[^A-Za-z0-9_-]+", "_", chat_id)[:64].strip("._-") or "anon"
    chat_dir = transient._transient_root() / chat_id
    chat_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex}_{Path(file.filename or 'file').name}"
    dest = chat_dir / safe_name
    async with aiofiles.open(dest, "wb") as f:
        while chunk := await file.read(1024 * 64):
            await f.write(chunk)
    return {
        "url": f"/uploads/transient/{chat_id}/{safe_name}",
        "filename": file.filename,
        "content_type": file.content_type,
    }


@router.delete("/api/uploads/transient/{chat_id}")
async def delete_transient_chat(chat_id: str, request: Request) -> dict:
    session._require_local_caller(request)
    chat_id = re.sub(r"[^A-Za-z0-9_-]+", "_", chat_id)[:64].strip("._-")
    if not chat_id:
        raise HTTPException(400, "Invalid chat_id.")
    chat_dir = transient._transient_root() / chat_id
    if chat_dir.exists():
        shutil.rmtree(chat_dir, ignore_errors=True)
    return {"ok": True, "chat_id": chat_id}
# --- Voice transcription ---

_MAX_TRANSCRIBE_BYTES = 25 * 1024 * 1024  # 25 MB cap for /api/transcribe uploads


@router.post("/api/transcribe")
async def transcribe_audio(request: Request, file: UploadFile = File(...)) -> dict:
    # Proxies user-API-key requests to the backend, so restrict to local
    # callers (matches /api/tts and /api/explain-command).
    session._require_local_caller(request)
    cfg = storage.load_config()
    if not cfg.get("api_key") or not cfg.get("base_url"):
        raise HTTPException(400, "Set your OpenWebUI URL and API key first.")
    profile = llm.active_profile(cfg)
    base = llm.normalize_base_url(cfg["base_url"])
    transcribe_url = f"{base}{profile.get('transcribe', '/api/v1/audio/transcriptions')}"
    headers = {"Authorization": f"Bearer {cfg['api_key']}"}
    audio_bytes = await file.read(_MAX_TRANSCRIBE_BYTES + 1)
    if len(audio_bytes) > _MAX_TRANSCRIBE_BYTES:
        raise HTTPException(413, "Audio upload too large (max 25 MB).")
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(
                transcribe_url,
                headers=headers,
                files={"file": (file.filename or "audio.webm", audio_bytes, file.content_type or "audio/webm")},
                data={"model": "whisper-1"},
            )
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"Transcription request failed: {exc}")
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, f"Transcription failed: {resp.text[:300]}")
    try:
        body = resp.json()
        return {"text": body.get("text", "")}
    except Exception:
        return {"text": resp.text}


# --- Text-to-speech (read-aloud) ---

class TtsIn(BaseModel):
    text: str
    voice: Optional[str] = None


@router.post("/api/tts")
async def tts_endpoint(body: TtsIn, request: Request) -> Response:
    session._require_local_caller(request)
    cfg = storage.load_config()
    if not cfg.get("api_key") or not cfg.get("base_url"):
        raise HTTPException(400, "Set your OpenWebUI URL and API key first.")
    voice = body.voice or cfg.get("tts_voice", "alloy")
    result = await tools.call_openwebui_audio(body.text[:4096], voice, cfg)
    audio_bytes = base64.b64decode(result["data_b64"])
    return Response(content=audio_bytes, media_type="audio/mpeg")
