"""Static registries: endpoint profiles, MCP/CLI catalogs, onboarding templates.

Extracted verbatim from app.py (Phase 3). Pure data — no I/O, no state.
"""

ENDPOINT_PROFILES: list[dict] = [
    {
        "name": "openwebui",
        "label": "OpenWebUI native",
        "models": "/api/models",
        "chat": "/api/chat/completions",
        "images": "/api/v1/images/generations",
        "audio": "/api/v1/audio/speech",
        "transcribe": "/api/v1/audio/transcriptions",
    },
    {
        "name": "openwebui-openai",
        "label": "OpenWebUI OpenAI proxy",
        "models": "/openai/v1/models",
        "chat": "/openai/v1/chat/completions",
        "images": "/openai/v1/images/generations",
        "audio": "/openai/v1/audio/speech",
        "transcribe": "/openai/v1/audio/transcriptions",
    },
    {
        "name": "openai-v1",
        "label": "OpenAI-compatible (/v1)",
        "models": "/v1/models",
        "chat": "/v1/chat/completions",
        "images": "/v1/images/generations",
        "audio": "/v1/audio/speech",
        "transcribe": "/v1/audio/transcriptions",
    },
    {
        "name": "api-v1",
        "label": "API v1 (/api/v1)",
        "models": "/api/v1/models",
        "chat": "/api/v1/chat/completions",
        "images": "/api/v1/images/generations",
        "audio": "/api/v1/audio/speech",
        "transcribe": "/api/v1/audio/transcriptions",
    },
]

# ---------------------------------------------------------------------------
# MCP server registry
# ---------------------------------------------------------------------------

