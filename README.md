# BetterWebUI

A friendlier front-end for OpenWebUI. Built for higher-ed faculty who want
the power of agentic AI — running commands, reading files, generating images
and audio, calling MCP servers — without having to be a developer.

## What it does

- Connects to your existing OpenWebUI instance (auto-detects whether the API
  lives at `/api`, `/v1`, `/openai/v1`, etc.)
- Lets you pick from any model your OpenWebUI knows about
- **Workspaces** — bundle a system prompt, chosen skills, MCP servers, CLI
  shortcuts, and persistent files into a saved configuration you can return
  to. "Grading", "Research", "Course prep" — switch with one click.
- **Skills** — short markdown briefs telling the assistant *how* to do
  specific tasks. Loaded on demand when a request matches.
- **System prompts** — the assistant's role and tone.
- **MCP servers** — extend the assistant with tools from a curated registry
  (Filesystem, GitHub, Fetch, Brave Search, Memory, Git, …) or your own
  custom servers.
- **CLI shortcuts** — registered command-line tools (git, gh, pandoc,
  ffmpeg, …) the assistant knows are available.
- **Math + markdown rendering** — prose, tables, code, and LaTeX (`$...$`,
  `$$...$$`, `\(...\)`, `\[...\]`) all render properly via KaTeX.
- **Multimodal in** — attach images and files to your messages.
- **Multimodal out** — generated images and audio download to your computer
  automatically; nothing is left lying on the server.
- **Local file picker** — when the assistant wants to read a file, you get a
  file picker. The assistant only sees what you choose to share.
- **Local shell execution** — bash on macOS/Linux, PowerShell on Windows.
  Every command requires a one-click approval before it runs.

## Service Integrations

BetterWebUI integrates with three external AI services via REST APIs, exposing them
at `/api/services/*` endpoints that the LLM can call through tool use or slash commands.

| Service | Env var | Default URL | Purpose |
|---|---|---|---|
| CognitiveLoopKernel (CLK) | `CLK_BASE_URL` | `http://localhost:8001` | Deep research loops & multi-step workflows |
| AutoGUI | `AUTOGUI_BASE_URL` | `http://localhost:8002` | Desktop GUI automation via ReAct |
| OSScreenObserver (OSSO) | `OSSO_BASE_URL` | `http://localhost:5001` | Screen reading & accessibility inspection |

### Enable / disable services

