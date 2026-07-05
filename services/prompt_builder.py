"""Tool protocol blocks, system-prompt assembly, and tool-call parsing
(Phase 3 extraction from app.py).
"""
from __future__ import annotations

import json
import platform
from typing import Optional

from . import llm
from . import state as svc_state
from .mcp import mcp_manager
from .skills import list_skill_files
from .storage import load_cli_tools, load_workspaces

# ---------------------------------------------------------------------------
# Tool definitions and system-prompt builders
# ---------------------------------------------------------------------------

TOOL_PROTOCOL = """
You have tools. To call a tool, output exactly one fenced JSON block on its
own lines like this:

```tool
{"tool": "TOOL_NAME", "args": {...}}
```

After the tool runs, the result is added to the conversation and you continue.
Output at most one tool call per assistant turn. Speak naturally to the user
before and after tool calls. Never invent tool output — wait for the result.

Available tools:

- update_task_plan: update the visible task plan the user sees in the right-hand
  panel. Call this when starting ANY multi-step task (to lay out the steps) and
  after each step (to tick items done, mark in_progress, or flag blocked).
  ALWAYS start a complex task by calling this first.
  Args: {"items": [{"id": "step-1", "title": "Step description",
  "status": "pending|in_progress|done|blocked", "note": "optional detail"}]}.

- spawn_subagent: run up to 3 read-only parallel sub-tasks to research,
  compare, or explore. Useful for "compare these rubrics", "research these
  topics", "check these files". The main conversation pauses until all
  subagents finish, then you get a combined summary.
  Args: {"kind": "explore|compare", "prompt": "what to investigate",
  "items": ["item1", "item2"] (optional for compare)}.

- execute_shell: run a shell command on the user's computer. The host OS is
  detected automatically (bash on Linux/macOS, PowerShell on Windows). USER
  APPROVAL IS REQUIRED before the command runs — if denied, you'll see an
  error and should ask the user what they'd prefer. Args: {"command": "...",
  "reason": "short explanation of why this command is needed"}.
  GRAPH / PLOT CONVENTION: if you run a Python script that generates a plot,
  save it to /tmp/bwui_plot.png (e.g. plt.savefig('/tmp/bwui_plot.png',
  bbox_inches='tight')) instead of plt.show(). The image is then
  automatically captured and displayed inline in the chat.

- read_file: read file(s) chosen by the user. The user is shown a file
  picker — you do NOT specify a path. The result is the chosen file(s)' name,
  type, and content. Args: {"reason": "why you need to read", "accept": "*",
  "multiple": false}. Use accept="image/*" or "text/*,.md,.csv" to filter.

- write_file: write a file to the workspace project folder (REQUIRES
  APPROVAL unless mode is "trusted"). The file lands at
  <project_root>/<filename> — falling back to the server's WORKSPACE_DIR
  when the workspace has no project_root configured. Any pre-existing
  file at that path is snapshotted into checkpoints before being
  overwritten, so the user can revert from the UI. On success the file
  is visible in the Files pane; data_b64 is only returned when the
  on-disk write fails so the user can still retrieve the content.
  Args: {"filename": "name.ext", "content":
  "...", "mime": "text/plain"}.

- delete_file: permanently delete a file from the workspace project folder
  (REQUIRES APPROVAL — the user must confirm before the file is removed).
  Args: {"filename": "name.ext", "reason": "why this file should be deleted"}.

- load_skill: load the full content of a named skill so you can follow its
  instructions. Args: {"skill_id": "..."}. Use this when a listed skill
  matches the user's request.

- generate_image: create an image via OpenWebUI's image endpoint. The image
  is sent to the user and downloaded to their computer automatically. Args:
  {"prompt": "description", "size": "1024x1024"}.

- generate_audio: text-to-speech via OpenWebUI. The audio is sent to the
  user and downloaded automatically. Args: {"text": "...", "voice": "alloy"}.

- mcp_call: call a tool from a connected MCP server (only available if
  servers are configured and running). Args: {"server": "server_name",
  "name": "tool_name", "arguments": {...}}.

- cli_call: run one of the user's pre-registered CLI shortcuts. Routes
  through execute_shell with approval (unless the shortcut has always-allow
  policy). Args: {"id": "shortcut_id", "args": "command-line arguments"}.

- web_search: search the public web. Use only when the user has enabled
  web search for this turn (the system prompt will say so). Args:
  {"query": "...", "max_results": 5}. Returns a list of
  {title, url, snippet} items.

- fetch_url: download and extract the readable text content of a web page.
  Useful after web_search to read the full article. Requires user approval
  unless chat mode is trusted. Args: {"url": "https://..."}.
  Returns {url, title, text, word_count} or {error: "..."}.
""".strip()

