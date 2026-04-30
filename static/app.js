// BetterWebUI client. Single-file vanilla JS — no build step.

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  config: null,
  models: [],
  prompts: [],
  skills: [],
  conversations: [],
  workspaces: [],
  mcpServers: [],
  mcpRegistry: [],
  cliTools: [],
  cliRegistry: [],
  currentConversationId: null,
  messages: [],
  attachments: [],
  busy: false,
  // Keyed by filename → { url, mime, filename }. Blob URLs created here
  // survive the `done` re-render so images/audio stay visible in-chat.
  fileStore: {},
};

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: opts.json ? { "Content-Type": "application/json" } : {},
    ...opts,
    body: opts.json ? JSON.stringify(opts.json) : opts.body,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json();
}

function escape(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ---------------------------------------------------------------------------
// Local-download helpers (for generated/written files)
// ---------------------------------------------------------------------------

function b64ToBlob(b64, mime) {
  const bin = atob(b64);
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  return new Blob([arr], { type: mime || "application/octet-stream" });
}

// Store a blob in the browser's fileStore and return its object URL.
// The Download link in the chat is how the user saves it — no auto-click.
function storeFile(blob, filename, mime) {
  const url = URL.createObjectURL(blob);
  state.fileStore[filename] = { url, mime: mime || blob.type || "application/octet-stream", filename };
  return url;
}

async function fileToContentEntry(file) {
  // Read text-ish files as text; binary as base64. The model will receive
  // text content directly and base64 only for binary attachments.
  const isText =
    file.type.startsWith("text/") ||
    /\.(md|markdown|csv|tsv|json|ya?ml|log|txt|py|js|ts|tsx|jsx|html|css|java|c|cpp|h|sh|tex|bib)$/i.test(file.name);
  const entry = {
    filename: file.name,
    content_type: file.type || "application/octet-stream",
    size: file.size,
  };
  if (isText) {
    entry.content = await file.text();
  } else {
    const buf = await file.arrayBuffer();
    let bin = "";
    const bytes = new Uint8Array(buf);
    for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    entry.data_b64 = btoa(bin);
  }
  return entry;
}

// ---------------------------------------------------------------------------
// Markdown + KaTeX rendering
// ---------------------------------------------------------------------------

// Private-use Unicode codepoints used as math-stash delimiters. They never
// appear in real content, survive `marked` and `DOMPurify` untouched, and
// don't depend on surrounding whitespace (the previous " MATH0 " marker
// failed when math hugged a paragraph edge or punctuation: marked
// normalized the spaces away and the restoration regex stopped matching).
const MATH_STASH_OPEN = "";
const MATH_STASH_CLOSE = "";
const MATH_STASH_RE = new RegExp(MATH_STASH_OPEN + "(\\d+)" + MATH_STASH_CLOSE, "g");

function renderMarkdownWithMath(text) {
  // 1. Strip the tool-call block (it's noise to the user).
  text = String(text || "").replace(/```tool[\s\S]*?```/g, "");

  // 2. Protect math regions from markdown processing — otherwise underscores
  //    and asterisks inside formulas get parsed as italics/bold.
  const stash = [];
  const stashOne = (s) => {
    stash.push(s);
    return MATH_STASH_OPEN + (stash.length - 1) + MATH_STASH_CLOSE;
  };
  text = text
    .replace(/\$\$[\s\S]+?\$\$/g, stashOne)
    .replace(/\\\[[\s\S]+?\\\]/g, stashOne)
    .replace(/\\\([\s\S]+?\\\)/g, stashOne)
    .replace(/(?<![\w\d])\$[^$\n]+?\$(?![\w\d])/g, stashOne);

  // 3. Markdown → HTML.
  let html = "";
  if (window.marked) {
    html = marked.parse(text, { breaks: true, gfm: true });
  } else {
    html = escape(text).replace(/\n/g, "<br/>");
  }

  // 4. Sanitize.
  if (window.DOMPurify) {
    html = DOMPurify.sanitize(html, {
      ADD_ATTR: ["target", "rel"],
    });
  }

  // 5. Restore math. The escaped source is the ELEMENT'S innerHTML; KaTeX
  //    auto-render later reads textContent, which decodes back to the raw
  //    LaTeX so $...$ delimiters are seen.
  html = html.replace(MATH_STASH_RE, (_, i) => escape(stash[+i]));

  return html;
}

function renderMathIn(el) {
  if (!window.renderMathInElement || !el) return;
  try {
    renderMathInElement(el, {
      delimiters: [
        { left: "$$", right: "$$", display: true },
        { left: "\\[", right: "\\]", display: true },
        { left: "\\(", right: "\\)", display: false },
        { left: "$", right: "$", display: false },
      ],
      throwOnError: false,
      strict: "ignore",
    });
  } catch (e) {
    console.warn("KaTeX render error:", e);
  }
}

// ---------------------------------------------------------------------------
// Settings tab
// ---------------------------------------------------------------------------

async function loadConfig() {
  state.config = await api("/api/config");
  $("#cfg-base-url").value = state.config.base_url || "";
  $("#cfg-api-key-status").textContent = state.config.api_key_set
    ? "API key is set (enter a new one to replace it)"
    : "Not set";
  $("#cfg-image-model").value = state.config.image_model || "";
  $("#cfg-tts-voice").value = state.config.tts_voice || "alloy";
  $("#cfg-consensus-runs").value = state.config.consensus_runs ?? 1;
  $("#cfg-shell-enabled").checked = state.config.shell_enabled !== false;
  renderConnectionStatus(state.config);
  await loadHealth();
}

function renderConnectionStatus(cfg) {
  const el = $("#connection-status");
  if (!el) return;
  el.className = "status-line";
  if (cfg.api_profile_label) {
    el.textContent = `Connected via ${cfg.api_profile_label}.`;
    el.classList.add("good");
  } else if (cfg.api_key_set && cfg.base_url) {
    el.textContent = "Saved — but no working API endpoint was detected at that URL.";
    el.classList.add("warn");
  } else {
    el.textContent = "Not yet connected.";
  }
}

async function loadHealth() {
  try {
    const h = await api("/api/health");
    $("#about-info").innerHTML =
      `Detected OS: <b>${h.platform}</b> · Shell: <b>${h.shell}</b><br/>` +
      `${h.skills} skill(s) · ${h.workspaces} workspace(s) · ` +
      `${h.mcp_running}/${h.mcp_servers} MCP server(s) running · ${h.cli_tools} CLI shortcut(s)`;
  } catch (e) {
    $("#about-info").textContent = "Health check failed.";
  }
}

async function saveConnection() {
  const baseUrl = $("#cfg-base-url").value.trim();
  const apiKey = $("#cfg-api-key").value.trim();
  const patch = { base_url: baseUrl };
  if (apiKey) patch.api_key = apiKey;
  const el = $("#connection-status");
  el.className = "status-line";
  el.textContent = "Testing…";
  const result = await api("/api/config", { method: "POST", json: patch });
  $("#cfg-api-key").value = "";
  state.config = result;
  $("#cfg-base-url").value = result.base_url || "";
  $("#cfg-api-key-status").textContent = result.api_key_set
    ? "API key is set (enter a new one to replace it)"
    : "Not set";
  renderConnectionStatus(result);
  await refreshModels();
}

async function saveDefaults() {
  const patch = {
    default_model: $("#cfg-default-model").value || null,
    image_model: $("#cfg-image-model").value.trim() || "",
    tts_voice: $("#cfg-tts-voice").value.trim() || "alloy",
    consensus_runs: Math.min(10, Math.max(1, parseInt($("#cfg-consensus-runs").value, 10) || 1)),
    shell_enabled: $("#cfg-shell-enabled").checked,
  };
  await api("/api/config", { method: "POST", json: patch });
  await loadConfig();
  await refreshModels();
  flash("Defaults saved.");
}

// ---------------------------------------------------------------------------
// Models
// ---------------------------------------------------------------------------

async function refreshModels() {
  const data = await api("/api/models");
  state.models = data.models || [];
  if (data.error) flash(data.error, "warn");
  populateModelSelects();
}

function populateModelSelects() {
  const optionHtml =
    '<option value="">— pick a model —</option>' +
    state.models.map((m) => `<option value="${m.id}">${escape(m.name)}</option>`).join("");
  $("#cfg-default-model").innerHTML = optionHtml;
  $("#cfg-default-model").value = state.config?.default_model || "";
  $("#chat-model-select").innerHTML = optionHtml;
  $("#chat-model-select").value = state.config?.default_model || "";
}

// ---------------------------------------------------------------------------
// Prompts
// ---------------------------------------------------------------------------

async function loadPrompts() {
  const data = await api("/api/system-prompts");
  state.prompts = data.prompts || [];
  renderPromptList();
}

function renderPromptList() {
  const ul = $("#prompt-list");
  ul.innerHTML = "";
  for (const p of state.prompts) {
    const li = document.createElement("li");
    li.innerHTML = `
      <div class="list-item-title">
        <div>${escape(p.name)}</div>
        <div class="list-item-desc">${escape(p.content.slice(0, 80))}${p.content.length > 80 ? "…" : ""}</div>
      </div>
      <div class="list-actions">
        <button data-action="edit" data-id="${p.id}">Edit</button>
        <button data-action="delete" data-id="${p.id}">Delete</button>
      </div>`;
    ul.appendChild(li);
  }
  ul.onclick = (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const p = state.prompts.find((x) => x.id === btn.dataset.id);
    if (btn.dataset.action === "edit") openPromptDialog(p);
    if (btn.dataset.action === "delete") deletePrompt(p.id);
  };
}

function openPromptDialog(prompt) {
  const isNew = !prompt;
  const p = prompt || { name: "", content: "" };
  showDialog({
    title: isNew ? "New system prompt" : `Edit: ${p.name}`,
    body: `
      <label>Name <input id="dlg-name" type="text" value="${escape(p.name)}" /></label>
      <label>Content
        <textarea id="dlg-content" rows="10">${escape(p.content)}</textarea>
      </label>
    `,
    actions: [
      { label: "Cancel", action: "cancel" },
      {
        label: "Save",
        primary: true,
        action: async () => {
          const body = {
            id: prompt?.id,
            name: $("#dlg-name").value.trim(),
            content: $("#dlg-content").value,
          };
          if (!body.name) return;
          await api("/api/system-prompts", { method: "POST", json: body });
          await loadPrompts();
          closeDialog();
        },
      },
    ],
  });
}

async function deletePrompt(id) {
  if (!confirm("Delete this prompt?")) return;
  await api(`/api/system-prompts/${id}`, { method: "DELETE" });
  await loadPrompts();
}

// ---------------------------------------------------------------------------
// Skills
// ---------------------------------------------------------------------------

async function loadSkills() {
  const data = await api("/api/skills");
  state.skills = data.skills || [];
  renderSkillList();
}

function renderSkillList() {
  const ul = $("#skill-list");
  ul.innerHTML = "";
  for (const s of state.skills) {
    const li = document.createElement("li");
    li.innerHTML = `
      <div class="list-item-title">
        <div>${escape(s.name)} <small>(${escape(s.id)})</small></div>
        <div class="list-item-desc">${escape(s.description || "no description")}</div>
      </div>
      <div class="list-actions">
        <button data-action="view" data-id="${s.id}">View</button>
        <button data-action="delete" data-id="${s.id}">Delete</button>
      </div>`;
    ul.appendChild(li);
  }
  ul.onclick = async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    if (btn.dataset.action === "view") {
      const skill = await api(`/api/skills/${btn.dataset.id}`);
      showDialog({
        title: skill.name,
        body: `<p class="hint">${escape(skill.description)}</p>
               <pre>${escape(skill.content)}</pre>`,
        actions: [{ label: "Close", action: "cancel" }],
      });
    }
    if (btn.dataset.action === "delete") {
      if (!confirm("Delete this skill?")) return;
      await api(`/api/skills/${btn.dataset.id}`, { method: "DELETE" });
      await loadSkills();
    }
  };
}

