"""Skill-file loading and configuration lints (Phase 3 extraction from app.py).

SKILLS_DIR is read via ``storage.SKILLS_DIR`` attribute access so test
fixtures that rebind the path (through app.py's forwarding properties) are
honored at call time.
"""
from __future__ import annotations

import shutil
from typing import Optional

from . import storage
from .storage import _load_frontmatter, load_cli_tools, load_mcp_servers


def list_skill_files() -> list[dict]:
    skills = []
    for path in sorted(storage.SKILLS_DIR.glob("*.md")):
        try:
            post = _load_frontmatter(path)
            skills.append({
                "id": path.stem,
                "name": post.get("name", path.stem),
                "description": post.get("description", ""),
                "filename": path.name,
            })
        except Exception as exc:
            skills.append({
                "id": path.stem,
                "name": path.stem,
                "description": f"(could not parse: {exc})",
                "filename": path.name,
            })
    return skills


def load_skill_content(skill_id: str) -> Optional[dict]:
    path = storage.SKILLS_DIR / f"{skill_id}.md"
    if not path.exists():
        return None
    post = _load_frontmatter(path)
    return {
        "id": skill_id,
        "name": post.get("name", skill_id),
        "description": post.get("description", ""),
        "content": post.content,
    }


def _lint_skills() -> list[dict]:
    issues = []
    for path in sorted(storage.SKILLS_DIR.glob("*.md")):
        try:
            post = _load_frontmatter(path)
            if not post.get("name"):
                issues.append({"type": "skill", "id": path.stem, "issue": "Missing 'name' in frontmatter"})
            if not post.get("description"):
                issues.append({"type": "skill", "id": path.stem, "issue": "Missing 'description' in frontmatter"})
        except Exception as exc:
            issues.append({"type": "skill", "id": path.stem, "issue": f"Parse error: {exc}"})
    return issues


def _lint_mcp() -> list[dict]:
    issues = []
    # Suggest what a missing binary usually means so the user knows how to fix it
    hint_by_bin = {
        "npx": "install Node.js (provides npx)",
        "node": "install Node.js",
        "uvx": "install uv (provides uvx)",
        "uv": "install uv",
    }
    for s in load_mcp_servers().get("servers", []):
        if not s.get("command"):
            issues.append({"type": "mcp", "id": s.get("name", "?"), "issue": "Missing 'command'"})
            continue
        cmd = s.get("command", "")
        # Validate the exact configured binary, not an alternative
        if cmd and not shutil.which(cmd):
            hint = hint_by_bin.get(cmd)
            msg = f"'{cmd}' not found on PATH"
            if hint:
                msg += f" — {hint}"
            issues.append({"type": "mcp", "id": s["name"], "issue": msg})
    return issues


def _lint_cli() -> list[dict]:
    issues = []
    for c in load_cli_tools().get("tools", []):
        if "{args}" not in c.get("command_template", ""):
            issues.append({"type": "cli", "id": c.get("id", "?"), "issue": "command_template does not contain {args}"})
    return issues