PLAN_MODE_BLOCK = """
⚠️ PLAN MODE IS ACTIVE.

You may ONLY call: update_task_plan, read_file, load_skill.
Do NOT call any side-effecting tool: execute_shell, write_file,
generate_image, generate_audio, cli_call, mcp_call.

Your job in plan mode is to:
1. Use update_task_plan to lay out a complete step-by-step plan.
2. Explain your approach clearly in plain English.
3. Tell the user to switch to "Approve-each" mode (the chip in the chat header)
   when they are ready to execute.

Do NOT execute anything — plan only.
""".strip()

RENDERING_PROTOCOL = r"""
The user sees your replies rendered as Markdown with LaTeX/KaTeX for math.
This is not a hint — it's how the UI works. Plain-text "math" like
"x^2 + (b/a)x = -c/a" displays literally and looks wrong. Always wrap
mathematics in LaTeX delimiters and use real LaTeX commands.

Markdown:
  Headings:    ##, ###  (use ### for sub-sections inside replies)
  Emphasis:    **bold**, *italic*
  Code:        `inline`, ```python\nfenced\n```
  Lists:       - bullet      OR      1. numbered
  Quotes:      > a quote
  Links:       [text](https://example.com)
  Tables:      | header | header |
               |--------|--------|
               | a      | b      |

Math — REQUIRED for any equation, expression, fraction, exponent, root,
sum, integral, matrix, or set-builder. Choose:
  Inline:   $...$            $\\(...\\)$
  Display:  $$...$$           \\[...\\]

Use real LaTeX commands. WRONG vs RIGHT:

  WRONG:   x^2 + (b/a)x = -c/a
  RIGHT:   $x^{2} + \frac{b}{a}\,x = -\frac{c}{a}$

  WRONG:   sqrt(b^2 - 4ac)
  RIGHT:   $\sqrt{b^{2} - 4ac}$

  WRONG:   (-b +/- sqrt(b^2 - 4ac)) / (2a)
  RIGHT:   $$x = \frac{-b \pm \sqrt{b^{2} - 4ac}}{2a}$$

  WRONG:   sum from i=1 to n of i^2
  RIGHT:   $\sum_{i=1}^{n} i^{2}$

  WRONG:   integral from 0 to 1 of x^2 dx
  RIGHT:   $\int_{0}^{1} x^{2}\,dx$

Common LaTeX you should know:
  Fractions       \frac{num}{den}        Roots     \sqrt{x}, \sqrt[n]{x}
  Exponents       x^{n}    (always brace multi-character exponents)
  Subscripts      x_{i}
  Operators       \pm  \mp  \cdot  \times  \div  \ast
  Relations       \leq  \geq  \neq  \approx  \equiv  \rightarrow  \Leftrightarrow
  Greek           \alpha \beta \gamma \delta \epsilon \pi \sigma \theta \phi \omega
  Sets            \mathbb{R} \mathbb{N} \mathbb{Z} \emptyset \in \notin \subset
  Logic           \forall \exists \neg \land \lor \Rightarrow
  Calculus        \int  \sum  \prod  \lim  \partial  \nabla  \infty
  Spacing         \,  (thin space)   \;  (thick)   \quad  \qquad

Aligned multi-line equations:
$$
\begin{aligned}
ax^{2} + bx + c &= 0 \\
x &= \frac{-b \pm \sqrt{b^{2} - 4ac}}{2a}
\end{aligned}
$$

Matrices:
$$
A = \begin{pmatrix} 1 & 2 \\ 3 & 4 \end{pmatrix}
$$

When in doubt: lean toward wrapping it in $...$. Never use ASCII math
shortcuts (^, /, sqrt(), <=, !=, sum, int) in user-visible prose.
""".strip()