function openNewSkillDialog() {
  showDialog({
    title: "New skill",
    body: `
      <label>ID (no spaces) <input id="dlg-id" type="text" placeholder="research-paper" /></label>
      <label>Name <input id="dlg-name" type="text" placeholder="Research Paper Helper" /></label>
      <label>When to use it
        <input id="dlg-desc" type="text" placeholder="When the user is writing a research paper or needs help with citations" />
      </label>
      <label>Instructions for the assistant
        <textarea id="dlg-content" rows="10" placeholder="When this skill is loaded, help the user by..."></textarea>
      </label>
    `,
    actions: [
      { label: "Cancel", action: "cancel" },
      {
        label: "Save",
        primary: true,
        action: async () => {
          const body = {
            id: $("#dlg-id").value.trim(),
            name: $("#dlg-name").value.trim(),
            description: $("#dlg-desc").value.trim(),
            content: $("#dlg-content").value,
          };
          if (!body.id || !body.name) return;
          await api("/api/skills", { method: "POST", json: body });
          await loadSkills();
          closeDialog();
        },
      },
    ],
  });
}

async function uploadSkill(file) {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/api/skills/upload", { method: "POST", body: fd });
  if (!res.ok) {
    flash("Upload failed: " + (await res.text()), "warn");
    return;
  }
  await loadSkills();
}

