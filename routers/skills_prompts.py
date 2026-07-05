"""System prompt and skill CRUD routes.

Extracted from app.py (Phase 3). Route paths, request/response shapes, and
behavior are unchanged. Handlers reach shared state through the services
package (module-attribute access for anything tests monkeypatch via app.py's
forwarding properties).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from services import skills, storage

router = APIRouter()

# --- System prompts ---

class PromptIn(BaseModel):
    id: Optional[str] = None
    name: str
    content: str


@router.get("/api/system-prompts")
async def list_prompts() -> dict:
    return storage.load_prompts()


@router.post("/api/system-prompts")
async def upsert_prompt(p: PromptIn) -> dict:
    data = storage.load_prompts()
    pid = p.id or p.name.lower().replace(" ", "-")
    existing = next((x for x in data["prompts"] if x["id"] == pid), None)
    if existing:
        existing["name"] = p.name
        existing["content"] = p.content
    else:
        data["prompts"].append({"id": pid, "name": p.name, "content": p.content})
    storage.save_json(storage.PROMPTS_PATH, data)
    return {"id": pid}


@router.delete("/api/system-prompts/{prompt_id}")
async def delete_prompt(prompt_id: str) -> dict:
    data = storage.load_prompts()
    data["prompts"] = [x for x in data["prompts"] if x["id"] != prompt_id]
    storage.save_json(storage.PROMPTS_PATH, data)
    return {"ok": True}


# --- Skills ---

@router.get("/api/skills")
async def list_skills() -> dict:
    return {"skills": skills.list_skill_files()}


@router.get("/api/skills/{skill_id}")
async def get_skill(skill_id: str) -> dict:
    skill = skills.load_skill_content(skill_id)
    if not skill:
        raise HTTPException(404, "Skill not found")
    return skill


@router.post("/api/skills/upload")
async def upload_skill(file: UploadFile = File(...)) -> dict:
    if not file.filename or not file.filename.endswith(".md"):
        raise HTTPException(400, "Skills must be .md files with frontmatter.")
    safe_name = Path(file.filename).name
    dest = storage.SKILLS_DIR / safe_name
    content = await file.read()
    dest.write_bytes(content)
    return {"id": dest.stem, "filename": safe_name}


class SkillIn(BaseModel):
    id: str
    name: str
    description: str
    content: str


@router.post("/api/skills")
async def create_skill(s: SkillIn) -> dict:
    safe_id = "".join(c for c in s.id if c.isalnum() or c in "-_").strip("-_") or "skill"
    body = f"---\nname: {s.name}\ndescription: {s.description}\n---\n\n{s.content}\n"
    (storage.SKILLS_DIR / f"{safe_id}.md").write_text(body, encoding="utf-8")
    return {"id": safe_id}


@router.delete("/api/skills/{skill_id}")
async def delete_skill(skill_id: str) -> dict:
    path = storage.SKILLS_DIR / f"{skill_id}.md"
    if path.exists():
        path.unlink()
    return {"ok": True}
