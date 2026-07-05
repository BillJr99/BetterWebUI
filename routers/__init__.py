"""FastAPI routers for BetterWebUI, one module per domain (Phase 3).

app.py (the composition root) includes each router in ALL_ROUTERS order.
No paths overlap across routers, so the order only mirrors the original
app.py declaration order for readability.
"""
from . import (
    admin,
    chat,
    conversations,
    mcp_cli,
    oauth_routes,
    onboarding,
    project,
    scheduled,
    session_trust,
    skills_prompts,
    uploads,
    workspaces,
)

ALL_ROUTERS = [
    admin.router,
    skills_prompts.router,
    session_trust.router,
    workspaces.router,
    onboarding.router,
    project.router,
    mcp_cli.router,
    uploads.router,
    scheduled.router,
    oauth_routes.router,
    conversations.router,
    chat.router,
]