// ---------------------------------------------------------------------------
// Workspaces
// ---------------------------------------------------------------------------

async function loadWorkspaces() {
  const data = await api("/api/workspaces");
  state.workspaces = data.workspaces || [];
  renderWorkspaceList();
  populateWorkspaceSelect();
}

function renderWorkspaceList() {
  const ul = $("#workspace-list");
  ul.innerHTML = "";
  if (!state.workspaces.length) {
    ul.innerHTML = `<p class="hint" style="padding:8px;">No workspaces yet. Create one to bundle a system prompt, skills, MCP servers, and CLI shortcuts together.</p>`;
    return;
  }
  for (const w of state.workspaces) {
    const isActive = state.config?.active_workspace_id === w.id;
    const li = document.createElement("li");
    li.classList.toggle("active", isActive);
    li.innerHTML = `
      <div class="list-item-title">
        <div>${escape(w.name)} ${isActive ? '<span class="badge">active</span>' : ""}</div>
        <div class="list-item-desc">${escape(w.description || "—")}</div>
        <div class="list-item-meta">
          ${w.active_skills?.length || 0} skill(s) ·
          ${w.active_mcp_servers?.length || 0} MCP ·
          ${w.active_cli_tools?.length || 0} CLI ·
          ${w.files?.length || 0} file(s)
        </div>
      </div>
      <div class="list-actions">
        <button data-action="activate" data-id="${w.id}">Use</button>
        <button data-action="edit" data-id="${w.id}">Edit</button>
        <button data-action="delete" data-id="${w.id}">Delete</button>
      </div>`;
    ul.appendChild(li);
  }
  ul.onclick = async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const id = btn.dataset.id;
    if (btn.dataset.action === "activate") return activateWorkspace(id);
    if (btn.dataset.action === "edit") return openWorkspaceDialog(state.workspaces.find((x) => x.id === id));
    if (btn.dataset.action === "delete") {
      if (!confirm("Delete this workspace?")) return;
      await api(`/api/workspaces/${id}`, { method: "DELETE" });
      await loadWorkspaces();
      await loadConfig();
    }
  };
}

function populateWorkspaceSelect() {
  const sel = $("#workspace-select");
  sel.innerHTML =
    '<option value="">— No workspace —</option>' +
    state.workspaces.map((w) => `<option value="${w.id}">${escape(w.name)}</option>`).join("");
  sel.value = state.config?.active_workspace_id || "";
  const label = $("#active-workspace-label");
  const active = state.workspaces.find((w) => w.id === state.config?.active_workspace_id);
  label.textContent = active ? active.name : "";
}

async function activateWorkspace(id) {
  await api("/api/config", {
    method: "POST",
    json: { active_workspace_id: id || "" },
  });
  await loadConfig();
  await loadWorkspaces();
  // Workspace switch starts a fresh chat by default
  newChat();
}

function openWorkspaceDialog(workspace) {
  const isNew = !workspace;
  const w = workspace || {
    id: "",
    name: "",
    description: "",
    system_prompt_id: "",
    active_skills: [],
    active_mcp_servers: [],
    active_cli_tools: [],
    files: [],
    default_model: "",
  };
  const skillsList = state.skills
    .map(
      (s) => `<label class="checkbox"><input type="checkbox" data-kind="skill" data-id="${s.id}" ${
        w.active_skills?.includes(s.id) ? "checked" : ""
      } /> <span><b>${escape(s.name)}</b> — <small>${escape(s.description)}</small></span></label>`,
    )
    .join("");
  const mcpList = state.mcpServers
    .map(
      (m) => `<label class="checkbox"><input type="checkbox" data-kind="mcp" data-id="${m.name}" ${
        w.active_mcp_servers?.includes(m.name) ? "checked" : ""
      } /> <span><b>${escape(m.name)}</b> — <small>${escape(m.description || "")}</small></span></label>`,
    )
    .join("");
  const cliList = state.cliTools
    .map(
      (c) => `<label class="checkbox"><input type="checkbox" data-kind="cli" data-id="${c.id}" ${
        w.active_cli_tools?.includes(c.id) ? "checked" : ""
      } /> <span><b>${escape(c.name)}</b> — <small>${escape(c.description || "")}</small></span></label>`,
    )
    .join("");
  const promptOptions = state.prompts
    .map((p) => `<option value="${p.id}" ${p.id === w.system_prompt_id ? "selected" : ""}>${escape(p.name)}</option>`)
    .join("");
  const filesPreview = (w.files || [])
    .map((f, i) => `<div class="file-row"><span>${escape(f.filename)}</span> <small>${escape(f.content_type || "")}</small> <button type="button" data-remove-file="${i}">×</button></div>`)
    .join("");

  showDialog({
    title: isNew ? "New workspace" : `Edit: ${w.name}`,
    wide: true,
    body: `
      <label>Name <input id="dlg-name" type="text" value="${escape(w.name)}" /></label>
      <label>Description <input id="dlg-desc" type="text" value="${escape(w.description || "")}" placeholder="When you'd reach for this workspace" /></label>
      <label>System prompt
        <select id="dlg-prompt">
          <option value="">— default —</option>
          ${promptOptions}
        </select>
      </label>
      <label>Default model <em>(optional)</em>
        <select id="dlg-model">
          <option value="">— inherit —</option>
          ${state.models.map((m) => `<option value="${m.id}" ${m.id === w.default_model ? "selected" : ""}>${escape(m.name)}</option>`).join("")}
        </select>
      </label>

      <h3>Skills available in this workspace</h3>
      <div class="check-grid">${skillsList || '<p class="hint">No skills yet.</p>'}</div>

      <h3>MCP servers</h3>
      <div class="check-grid">${mcpList || '<p class="hint">No MCP servers configured. Add them under Tools.</p>'}</div>

      <h3>CLI shortcuts</h3>
      <div class="check-grid">${cliList || '<p class="hint">No CLI shortcuts configured. Add them under Tools.</p>'}</div>

      <h3>Persistent files</h3>
      <p class="hint">Files added here travel with every chat in this workspace (attached to the first message). Useful for syllabi, rubrics, drafts.</p>
      <div id="dlg-files">${filesPreview}</div>
      <label class="upload-label inline">
        + Add file
        <input id="dlg-add-file" type="file" hidden multiple />
      </label>
    `,
    actions: [
      { label: "Cancel", action: "cancel" },
      {
        label: "Save",
        primary: true,
        action: async () => {
          const body = {
            id: w.id || undefined,
            name: $("#dlg-name").value.trim(),
            description: $("#dlg-desc").value.trim(),
            system_prompt_id: $("#dlg-prompt").value || null,
            default_model: $("#dlg-model").value || null,
            active_skills: collectChecked("skill"),
            active_mcp_servers: collectChecked("mcp"),
            active_cli_tools: collectChecked("cli"),
            files: pendingWorkspaceFiles,
          };
          if (!body.name) return;
          const res = await api("/api/workspaces", { method: "POST", json: body });
          await loadWorkspaces();
          // Auto-activate new workspaces
          if (isNew) await activateWorkspace(res.id);
          closeDialog();
        },
      },
    ],
  });

  // Wire file additions/removals into a mutable working list
  let pendingWorkspaceFiles = [...(w.files || [])];
  const renderFiles = () => {
    $("#dlg-files").innerHTML = pendingWorkspaceFiles
      .map((f, i) => `<div class="file-row"><span>${escape(f.filename)}</span> <small>${escape(f.content_type || "")}</small> <button type="button" data-remove-file="${i}">×</button></div>`)
      .join("");
    $("#dlg-files").querySelectorAll("[data-remove-file]").forEach((btn) => {
      btn.onclick = () => {
        pendingWorkspaceFiles.splice(+btn.dataset.removeFile, 1);
        renderFiles();
      };
    });
  };
  renderFiles();
  $("#dlg-add-file").onchange = async (e) => {
    for (const file of e.target.files) {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch("/api/upload", { method: "POST", body: fd });
      if (res.ok) {
        const a = await res.json();
        pendingWorkspaceFiles.push(a);
      }
    }
    renderFiles();
    e.target.value = "";
  };
}