Each service can be toggled on or off independently from **Settings → Services**
(or via the API). Disabled services immediately return an HTTP 503 for all
their routes, and the LLM is told the service is unavailable. Re-enabling
restores normal operation without a restart.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/services/status` | Current enabled/disabled state for all services |
| POST | `/api/services/{name}/enable` | Enable a service (`clk`, `autogui`, `osso`) |
| POST | `/api/services/{name}/disable` | Disable a service |

### Graceful degradation

When an enabled service is **not running or unreachable**, BetterWebUI returns
a descriptive HTTP 503 response rather than crashing. The LLM receives the
error message and relays it to the user.

### Approval flow

Tool calls that trigger side-effects require a **one-click approval** from the
user in the chat interface before the action executes:

- `clk_research` — shows the workflow and command for approval
- `autogui_task` — shows the task description for approval
- `screen_action` — shows the action type and coordinates for approval

Read-only operations (`screen_windows`, `screen_description`,
`screen_screenshot`) run without an approval prompt.

### Integrated endpoints

| Method | Path | Service |
|---|---|---|
| GET | `/api/services/health` | All (aggregated health check) |
| GET | `/api/services/status` | All (enable/disable state) |
| POST | `/api/services/{name}/enable` | All |
| POST | `/api/services/{name}/disable` | All |
| GET | `/api/services/clk/workflows` | CLK |
| POST | `/api/services/clk/research` | CLK |
| GET | `/api/services/clk/research/{id}` | CLK |
| GET | `/api/services/clk/research/{id}/stream` | CLK (SSE) |
| GET | `/api/services/clk/research/{id}/artifacts` | CLK |
| POST | `/api/services/clk/research/{id}/cancel` | CLK |
| POST | `/api/services/autogui/task` | AutoGUI |
| GET | `/api/services/autogui/task/{id}` | AutoGUI |
| GET | `/api/services/autogui/task/{id}/stream` | AutoGUI (SSE) |
| POST | `/api/services/autogui/task/{id}/cancel` | AutoGUI |
| GET | `/api/services/autogui/tools` | AutoGUI |
| GET | `/api/services/osso/windows` | OSSO |
| GET | `/api/services/osso/description` | OSSO |
| GET | `/api/services/osso/structure` | OSSO |
| GET | `/api/services/osso/screenshot` | OSSO |
| POST | `/api/services/osso/action` | OSSO |
| GET | `/api/services/osso/capabilities` | OSSO |
| GET | `/api/services/tools` | All (LLM tool specs) |

### Slash commands

When typing in the chat, prefix your message with a slash command to route directly
to a service:

- `/research <topic>` — starts a CLK research workflow
- `/observe` — returns a description of the current screen via OSSO
- `/automate <task>` — sends a GUI automation task to AutoGUI (dry-run by default)

### Deployment

See [deploy/README.md](deploy/README.md) for the full integration deployment guide,
including Docker Compose configuration and the `bootstrap.sh` script for cloning
sibling repositories.

## First-time setup

You need an **OpenWebUI instance you can reach** and its **API key**
(OpenWebUI: Settings → Account → API Keys).

Choose whichever installation method suits you:

---

### Option A — Docker (recommended, no Python needed)

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and start it.
2. Open a terminal, navigate to the folder you cloned/downloaded, and run:

```bash
docker compose up
```

That's it. Docker builds and starts the app. Open <http://localhost:8765> in your browser.

To stop it: press `Ctrl-C` in the terminal. To start again later: `docker compose up`.

> **Your data** (conversations, workspaces, skills) is saved in the `data/` folder next to
> the app, not inside Docker. You can back it up, share it, or delete it freely.

---

### Option B — Python (macOS / Linux)

You need **Python 3.10+** ([python.org](https://www.python.org/downloads/) if you don't have it).

```bash
./start.sh
```

The first launch creates a `.venv` folder and installs packages. Later launches just start.

### Option C — Python (Windows)

You need **Python 3.10+** ([python.org](https://www.python.org/downloads/) if you don't have it).

Double-click `start.bat`, or in a terminal:

```cmd
start.bat
```

---

When the server is running, open <http://127.0.0.1:8765> in your browser.

### Configure on first run

1. Click **Settings** in the sidebar.
2. Paste your OpenWebUI URL (just the root, e.g. `http://localhost:3000`)
   and your API key. Click **Save & test** — the URL is auto-detected and
   the model dropdown populates.
3. Pick a default chat model. Click **Save defaults**.
4. If you have CLK, AutoGUI, or OSScreenObserver running, scroll to
   **Settings → Services** to enable/disable each one. (All three are enabled
   by default; they degrade gracefully if not reachable.)
5. Start a new chat (or use the onboarding wizard if prompted).

Optional, only if you want to use MCP servers:

- **Node.js** (for `npx`-based servers like Filesystem, GitHub, Memory)
- **uv** (for `uvx`-based servers like Fetch, Git, Time)

## Where things run

**BetterWebUI runs locally on your computer.** When you click `start.sh`
or `start.bat`, the server starts on your machine. That means:

- Shell commands the assistant runs → execute on **your** computer
- Files you pick → stay on **your** computer
- Files the assistant generates → download to **your** Downloads folder
- The OpenWebUI server (a separate thing) is the only remote piece, and
  it only ever sees the messages and base64'd attachments you send

If you want to host BetterWebUI on a remote server and have shell
commands still execute locally, that's a different architecture (a local
bridge agent). It's not built in yet — let us know if you need it.

## Workspaces

A workspace is a saved bundle of:
- A system prompt
- A subset of your skills
- A subset of your MCP servers
- A subset of your CLI shortcuts
- Persistent files (attached to every new chat in that workspace)
- A default model (optional)

Open the **Workspaces** tab → **+ New workspace** to create one.
Examples:

- **Grading**: prompt = "You are a grading assistant…", skills =
  `grading-rubric`, files = `[syllabus.pdf, rubric.docx]`.
- **Research**: prompt = "You are a research assistant…", skills =
  `research-citations`, MCP = `fetch`, `brave-search`.
- **Course prep**: prompt = "Help me prepare lecture materials…",
  CLI shortcuts = `pandoc`, files = `[course-notes.md]`.