def resolve_active_workspace(config: dict) -> Optional[dict]:
    wid = config.get("active_workspace_id")
    if not wid:
        return None
    data = load_workspaces()
    return next((w for w in data["workspaces"] if w["id"] == wid), None)


def build_system_prompt(
    config: dict,
    prompts: dict,
    mode: str = "approve-each",
    *,
    user_memories: Optional[list[str]] = None,
    use_vision: bool = False,
    web_search_mode: str = "off",
) -> str:
    parts: list[str] = []
    workspace = resolve_active_workspace(config)

    # 1. The system prompt itself
    prompt_id = (workspace or {}).get("system_prompt_id") or config.get("active_prompt_id") or "default"
    chosen = next(
        (p for p in prompts["prompts"] if p["id"] == prompt_id),
        prompts["prompts"][0] if prompts["prompts"] else None,
    )
    if chosen:
        parts.append(chosen["content"])

    if workspace:
        parts.append(
            f"Active workspace: {workspace['name']}."
            + (f" {workspace['description']}" if workspace.get("description") else "")
        )

    # User memories — durable preferences/facts/constraints stored client-side
    # in the browser and injected here on every turn. Subject to context trim.
    if user_memories:
        cleaned = [m.strip() for m in user_memories if isinstance(m, str) and m.strip()]
        if cleaned:
            parts.append(
                "Things to remember about the user:\n"
                + "\n".join(f"- {m}" for m in cleaned[:50])  # hard cap so a runaway list can't blow the budget
            )

    # Per-turn capability hints
    if use_vision:
        parts.append(
            "The user has explicitly asked you to USE VISION on this turn. "
            "If any images are attached, analyse them in detail and incorporate "
            "what you see into your reply."
        )
    if web_search_mode == "required":
        parts.append(
            "The user requires web search on this turn. You MUST call the "
            "web_search tool before answering so your reply reflects current "
            "information."
        )
    elif web_search_mode == "if_needed":
        parts.append(
            "If answering accurately requires current or specialised information "
            "you don't have, call the web_search tool first."
        )

    # Plan mode block (injected before other tools if active)
    effective_mode = mode or (workspace or {}).get("mode") or config.get("chat_mode", "approve-each")
    if effective_mode == "plan":
        parts.append(PLAN_MODE_BLOCK)

    # Response style
    parts.append(
        "Always attempt a complete, useful response to the user's request before "
        "asking clarifying questions. If something is ambiguous, make a reasonable "
        "assumption, state it briefly, and proceed. Save any follow-up questions for "
        "the end of your reply, after the substantive response."
    )

    # Rendering rules
    parts.append(RENDERING_PROTOCOL)

    # In test mode skip the tool-protocol block and all tool / service listings.
    # The basic prompt + memories above are sufficient for outcome assertions;
    # omitting ~1 k tokens of tool instructions cuts inference time by ~40 %.
    if llm._TEST_MODE:
        return "\n\n".join(parts)

    # 2. Available skills
    if workspace:
        active_skill_ids = workspace.get("active_skills") or []
    else:
        active_skill_ids = config.get("active_skills") or []
    available_skills = list_skill_files()
    if available_skills:
        listing = "\n".join(
            f"- {s['id']}: {s['description']}"
            for s in available_skills
            if not active_skill_ids or s["id"] in active_skill_ids
        )
        if listing:
            parts.append("Skills you may invoke via load_skill (id: when to use):\n" + listing)

    # 3. MCP tools
    allowed_servers = (workspace or {}).get("active_mcp_servers")
    mcp_tools = mcp_manager.list_all_tools(allowed_servers=allowed_servers)
    if mcp_tools:
        listing = "\n".join(
            f"- {t['server']}.{t['name']}: {t['description']}" for t in mcp_tools
        )
        parts.append("MCP tools available via mcp_call (server.name: description):\n" + listing)

    # 4. CLI shortcuts
    cli_data = load_cli_tools()
    cli_ids = (workspace or {}).get("active_cli_tools")
    cli_listing = []
    for c in cli_data.get("tools", []):
        if cli_ids is not None and c["id"] not in cli_ids:
            continue
        policy = c.get("approval_policy", "ask")
        policy_note = " [always-allowed]" if policy == "always" else ""
        cli_listing.append(
            f"- {c['id']} ({c.get('name', c['id'])}): {c.get('description', '')} "
            f"[template: {c.get('command_template', '')}]{policy_note}"
        )
    if cli_listing:
        parts.append(
            "CLI shortcuts available via cli_call (id: description [template]):\n"
            + "\n".join(cli_listing)
        )

    parts.append(f"Detected operating system: {platform.system()} ({platform.platform()}).")
    parts.append(TOOL_PROTOCOL)

    # 5. Integrated services — only advertised when enabled
    service_lines: list[str] = []
    if svc_state.is_enabled("autogui"):
        service_lines.append(
            "- autogui_task: drive the desktop GUI (move the mouse, click, type, "
            "open apps, work in any window — including Notepad on Windows or any "
            "other native app) via AutoGUI's ReAct loop. PREFER this over "
            "execute_shell when the user asks to operate a GUI application. "
            "Args: {\"task\": \"natural-language description of what to do\", "
            "\"dry_run\": false}."
        )
    if svc_state.is_enabled("osso"):
        service_lines.append(
            "- screen_windows: list every open window on the user's desktop. "
            "Args: {}."
        )
        service_lines.append(
            "- screen_description: describe a window's contents via accessibility "
            "tree or vision. Args: {\"window_index\": 0, \"mode\": "
            "\"accessibility|vision\"}."
        )
        service_lines.append(
            "- screen_screenshot: capture a screenshot of a window. "
            "Args: {\"window_index\": 0}."
        )
        service_lines.append(
            "- screen_action: perform a precise screen action (click, type, key "
            "press). REQUIRES APPROVAL. Args: {\"action\": \"click|type|key\", "
            "\"x\": 0, \"y\": 0, \"text\": \"text-to-type-or-key-name\"}."
        )
    if svc_state.is_enabled("clk"):
        service_lines.append(
            "- clk_research: start a CognitiveLoopKernel research / reasoning "
            "workflow for deep multi-step analysis. REQUIRES APPROVAL. "
            "Args: {\"command\": \"run\", \"workflow\": \"optional workflow name\", "
            "\"args\": [], \"workspace_id\": \"optional\"}."
        )
    if service_lines and effective_mode != "plan":
        parts.append(
            "Integrated services available as tools (call them like any other "
            "tool via the ```tool block). These extend what you can do beyond "
            "execute_shell:\n" + "\n".join(service_lines)
        )

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Tool-call parsing
# ---------------------------------------------------------------------------

def extract_tool_call(text: str) -> Optional[dict]:
    marker = "```tool"
    start = text.find(marker)
    if start == -1:
        return None
    body_start = text.find("\n", start) + 1
    end = text.find("```", body_start)
    if end == -1:
        return None
    body = text[body_start:end].strip()
    try:
        call = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(call, dict) or "tool" not in call:
        return None
    raw_args = call.get("args", {}) or {}
    if not isinstance(raw_args, dict):
        raw_args = {}
    # Some models omit the "args" wrapper and place fields at the top level.
    # Merge any unknown top-level keys into args so the tool handler sees them.
    if not raw_args:
        raw_args = {k: v for k, v in call.items() if k not in ("tool", "args")}
    return {
        "tool": call["tool"],
        "args": raw_args,
        "raw_block": text[start : end + 3],
    }