function collectChecked(kind) {
  return Array.from(document.querySelectorAll(`[data-kind="${kind}"]`))
    .filter((el) => el.checked)
    .map((el) => el.dataset.id);
}

// ---------------------------------------------------------------------------
// MCP servers (Tools tab)
// ---------------------------------------------------------------------------

async function loadMcp() {
  const [servers, registry] = await Promise.all([
    api("/api/mcp/servers"),
    api("/api/mcp/registry"),
  ]);
  state.mcpServers = servers.servers || [];
  state.mcpRegistry = registry.registry || [];
  renderMcpServers();
}

function renderMcpServers() {
  const ul = $("#mcp-server-list");
  ul.innerHTML = "";
  if (!state.mcpServers.length) {
    ul.innerHTML = `<p class="hint" style="padding:8px;">No MCP servers configured.</p>`;
    return;
  }
  for (const s of state.mcpServers) {
    const li = document.createElement("li");
    const dot = s.running ? "good" : (s.error ? "warn" : "muted");
    li.innerHTML = `
      <div class="list-item-title">
        <div><span class="status-dot ${dot}"></span>${escape(s.name)}
          ${s.running ? `<small>(${s.tool_count} tool${s.tool_count === 1 ? "" : "s"})</small>` : ""}
        </div>
        <div class="list-item-desc">${escape(s.description || "")}${
          s.error ? ` <span class="warn-text">${escape(s.error)}</span>` : ""
        }</div>
      </div>
      <div class="list-actions">
        <button data-action="tools" data-name="${escape(s.name)}">Tools</button>
        <button data-action="delete" data-name="${escape(s.name)}">Remove</button>
      </div>`;
    ul.appendChild(li);
  }
  ul.onclick = async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const name = btn.dataset.name;
    if (btn.dataset.action === "delete") {
      if (!confirm(`Remove MCP server '${name}'?`)) return;
      await api(`/api/mcp/servers/${encodeURIComponent(name)}`, { method: "DELETE" });
      await loadMcp();
    }
    if (btn.dataset.action === "tools") {
      const s = state.mcpServers.find((x) => x.name === name);
      const tools = (s?.tools || []).map((t) => `<li><b>${escape(t.name)}</b> — <small>${escape(t.description || "")}</small></li>`).join("");
      showDialog({
        title: `${name} — exposed tools`,
        body: tools ? `<ul class="plain-list">${tools}</ul>` : `<p class="hint">No tools (server may still be starting, or in error).</p>`,
        actions: [{ label: "Close", action: "cancel" }],
      });
    }
  };
}

function openMcpRegistryDialog() {
  const items = state.mcpRegistry
    .map(
      (r, i) => `
      <div class="registry-card" data-i="${i}">
        <h3>${escape(r.name)}</h3>
        <p class="hint">${escape(r.description)}</p>
        <p class="hint"><b>Requires:</b> ${escape(r.requires || "—")}</p>
        <button class="primary" data-i="${i}">Add</button>
      </div>`,
    )
    .join("");
  showDialog({
    title: "Add an MCP server",
    wide: true,
    body: `<div class="registry-grid">${items}</div>`,
    actions: [{ label: "Close", action: "cancel" }],
  });
  document.querySelectorAll(".registry-card button").forEach((btn) => {
    btn.onclick = () => {
      const r = state.mcpRegistry[+btn.dataset.i];
      openMcpFieldsDialog(r);
    };
  });
}