Switch the active workspace from the dropdown at the top of the chat.

## Skills

Skills are markdown files in the `skills/` folder. Three are included as
examples (rubric helper, citation helper, computer helper). You can:

- Click **Skills** in the sidebar → **New skill** to write one in the UI
- Or drop a `.md` file into the `skills/` folder directly

Each skill is a frontmatter header plus a body:

```markdown
---
name: My Skill
description: When the assistant should load this skill
---

When this skill is loaded, do these things…
```

The assistant sees a list of available skills and their descriptions. When
a user request matches one, the assistant calls `load_skill` to read the
full instructions and follow them.

## MCP servers

Click **Tools** → **+ Add from registry** to install one of:

- **Filesystem** — read/write files in a chosen directory (needs Node.js)
- **GitHub** — repos, issues, PRs (needs Node.js + a GitHub PAT)
- **Fetch** — retrieve and parse web pages (needs Python + uv)
- **Brave Search** — web search (needs Node.js + a Brave API key)
- **Memory** — a persistent knowledge graph (needs Node.js)
- **Git** — read a local Git repo's history (needs Python + uv)
- **Sequential Thinking** — stepped reasoning (needs Node.js)
- **Time** — accurate time + timezone conversion (needs Python + uv)

Or **+ Custom** to register a server you've written or found elsewhere.

If a server fails to start (most often: missing `npx` or `uvx`), the UI
shows the error in the server's row — fix the prerequisite, then click
the row to reconcile.

## CLI shortcuts

Pre-registered command templates the assistant can invoke through
`cli_call`. Each invocation goes through the same approval dialog as a
raw shell command. The curated registry includes `git`, `gh`, `pandoc`,
`ffmpeg`, `yt-dlp`, `sqlite3`, `ripgrep`, `curl`. Add your own with
**+ Custom** — use `{args}` in the template as the placeholder for
arguments the assistant fills in.

## Math + markdown

The assistant's responses render as proper markdown — headings, lists,
tables, code blocks, links. Mathematics renders via KaTeX. The assistant
is told it can use:

- `$inline$` and `$$display$$`
- `\(inline\)` and `\[display\]`

Try asking it to derive something or explain a formula and the equations
will typeset nicely.

## Safety

Every action that touches your computer is gated:

- **Shell commands** show a dialog with the exact command and the
  assistant's stated reason. You approve or deny each one.
- **File saves** show the filename and a preview before downloading.
- **File reads** open a file picker — you choose what the assistant sees.
- File generation (image/audio), skill loading, and MCP tool calls run
  without prompting (they don't change anything destructive).
- Shell execution can be turned off entirely in Settings.

## Where things live

```
betterwebui/
├── app.py              # backend (FastAPI)
├── static/             # frontend (HTML/CSS/JS, no build step)
├── skills/             # your skills, as .md files
├── services/           # integration clients (CLK, AutoGUI, OSSO)
├── data/
│   ├── config.json         # your settings (API key lives here)
│   ├── system_prompts.json
│   ├── conversations.json
│   ├── workspaces.json
│   ├── mcp_servers.json
│   ├── cli_tools.json
│   └── uploads/            # files you attached
└── start.sh / start.bat
```

The `data/` folder is yours — back it up if you've written prompts,
workspaces, or conversations you care about.

Generated images/audio are NOT stored on the server — they stream
directly to your browser, which downloads them and displays them inline
using a temporary blob URL.

## Troubleshooting

- **"Cannot reach OpenWebUI"** — check the URL and that OpenWebUI is
  actually running. Try opening it in another browser tab first.
- **"No working API endpoint detected"** — the URL probably points at a
  web page rather than the API. Try just the host root.
- **Image generation fails** — your OpenWebUI instance needs an image
  backend configured (Image Generation in OpenWebUI's admin settings).
- **Audio generation fails** — OpenWebUI needs TTS configured (Audio
  settings in admin).
- **MCP server won't start** — usually `npx` or `uvx` is missing. Install
  Node.js (https://nodejs.org/) or uv (https://docs.astral.sh/uv/), then
  reconcile from the Tools tab.
- **Math doesn't render** — check the browser console for KaTeX errors;
  CDN may be blocked by a firewall.

## License

MIT license; Use freely within your institution.
