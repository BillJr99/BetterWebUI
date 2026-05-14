"""
BetterWebUI — a friendlier OpenWebUI front-end with skills, custom system
prompts, multimodal generation, MCP-style tooling, gated shell execution,
visible task plans, file-tree/diff/checkpoints, plan mode, subagents,
workspace bundles, conversation search/pinning/forking,
per-turn telemetry, onboarding wizard, and accessibility features.
"""

import asyncio
import base64
import hashlib
import io
import ipaddress
import json
import os
import platform
import shutil
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

import aiofiles
import httpx
import yaml
from fastapi import FastAPI, HTTPException, UploadFile, File, Request
from fastapi.responses import FileResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).parent.resolve()
DATA_DIR = ROOT / "data"
SKILLS_DIR = ROOT / "skills"
UPLOADS_DIR = DATA_DIR / "uploads"
CHECKPOINTS_DIR = DATA_DIR / "checkpoints"
TASKS_DIR = DATA_DIR / "tasks"
CONFIG_PATH = DATA_DIR / "config.json"
PROMPTS_PATH = DATA_DIR / "system_prompts.json"
CONVERSATIONS_PATH = DATA_DIR / "conversations.json"
WORKSPACES_PATH = DATA_DIR / "workspaces.json"
MCP_PATH = DATA_DIR / "mcp_servers.json"
CLI_PATH = DATA_DIR / "cli_tools.json"
BRANDING_PATH = DATA_DIR / "branding.json"

# WORKSPACE_DIR is the default directory for shell execution and file I/O.
# Set via the WORKSPACE_DIR environment variable (Docker mounts a host folder
# here). Falls back to a local "workspace/" subfolder when running without Docker.
WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", str(ROOT / "workspace")))

for d in (DATA_DIR, SKILLS_DIR, UPLOADS_DIR, CHECKPOINTS_DIR, TASKS_DIR, WORKSPACE_DIR):
    d.mkdir(parents=True, exist_ok=True)