function openMcpFieldsDialog(reg) {
  const fields = reg.fields || [];
  const fieldHtml =
    fields
      .map(
        (f) => `
      <label>${escape(f.label)}
        <input id="dlg-f-${f.name}" type="${f.type === "password" ? "password" : "text"}" />
      </label>`,
      )
      .join("") || `<p class="hint">No additional configuration needed.</p>`;
  showDialog({
    title: `Add: ${reg.name}`,
    body: `
      <p class="hint">${escape(reg.description)}</p>
      <p class="hint"><b>Requires:</b> ${escape(reg.requires || "—")}</p>
      ${fieldHtml}
      <label>Server name
        <input id="dlg-name" type="text" value="${escape(reg.id)}" />
        <small>Used to refer to this server in tool calls and workspaces.</small>
      </label>
    `,
    actions: [
      { label: "Cancel", action: "cancel" },
      {
        label: "Add",
        primary: true,
        action: async () => {
          const values = {};
          for (const f of fields) {
            values[f.name] = $(`#dlg-f-${f.name}`).value.trim();
            if (!values[f.name]) {
              flash(`${f.label} is required.`, "warn");
              return;
            }
          }
          const args = (reg.args_template || []).map((a) => fillTemplate(a, values));
          const env = Object.fromEntries(
            Object.entries(reg.env_template || {}).map(([k, v]) => [k, fillTemplate(v, values)]),
          );
          const body = {
            name: $("#dlg-name").value.trim() || reg.id,
            command: reg.command,
            args,
            env,
            description: reg.description,
            enabled: true,
          };
          await api("/api/mcp/servers", { method: "POST", json: body });
          await loadMcp();
          closeDialog();
          flash("MCP server added — bringing it up may take a moment.", "good");
        },
      },
    ],
  });
}

function fillTemplate(tpl, values) {
  return String(tpl).replace(/\{(\w+)\}/g, (_, k) => values[k] ?? "");
}

function openMcpCustomDialog() {
  showDialog({
    title: "Custom MCP server",
    body: `
      <label>Name <input id="dlg-name" type="text" placeholder="my-server" /></label>
      <label>Command <input id="dlg-cmd" type="text" placeholder="npx, uvx, python, /path/to/binary" /></label>
      <label>Arguments (one per line)
        <textarea id="dlg-args" rows="4" placeholder="-y\\n@my-org/my-mcp-server"></textarea>
      </label>
      <label>Environment variables (KEY=value, one per line)
        <textarea id="dlg-env" rows="3"></textarea>
      </label>
      <label>Description <input id="dlg-desc" type="text" /></label>
    `,
    actions: [
      { label: "Cancel", action: "cancel" },
      {
        label: "Add",
        primary: true,
        action: async () => {
          const env = {};
          for (const line of $("#dlg-env").value.split("\n")) {
            const m = line.match(/^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$/);
            if (m) env[m[1]] = m[2];
          }
          const body = {
            name: $("#dlg-name").value.trim(),
            command: $("#dlg-cmd").value.trim(),
            args: $("#dlg-args").value.split("\n").map((s) => s.trim()).filter(Boolean),
            env,
            description: $("#dlg-desc").value.trim(),
            enabled: true,
          };
          if (!body.name || !body.command) return;
          await api("/api/mcp/servers", { method: "POST", json: body });
          await loadMcp();
          closeDialog();
        },
      },
    ],
  });
}

// ---------------------------------------------------------------------------
// CLI shortcuts (Tools tab)
// ---------------------------------------------------------------------------

async function loadCli() {
  const [tools, registry] = await Promise.all([
    api("/api/cli/tools"),
    api("/api/cli/registry"),
  ]);
  state.cliTools = tools.tools || [];
  state.cliRegistry = registry.registry || [];
  renderCliTools();
}

function renderCliTools() {
  const ul = $("#cli-tool-list");
  ul.innerHTML = "";
  if (!state.cliTools.length) {
    ul.innerHTML = `<p class="hint" style="padding:8px;">No CLI shortcuts yet.</p>`;
    return;
  }
  for (const c of state.cliTools) {
    const li = document.createElement("li");
    li.innerHTML = `
      <div class="list-item-title">
        <div>${escape(c.name)} <small>(${escape(c.id)})</small></div>
        <div class="list-item-desc">${escape(c.description || "")}</div>
        <div class="list-item-meta"><code>${escape(c.command_template || "")}</code></div>
      </div>
      <div class="list-actions">
        <button data-action="delete" data-id="${escape(c.id)}">Remove</button>
      </div>`;
    ul.appendChild(li);
  }
  ul.onclick = async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    if (btn.dataset.action === "delete") {
      await api(`/api/cli/tools/${encodeURIComponent(btn.dataset.id)}`, { method: "DELETE" });
      await loadCli();
    }
  };
}

function openCliRegistryDialog() {
  const items = state.cliRegistry
    .map(
      (r, i) => `
      <div class="registry-card" data-i="${i}">
        <h3>${escape(r.name)}</h3>
        <p class="hint">${escape(r.description)}</p>
        <p class="hint"><code>${escape(r.command_template)}</code></p>
        <button class="primary" data-i="${i}">Add</button>
      </div>`,
    )
    .join("");
  showDialog({
    title: "Add a CLI shortcut",
    wide: true,
    body: `<div class="registry-grid">${items}</div>`,
    actions: [{ label: "Close", action: "cancel" }],
  });
  document.querySelectorAll(".registry-card button").forEach((btn) => {
    btn.onclick = async () => {
      const r = state.cliRegistry[+btn.dataset.i];
      await api("/api/cli/tools", { method: "POST", json: r });
      await loadCli();
      closeDialog();
    };
  });
}

function openCliCustomDialog() {
  showDialog({
    title: "Custom CLI shortcut",
    body: `
      <label>ID <input id="dlg-id" type="text" placeholder="summarize-pdf" /></label>
      <label>Name <input id="dlg-name" type="text" placeholder="Summarize PDF" /></label>
      <label>Description <input id="dlg-desc" type="text" placeholder="When to use this shortcut" /></label>
      <label>Command template
        <input id="dlg-cmd" type="text" placeholder="pandoc {args}" />
        <small>Use <code>{args}</code> as the placeholder for arguments the assistant fills in.</small>
      </label>
    `,
    actions: [
      { label: "Cancel", action: "cancel" },
      {
        label: "Add",
        primary: true,
        action: async () => {
          const body = {
            id: $("#dlg-id").value.trim(),
            name: $("#dlg-name").value.trim(),
            description: $("#dlg-desc").value.trim(),
            command_template: $("#dlg-cmd").value.trim(),
          };
          if (!body.id || !body.name || !body.command_template) return;
          await api("/api/cli/tools", { method: "POST", json: body });
          await loadCli();
          closeDialog();
        },
      },
    ],
  });
}

// ---------------------------------------------------------------------------
// Conversations
// ---------------------------------------------------------------------------

async function loadConversations() {
  const data = await api("/api/conversations");
  state.conversations = data.conversations || [];
  renderConversationList();
}