MCP_REGISTRY: list[dict] = [
    {
        "id": "filesystem",
        "name": "Filesystem",
        "description": "Read and write files within a chosen directory.",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem",
        "command": "npx",
        "args_template": ["-y", "@modelcontextprotocol/server-filesystem", "{root_path}"],
        "fields": [
            {"name": "root_path", "label": "Root directory the assistant may access", "type": "path"}
        ],
        "requires": "Node.js (npm/npx).",
    },
    {
        "id": "github",
        "name": "GitHub",
        "description": "Browse repositories, search code, manage issues and pull requests.",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/github",
        "command": "npx",
        "args_template": ["-y", "@modelcontextprotocol/server-github"],
        "env_template": {"GITHUB_PERSONAL_ACCESS_TOKEN": "{token}"},
        "fields": [
            {"name": "token", "label": "GitHub personal access token", "type": "password"}
        ],
        "requires": "Node.js (npm/npx) and a GitHub PAT.",
    },
    {
        "id": "fetch",
        "name": "Fetch",
        "description": "Retrieve and convert web pages into structured text the assistant can read.",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/fetch",
        "command": "uvx",
        "args_template": ["mcp-server-fetch"],
        "fields": [],
        "requires": "Python with uv installed (https://docs.astral.sh/uv/).",
    },
    {
        "id": "brave-search",
        "name": "Brave Search",
        "description": "Search the web via Brave's API.",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/brave-search",
        "command": "npx",
        "args_template": ["-y", "@modelcontextprotocol/server-brave-search"],
        "env_template": {"BRAVE_API_KEY": "{api_key}"},
        "fields": [
            {"name": "api_key", "label": "Brave API key", "type": "password"}
        ],
        "requires": "Node.js plus a Brave Search API key.",
    },
    {
        "id": "memory",
        "name": "Memory",
        "description": "A persistent knowledge graph the assistant can read from and write to across chats.",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/memory",
        "command": "npx",
        "args_template": ["-y", "@modelcontextprotocol/server-memory"],
        "fields": [],
        "requires": "Node.js (npm/npx).",
    },
    {
        "id": "git",
        "name": "Git",
        "description": "Read and search a local Git repository's history and contents.",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/git",
        "command": "uvx",
        "args_template": ["mcp-server-git", "--repository", "{repo_path}"],
        "fields": [
            {"name": "repo_path", "label": "Path to a Git repository", "type": "path"}
        ],
        "requires": "Python with uv installed.",
    },
    {
        "id": "sequential-thinking",
        "name": "Sequential Thinking",
        "description": "Lets the assistant break problems into stepped thoughts before answering.",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking",
        "command": "npx",
        "args_template": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
        "fields": [],
        "requires": "Node.js (npm/npx).",
    },
    {
        "id": "time",
        "name": "Time",
        "description": "Provides accurate current time and timezone conversion.",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/time",
        "command": "uvx",
        "args_template": ["mcp-server-time"],
        "fields": [],
        "requires": "Python with uv installed.",
    },
    # ---- Cloud services (community-maintained MCP servers) ----
    {
        "id": "gdrive",
        "name": "Google Drive",
        "description": "Browse, search, and read files from Google Drive.",
        "homepage": "https://github.com/modelcontextprotocol/servers-archived/tree/main/src/gdrive",
        "command": "npx",
        "args_template": ["-y", "@modelcontextprotocol/server-gdrive"],
        "env_template": {
            "GDRIVE_CREDENTIALS_PATH": "{credentials_path}",
        },
        "fields": [
            {"name": "credentials_path", "label": "Path to gcp-oauth.keys.json", "type": "path"},
        ],
        "requires": "Node.js plus a Google Cloud OAuth credentials JSON. Run the server once interactively to mint a refresh token.",
        "category": "cloud",
    },
    {
        "id": "google-workspace",
        "name": "Google Workspace",
        "description": "Read Gmail, manage Google Calendar events, and search Drive in one server.",
        "homepage": "https://github.com/taylorwilsdon/google_workspace_mcp",
        "command": "uvx",
        "args_template": ["google-workspace-mcp"],
        "env_template": {
            "GOOGLE_OAUTH_CLIENT_ID": "{client_id}",
            "GOOGLE_OAUTH_CLIENT_SECRET": "{client_secret}",
        },
        "fields": [
            {"name": "client_id", "label": "Google OAuth client ID", "type": "text"},
            {"name": "client_secret", "label": "Google OAuth client secret", "type": "password"},
        ],
        "requires": "Python with uv installed plus a Google Cloud OAuth client. Follow the server's README for the consent-screen setup.",
        "category": "cloud",
    },
    {
        "id": "microsoft-graph",
        "name": "Microsoft 365 (Graph)",
        "description": "Outlook mail, calendar, OneDrive, SharePoint, and Teams via Microsoft Graph.",
        "homepage": "https://github.com/softeria/ms-365-mcp-server",
        "command": "npx",
        "args_template": ["-y", "@softeria/ms-365-mcp-server"],
        "env_template": {
            "MS365_MCP_CLIENT_ID": "{client_id}",
            "MS365_MCP_TENANT_ID": "{tenant_id}",
        },
        "fields": [
            {"name": "client_id", "label": "Azure AD app client ID", "type": "text"},
            {"name": "tenant_id", "label": "Tenant ID (or 'common')", "type": "text"},
        ],
        "requires": "Node.js plus an Azure AD app registration with Microsoft Graph delegated permissions.",
        "category": "cloud",
    },
    {
        "id": "slack",
        "name": "Slack",
        "description": "Read channels, post messages, search history.",
        "homepage": "https://github.com/modelcontextprotocol/servers-archived/tree/main/src/slack",
        "command": "npx",
        "args_template": ["-y", "@modelcontextprotocol/server-slack"],
        "env_template": {
            "SLACK_BOT_TOKEN": "{bot_token}",
            "SLACK_TEAM_ID": "{team_id}",
        },
        "fields": [
            {"name": "bot_token", "label": "Slack bot token (xoxb-...)", "type": "password"},
            {"name": "team_id", "label": "Slack team / workspace ID", "type": "text"},
        ],
        "requires": "Node.js plus a Slack app installed in your workspace with the required scopes.",
        "category": "cloud",
    },
    {
        "id": "notion",
        "name": "Notion",
        "description": "Search, read, and update Notion pages and databases.",
        "homepage": "https://github.com/makenotion/notion-mcp-server",
        "command": "npx",
        "args_template": ["-y", "@notionhq/notion-mcp-server"],
        "env_template": {
            "OPENAPI_MCP_HEADERS": "{headers_json}",
        },
        "fields": [
            {"name": "headers_json", "label": "Headers JSON (e.g. {\"Authorization\":\"Bearer ntn_...\",\"Notion-Version\":\"2022-06-28\"})", "type": "password"},
        ],
        "requires": "Node.js plus a Notion integration token with workspace access.",
        "category": "cloud",
    },
    {
        "id": "linear",
        "name": "Linear",
        "description": "Browse and update Linear issues, projects, and cycles.",
        "homepage": "https://github.com/jerhadf/linear-mcp-server",
        "command": "npx",
        "args_template": ["-y", "linear-mcp-server"],
        "env_template": {
            "LINEAR_API_KEY": "{api_key}",
        },
        "fields": [
            {"name": "api_key", "label": "Linear personal API key", "type": "password"},
        ],
        "requires": "Node.js plus a Linear API key from Settings → API.",
        "category": "cloud",
    },
    {
        "id": "asana",
        "name": "Asana",
        "description": "Read and update Asana tasks, projects, and workspaces.",
        "homepage": "https://github.com/cristip73/mcp-server-asana",
        "command": "npx",
        "args_template": ["-y", "@cristip73/mcp-server-asana"],
        "env_template": {
            "ASANA_ACCESS_TOKEN": "{access_token}",
        },
        "fields": [
            {"name": "access_token", "label": "Asana personal access token", "type": "password"},
        ],
        "requires": "Node.js plus an Asana personal access token from My Settings → Apps.",
        "category": "cloud",
    },
    {
        "id": "jira",
        "name": "Jira",
        "description": "Search, read, and update Jira issues.",
        "homepage": "https://github.com/sooperset/mcp-atlassian",
        "command": "uvx",
        "args_template": ["mcp-atlassian"],
        "env_template": {
            "JIRA_URL": "{jira_url}",
            "JIRA_USERNAME": "{username}",
            "JIRA_API_TOKEN": "{api_token}",
        },
        "fields": [
            {"name": "jira_url", "label": "Jira base URL (e.g. https://acme.atlassian.net)", "type": "text"},
            {"name": "username", "label": "Atlassian account email", "type": "text"},
            {"name": "api_token", "label": "Atlassian API token", "type": "password"},
        ],
        "requires": "Python with uv installed plus an Atlassian API token.",
        "category": "cloud",
    },
]