function renderConversationList() {
  const ul = $("#conversation-list");
  ul.innerHTML = "";
  if (!state.conversations.length) {
    ul.innerHTML = '<p class="hint" style="padding: 8px;">No chats yet.</p>';
    return;
  }
  for (const c of state.conversations) {
    const li = document.createElement("li");
    if (c.id === state.currentConversationId) li.classList.add("active");
    li.innerHTML = `
      <div class="list-item-title">${escape(c.title || "Untitled")}</div>
      <div class="list-actions">
        <button data-action="delete" data-id="${c.id}" title="Delete">×</button>
      </div>`;
    li.onclick = (e) => {
      const btn = e.target.closest("button");
      if (btn?.dataset.action === "delete") {
        e.stopPropagation();
        deleteConversation(c.id);
        return;
      }
      openConversation(c.id);
    };
    ul.appendChild(li);
  }
}

async function openConversation(id) {
  const conv = await api(`/api/conversations/${id}`);
  state.currentConversationId = id;
  state.messages = conv.messages || [];
  renderMessages();
  renderConversationList();
}

async function deleteConversation(id) {
  if (!confirm("Delete this conversation?")) return;
  await api(`/api/conversations/${id}`, { method: "DELETE" });
  if (state.currentConversationId === id) newChat();
  await loadConversations();
}

function newChat() {
  state.currentConversationId = null;
  state.messages = [];
  state.attachments = [];
  renderMessages();
  renderAttachments();
  renderConversationList();
}

// ---------------------------------------------------------------------------
// Messages rendering
// ---------------------------------------------------------------------------

function renderMessages() {
  const container = $("#messages");
  container.innerHTML = "";
  if (!state.messages.length) {
    const ws = state.workspaces.find((w) => w.id === state.config?.active_workspace_id);
    const wsLine = ws
      ? `Working in <b>${escape(ws.name)}</b>. Conversations here use its system prompt, skills, and tools.`
      : "Pick a workspace from the top of this chat, or just begin.";
    container.innerHTML = `
      <div class="empty-state">
        <div class="ornament">§</div>
        <h2>Begin where you are.</h2>
        <p>Ask a question, paste a draft, or describe a task. The assistant can run commands, read your files, and create images or audio — always with your approval.</p>
        <p class="hint">${wsLine}</p>
        <hr />
        <p class="hint">First time here? Open <b>Settings</b> to enter your OpenWebUI URL and API key.</p>
      </div>`;
    return;
  }
  for (const m of state.messages) {
    appendMessage(m);
  }
}

function appendMessage(m) {
  const container = $("#messages");
  const tpl = $("#message-template").content.cloneNode(true);
  const wrap = tpl.querySelector(".message");
  wrap.classList.add(m.role);
  if (m._placeholder) wrap.classList.add("typing");
  const isToolResult =
    m.role === "tool" || (m.role === "user" && (m.content || "").startsWith("[Tool"));
  if (isToolResult) {
    wrap.classList.add("tool");
    tpl.querySelector(".role").textContent = "Tool result";
  } else {
    tpl.querySelector(".role").textContent =
      m.role === "assistant" ? "Assistant" : m.role === "user" ? "You" : m.role;
  }

  const content = tpl.querySelector(".content");
  if (m._placeholder) {
    content.innerHTML =
      '<span class="typing-dots"><span></span><span></span><span></span></span>';
  } else if (m.role === "assistant") {
    content.innerHTML = renderMarkdownWithMath(m.content || "");
  } else if (isToolResult) {
    content.textContent = m.content || "";
  } else {
    content.innerHTML = renderMarkdownWithMath(m.content || "");
  }

  // For backend-synced tool result messages (role:"user", content:"[Tool..."),
  // look up the fileStore so images/audio survive the `done` re-render.
  let effectiveAttachments = m.attachments || [];
  if (isToolResult && m.role === "user" && !effectiveAttachments.length) {
    const fm = (m.content || "").match(/"filename":\s*"([^"]+)"/);
    if (fm) {
      const stored = state.fileStore[fm[1]];
      if (stored) effectiveAttachments = [{ url: stored.url, content_type: stored.mime, filename: stored.filename }];
    }
  }

  if (effectiveAttachments.length) {
    const att = document.createElement("div");
    att.className = "attachments";
    for (const a of effectiveAttachments) {
      const ct = a.content_type || "";
      if (ct.startsWith("image/")) {
        const img = document.createElement("img");
        img.src = a.url;
        img.alt = a.filename || "";
        att.appendChild(img);
      } else if (ct.startsWith("audio/")) {
        const audio = document.createElement("audio");
        audio.controls = true;
        audio.src = a.url;
        att.appendChild(audio);
      } else if (ct.startsWith("video/")) {
        const video = document.createElement("video");
        video.controls = true;
        video.src = a.url;
        video.style.maxWidth = "100%";
        att.appendChild(video);
      } else {
        const span = document.createElement("span");
        span.className = "file-pill";
        span.textContent = a.filename || a.url;
        att.appendChild(span);
      }
      if (a.url && a.filename) {
        const dl = document.createElement("a");
        dl.href = a.url;
        dl.download = a.filename;
        dl.className = "download-link";
        dl.textContent = "Download";
        att.appendChild(dl);
      }
    }
    content.appendChild(att);
  }

  container.appendChild(tpl);
  // Render math after the node is in the DOM
  const newEl = container.lastElementChild;
  if (newEl && m.role === "assistant") renderMathIn(newEl);
  container.scrollTop = container.scrollHeight;
  return newEl;
}

// ---------------------------------------------------------------------------
// Composer / attachments
// ---------------------------------------------------------------------------

async function attachFile(file) {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/api/upload", { method: "POST", body: fd });
  if (!res.ok) {
    flash("Upload failed.", "warn");
    return;
  }
  const a = await res.json();
  state.attachments.push(a);
  renderAttachments();
}

function renderAttachments() {
  const wrap = $("#attachments-preview");
  wrap.innerHTML = "";
  state.attachments.forEach((a, i) => {
    const span = document.createElement("span");
    span.className = "pill";
    span.innerHTML = `${escape(a.filename)} <button data-i="${i}" title="Remove">×</button>`;
    span.querySelector("button").onclick = () => {
      state.attachments.splice(i, 1);
      renderAttachments();
    };
    wrap.appendChild(span);
  });
}

// ---------------------------------------------------------------------------
// Send + SSE chat loop
// ---------------------------------------------------------------------------

async function send() {
  if (state.busy) return;
  const text = $("#composer-input").value.trim();
  if (!text && !state.attachments.length) return;

  // If a workspace is active and this is the first message, prepend its
  // persistent files as attachments.
  let attachments = state.attachments.slice();
  const ws = state.workspaces.find((w) => w.id === state.config?.active_workspace_id);
  if (ws && state.messages.length === 0 && Array.isArray(ws.files)) {
    attachments = [...ws.files, ...attachments];
  }

  const userMsg = { role: "user", content: text, attachments };
  state.messages.push(userMsg);
  appendMessage(userMsg);
  $("#composer-input").value = "";
  state.attachments = [];
  renderAttachments();

  const model = $("#chat-model-select").value || ws?.default_model || state.config?.default_model;
  if (!model) {
    flash("Pick a model first (top of chat or Settings).", "warn");
    return;
  }

  state.busy = true;
  const sendBtn = $("#send-btn");
  sendBtn.disabled = true;
  sendBtn.innerHTML = '<span class="spinner"></span>';

  // Animated typing bubble — visible immediately, before the server responds.
  const placeholder = { role: "assistant", content: "", _placeholder: true };
  state.messages.push(placeholder);
  appendMessage(placeholder);
  const placeholderEl = $("#messages").lastElementChild;

  try {
    // Only user/assistant turns travel back to the API. Pure-UI message
    // types ("system-event", in-chat tool-result displays) are stripped.
    const sendable = state.messages.filter(
      (m) => !m._placeholder && (m.role === "user" || m.role === "assistant"),
    );
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        conversation_id: state.currentConversationId,
        messages: sendable,
        model,
      }),
    });
    if (!res.ok) {
      const t = await res.text();
      throw new Error(`${res.status}: ${t}`);
    }
    placeholderEl.remove();
    state.messages = state.messages.filter((m) => !m._placeholder);

    await consumeSSE(res, async (event, data) => {
      if (event === "assistant_text") {
        const msg = { role: "assistant", content: data.text };
        state.messages.push(msg);
        appendMessage(msg);
        return;
      }
      if (event === "approval_request") {
        const approved = await askApproval(data);
        await api("/api/approve", {
          method: "POST",
          json: { approval_id: data.approval_id, approved },
        });
        return;
      }
      if (event === "file_request") {
        await handleFileRequest(data);
        return;
      }
      if (event === "tool_running") {
        const sysMsg = {
          role: "system-event",
          content: `Running ${data.tool}: ${data.command || ""}`,
        };
        state.messages.push(sysMsg);
        appendMessage(sysMsg);
        return;
      }
      if (event === "notice") {
        flash(data.message);
        return;
      }
      if (event === "tool_call") return;
      if (event === "tool_result") {
        await handleToolResult(data);
        return;
      }
      if (event === "done") {
        state.currentConversationId = data.conversation_id;
        // Re-sync from the backend's canonical history. The backend
        // includes synthetic "[Tool 'X' result]" user messages, which the
        // renderer recognizes and styles as tool blocks. This keeps the
        // model's tool-result context for follow-up turns.
        if (Array.isArray(data.messages)) {
          state.messages = data.messages;
          renderMessages();
        }
        await loadConversations();
        return;
      }
      if (event === "error") {
        const sysMsg = { role: "system-event", content: "Error: " + data.message };
        state.messages.push(sysMsg);
        appendMessage(sysMsg);
        return;
      }
    });
  } catch (e) {
    placeholderEl?.remove();
    state.messages = state.messages.filter((m) => !m._placeholder);
    flash("Send failed: " + e.message, "warn");
  } finally {
    state.busy = false;
    const btn = $("#send-btn");
    btn.disabled = false;
    btn.textContent = "Send";
  }
}

async function handleToolResult(data) {
  const r = data.result || {};

  // Generated/written files: store blob URL in fileStore for inline rendering
  // and user-initiated download; no automatic download to disk.
  if (r.data_b64 && r.filename) {
    const mime = r.mime || "application/octet-stream";
    const blob = b64ToBlob(r.data_b64, mime);
    const url = storeFile(blob, r.filename, mime);
    const label =
      mime.startsWith("image/") ? "Image" :
      mime.startsWith("audio/") ? "Audio" :
      mime.startsWith("video/") ? "Video" : "File";
    const sysMsg = {
      role: "tool",
      content: `${label} ready: ${r.filename}`,
      attachments: [{ url, content_type: mime, filename: r.filename }],
    };
    state.messages.push(sysMsg);
    appendMessage(sysMsg);
    return;
  }

  // Text summary for shell results
  if (data.tool === "execute_shell" || data.tool === "cli_call") {
    const text =
      `Exit ${r.exit_code} (${r.shell || ""}, ${r.duration_ms || 0}ms)\n` +
      `--- stdout ---\n${r.stdout || ""}\n` +
      (r.stderr ? `--- stderr ---\n${r.stderr}\n` : "");
    const sysMsg = { role: "tool", content: text };
    state.messages.push(sysMsg);
    appendMessage(sysMsg);
    return;
  }

  // read_file results: summarize without re-rendering all content
  if (data.tool === "read_file") {
    if (r.error) {
      const sysMsg = { role: "system-event", content: r.error };
      state.messages.push(sysMsg);
      appendMessage(sysMsg);
      return;
    }
    const lines = (r.files || []).map((f) => `${f.filename} (${f.content_type || "?"}, ${f.size || 0}B)`);
    const sysMsg = { role: "tool", content: `Read ${lines.length} file(s):\n${lines.join("\n")}` };
    state.messages.push(sysMsg);
    appendMessage(sysMsg);
    return;
  }

  // Default: show JSON-ish summary
  const sysMsg = { role: "tool", content: JSON.stringify(r, null, 2).slice(0, 3000) };
  state.messages.push(sysMsg);
  appendMessage(sysMsg);
}

async function consumeSSE(res, onEvent) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) !== -1) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const lines = block.split("\n");
      let eventName = "message";
      let dataStr = "";
      for (const ln of lines) {
        if (ln.startsWith("event:")) eventName = ln.slice(6).trim();
        else if (ln.startsWith("data:")) dataStr += ln.slice(5).trim();
      }
      if (!dataStr) continue;
      try {
        await onEvent(eventName, JSON.parse(dataStr));
      } catch (e) {
        console.error("SSE handler error", e);
      }
    }
  }
}

// ---------------------------------------------------------------------------
// File-request dialog (open file picker, send back contents)
// ---------------------------------------------------------------------------