# ---------------------------------------------------------------------------
# CLI shortcuts registry
# ---------------------------------------------------------------------------

CLI_REGISTRY: list[dict] = [
    {
        "id": "git",
        "name": "git",
        "description": "Version control. Show status, diff, log, and history.",
        "command_template": "git {args}",
        "examples": ["git status", "git log --oneline -20", "git diff"],
    },
    {
        "id": "gh",
        "name": "GitHub CLI",
        "description": "Operate on GitHub repos, PRs, and issues from the terminal.",
        "command_template": "gh {args}",
        "examples": ["gh pr list", "gh issue view 123"],
    },
    {
        "id": "pandoc",
        "name": "pandoc",
        "description": "Convert documents between formats — markdown, docx, pdf, html, latex.",
        "command_template": "pandoc {args}",
        "examples": ["pandoc input.md -o output.docx", "pandoc paper.docx -o paper.pdf"],
    },
    {
        "id": "ffmpeg",
        "name": "ffmpeg",
        "description": "Convert and process audio/video files.",
        "command_template": "ffmpeg {args}",
        "examples": ["ffmpeg -i talk.mov -vn talk.mp3"],
    },
    {
        "id": "yt-dlp",
        "name": "yt-dlp",
        "description": "Download videos and audio from sites like YouTube, Vimeo, etc.",
        "command_template": "yt-dlp {args}",
        "examples": ["yt-dlp -x --audio-format mp3 'URL'"],
    },
    {
        "id": "sqlite3",
        "name": "sqlite3",
        "description": "Inspect and query SQLite databases.",
        "command_template": "sqlite3 {args}",
        "examples": ["sqlite3 grades.db '.tables'"],
    },
    {
        "id": "rg",
        "name": "ripgrep",
        "description": "Fast recursive search through text files.",
        "command_template": "rg {args}",
        "examples": ["rg 'TODO' src/"],
    },
    {
        "id": "curl",
        "name": "curl",
        "description": "Fetch URLs from the web.",
        "command_template": "curl {args}",
        "examples": ["curl -fsSL https://example.com"],
    },
]
# Onboarding workspace templates (use-case presets)
ONBOARDING_TEMPLATES: list[dict] = [
    {
        "id": "grading",
        "name": "Grading",
        "description": "Grade and give feedback on student work.",
        "system_prompt": (
            "You are a grading assistant for a higher-ed instructor. "
            "Be constructive, specific, and aligned with the rubric provided. "
            "Use load_skill to load the grading-rubric skill when grading."
        ),
        "skills": ["grading-rubric"],
        "cli": [],
        "mcp": [],
    },
    {
        "id": "research",
        "name": "Research",
        "description": "Find sources, summarize papers, manage citations.",
        "system_prompt": (
            "You are a research assistant for an academic. Help find, summarize, "
            "and cite sources. Use load_skill for research-citations."
        ),
        "skills": ["research-citations"],
        "cli": [],
        "mcp": ["fetch", "brave-search"],
    },
    {
        "id": "course-prep",
        "name": "Course Prep",
        "description": "Write syllabi, slides, lecture notes, and assignments.",
        "system_prompt": (
            "You are a course-preparation assistant. Help draft syllabi, "
            "lesson plans, slides, and assignments."
        ),
        "skills": [],
        "cli": ["pandoc"],
        "mcp": [],
    },
    {
        "id": "writing",
        "name": "Writing",
        "description": "Draft, edit, and polish academic or professional writing.",
        "system_prompt": (
            "You are a writing coach and editor. Help draft, revise, "
            "and polish documents."
        ),
        "skills": [],
        "cli": ["pandoc"],
        "mcp": [],
    },
    {
        "id": "coding",
        "name": "Coding",
        "description": "Write, debug, and explain code.",
        "system_prompt": (
            "You are a coding assistant. Help write, debug, and explain code. "
            "Use the computer-helper skill for running commands on the user's machine."
        ),
        "skills": ["computer-helper"],
        "cli": ["git", "rg"],
        "mcp": ["filesystem", "git"],
    },
]