async function handleFileRequest(req) {
  const filesPicked = await new Promise((resolve) => {
    showDialog({
      title: "The assistant would like to read a file",
      body: `
        <p>${escape(req.purpose || "Read file(s) from your computer.")}</p>
        <p class="hint">Files stay on your computer. The assistant only sees the contents you choose to share.</p>
        <label class="upload-label inline" id="file-pick-label">
          Choose file${req.multiple ? "(s)" : ""}
          <input id="file-pick-input" type="file" hidden ${req.multiple ? "multiple" : ""} ${
            req.accept && req.accept !== "*/*" ? `accept="${escape(req.accept)}"` : ""
          } />
        </label>
        <div id="file-pick-preview"></div>
      `,
      actions: [
        {
          label: "Skip",
          action: () => {
            closeDialog();
            resolve([]);
          },
        },
      ],
    });
    const input = $("#file-pick-input");
    const preview = $("#file-pick-preview");
    input.onchange = async () => {
      const fs = Array.from(input.files || []);
      if (!fs.length) return;
      preview.innerHTML = `<p class="hint">Reading ${fs.length} file${fs.length === 1 ? "" : "s"}…</p>`;
      const entries = await Promise.all(fs.map(fileToContentEntry));
      closeDialog();
      resolve(entries);
    };
  });

  await api("/api/file-response", {
    method: "POST",
    json: { request_id: req.request_id, files: filesPicked },
  });
}

// ---------------------------------------------------------------------------
// Approval dialog (shell + write)
// ---------------------------------------------------------------------------

function askApproval(req) {
  return new Promise((resolve) => {
    let title, body;
    if (req.tool === "execute_shell") {
      title = `Run a ${req.shell} command?`;
      body = `
        <div class="danger-banner"><strong>Caution.</strong> The assistant wants to run a command on your computer. Read it carefully before approving.</div>
        ${req.reason ? `<p><b>Why:</b> ${escape(req.reason)}</p>` : ""}
        <p><b>Command:</b></p>
        <pre>${escape(req.command)}</pre>
      `;
    } else if (req.tool === "write_file") {
      title = `Accept file from assistant?`;
      body = `
        <p>The assistant wants to share a file <b>(${req.byte_count} bytes)</b>. Approve to add it to the chat, where you can preview and download it.</p>
        <p><b>Filename:</b> <code>${escape(req.filename)}</code></p>
        <p><b>Preview:</b></p>
        <pre>${escape(req.preview || "")}</pre>
      `;
    } else {
      title = `Allow ${req.tool}?`;
      body = `<pre>${escape(JSON.stringify(req, null, 2))}</pre>`;
    }
    showDialog({
      title,
      body,
      actions: [
        {
          label: "Deny",
          action: () => {
            closeDialog();
            resolve(false);
          },
        },
        {
          label: "Approve",
          primary: true,
          action: () => {
            closeDialog();
            resolve(true);
          },
        },
      ],
    });
  });
}

// ---------------------------------------------------------------------------
// Generic dialog
// ---------------------------------------------------------------------------

function showDialog({ title, body, actions, wide }) {
  closeDialog();
  const root = $("#dialog-root");
  const wrap = document.createElement("div");
  wrap.className = "dialog-backdrop";
  wrap.innerHTML = `
    <div class="dialog ${wide ? "wide" : ""}">
      <h2>${escape(title)}</h2>
      <div class="dialog-body">${body}</div>
      <div class="dialog-actions"></div>
    </div>`;
  const actionsEl = wrap.querySelector(".dialog-actions");
  for (const a of actions) {
    const btn = document.createElement("button");
    btn.textContent = a.label;
    if (a.primary) btn.classList.add("primary");
    btn.onclick = () => {
      if (a.action === "cancel") closeDialog();
      else if (typeof a.action === "function") a.action();
    };
    actionsEl.appendChild(btn);
  }
  root.appendChild(wrap);
}

function closeDialog() {
  $("#dialog-root").innerHTML = "";
}

function flash(msg, level = "info") {
  let host = document.getElementById("toast-root");
  if (!host) {
    host = document.createElement("div");
    host.id = "toast-root";
    document.body.appendChild(host);
  }
  const t = document.createElement("div");
  t.className = `toast ${level}`;
  t.textContent = msg;
  host.appendChild(t);
  // Force a reflow so the entry transition runs
  // eslint-disable-next-line no-unused-expressions
  t.offsetHeight;
  t.classList.add("visible");
  setTimeout(() => {
    t.classList.remove("visible");
    setTimeout(() => t.remove(), 250);
  }, 3500);
}

// ---------------------------------------------------------------------------
// Tabs and wiring
// ---------------------------------------------------------------------------

function wireTabs() {
  $$(".tab").forEach((btn) =>
    btn.addEventListener("click", () => {
      $$(".tab").forEach((b) => b.classList.remove("active"));
      $$(".tab-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      $(`#tab-${btn.dataset.tab}`).classList.add("active");
    }),
  );
}

function wireEvents() {
  $("#new-chat-btn").onclick = newChat;
  $("#send-btn").onclick = send;
  $("#composer-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });
  $("#attach-input").addEventListener("change", (e) => {
    const f = e.target.files[0];
    if (f) attachFile(f);
    e.target.value = "";
  });
  $("#save-connection").onclick = saveConnection;
  $("#save-defaults").onclick = saveDefaults;

  $("#new-prompt-btn").onclick = () => openPromptDialog(null);
  $("#new-skill-btn").onclick = openNewSkillDialog;
  $("#upload-skill").addEventListener("change", (e) => {
    const f = e.target.files[0];
    if (f) uploadSkill(f);
    e.target.value = "";
  });

  $("#new-workspace-btn").onclick = () => openWorkspaceDialog(null);
  $("#workspace-select").onchange = (e) => activateWorkspace(e.target.value);

  $("#mcp-from-registry-btn").onclick = openMcpRegistryDialog;
  $("#mcp-custom-btn").onclick = openMcpCustomDialog;
  $("#cli-from-registry-btn").onclick = openCliRegistryDialog;
  $("#cli-custom-btn").onclick = openCliCustomDialog;

  $("#chat-model-select").onchange = () => {};
}

async function init() {
  wireTabs();
  wireEvents();
  await loadConfig();
  await Promise.all([
    refreshModels(),
    loadPrompts(),
    loadSkills(),
    loadWorkspaces(),
    loadMcp(),
    loadCli(),
    loadConversations(),
  ]);
  populateWorkspaceSelect();
  newChat();
}

init().catch((e) => {
  document.body.innerHTML = `<pre style="padding: 30px;">Init failed: ${escape(e.message)}</pre>`;
});
