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
  fileStore: {},
  taskPlan: [],          // current plan items from backend
  convSearchQuery: "",   // conversation search filter
  micListening: false,   // voice input state
  rightRailVisible: false,
  planPaneVisible: false,
  filesPaneVisible: false,
  // last-turn telemetry
  lastTelemetry: null,
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
// Local-download helpers
// ---------------------------------------------------------------------------

function b64ToBlob(b64, mime) {
  const bin = atob(b64);
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  return new Blob([arr], { type: mime || "application/octet-stream" });
}

function storeFile(blob, filename, mime) {
  const url = URL.createObjectURL(blob);
  state.fileStore[filename] = { url, mime: mime || blob.type || "application/octet-stream", filename };
  return url;
}

async function fileToContentEntry(file) {
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

const MATH_STASH_OPEN = "";
const MATH_STASH_CLOSE = "";
const MATH_STASH_RE = new RegExp(MATH_STASH_OPEN + "(\\d+)" + MATH_STASH_CLOSE, "g");

function renderMarkdownWithMath(text) {
  text = String(text || "").replace(/```tool[\s\S]*?```/g, "");
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

  let html = "";
  if (window.marked) {
    html = marked.parse(text, { breaks: true, gfm: true });
  } else {
    html = escape(text).replace(/\n/g, "<br/>");
  }
  if (window.DOMPurify) {
    html = DOMPurify.sanitize(html, { ADD_ATTR: ["target", "rel"] });
  }
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
// Display settings
// ---------------------------------------------------------------------------

function applyDisplaySettings(display) {
  const body = document.body;
  // Font size
  body.classList.remove("font-sm", "font-md", "font-lg", "font-xl");
  body.classList.add("font-" + (display.font_size || "md"));
  // Line height
  body.classList.remove("lh-normal", "lh-relaxed", "lh-loose");
  body.classList.add("lh-" + (display.line_height || "normal"));
  // Dyslexic font
  body.classList.toggle("dyslexic-font", !!display.dyslexic_font);
  // High contrast
  body.classList.toggle("high-contrast", !!display.high_contrast);
  // Reduce motion
  body.classList.toggle("reduce-motion", !!display.reduce_motion);
}

function loadDisplaySettingsUI(display) {
  if (!display) return;
  const fs = $("#cfg-font-size");
  const lh = $("#cfg-line-height");
  const dy = $("#cfg-dyslexic");
  const hc = $("#cfg-high-contrast");
  const rm = $("#cfg-reduce-motion");
  if (fs) fs.value = display.font_size || "md";
  if (lh) lh.value = display.line_height || "normal";
  if (dy) dy.checked = !!display.dyslexic_font;
  if (hc) hc.checked = !!display.high_contrast;
  if (rm) rm.checked = !!display.reduce_motion;
}

async function saveDisplay() {
  const display = {
    font_size: $("#cfg-font-size").value,
    line_height: $("#cfg-line-height").value,
    dyslexic_font: $("#cfg-dyslexic").checked,
    high_contrast: $("#cfg-high-contrast").checked,
    reduce_motion: $("#cfg-reduce-motion").checked,
  };
  await api("/api/config", { method: "POST", json: { display } });
  applyDisplaySettings(display);
  flash("Display settings saved.", "good");
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
  // Mode select
  const ms = $("#mode-select");
  if (ms) ms.value = state.config.chat_mode || "approve-each";
  // Display
  loadDisplaySettingsUI(state.config.display || {});
  applyDisplaySettings(state.config.display || {});
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
  flash("Defaults saved.", "good");
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
  await loadLintWarnings();
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

async function loadLintWarnings() {
  try {
    const lint = await api("/api/lint");
    renderLintSection("skill-lint-warnings", lint.skills || []);
    renderLintSection("mcp-lint-warnings", lint.mcp || []);
    renderLintSection("cli-lint-warnings", lint.cli || []);
  } catch (e) { /* lint endpoint may not exist yet */ }
}

function renderLintSection(elId, warnings) {
  const el = document.getElementById(elId);
  if (!el) return;
  if (!warnings.length) { el.hidden = true; return; }
  el.hidden = false;
  el.innerHTML = `<strong>⚠ Issues found:</strong><ul>${warnings.map((w) => `<li>${escape(w)}</li>`).join("")}</ul>`;
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
        <button data-action="export" data-id="${w.id}" title="Export as .bwui bundle">↓</button>
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
    if (btn.dataset.action === "export") return exportWorkspace(id);
    if (btn.dataset.action === "delete") {
      if (!confirm("Delete this workspace?")) return;
      await api(`/api/workspaces/${id}`, { method: "DELETE" });
      await loadWorkspaces();
      await loadConfig();
    }
  };
}

async function exportWorkspace(id) {
  const w = state.workspaces.find((x) => x.id === id);
  const res = await fetch(`/api/workspaces/${encodeURIComponent(id)}/export`);
  if (!res.ok) { flash("Export failed.", "warn"); return; }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${(w?.name || id).replace(/\s+/g, "_")}.bwui`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  flash("Workspace exported.", "good");
}

async function importWorkspace(file) {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/api/workspaces/import", { method: "POST", body: fd });
  if (!res.ok) { flash("Import failed: " + (await res.text()), "warn"); return; }
  const data = await res.json();
  await loadWorkspaces();
  flash(`Workspace "${data.name}" imported.`, "good");
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
  newChat();
  // Refresh file tree for the new workspace
  if (state.filesPaneVisible) refreshFileTree();
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
    project_root: "",
    mode: "approve-each",
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
      <label>Project root <em>(optional — for file tree &amp; checkpoints)</em>
        <input id="dlg-project-root" type="text" value="${escape(w.project_root || "")}" placeholder="/Users/you/my-project" />
        <small>Absolute path to a folder. The file tree pane will show its contents.</small>
      </label>

      <h3>Skills available in this workspace</h3>
      <div class="check-grid">${skillsList || '<p class="hint">No skills yet.</p>'}</div>

      <h3>MCP servers</h3>
      <div class="check-grid">${mcpList || '<p class="hint">No MCP servers configured. Add them under Tools.</p>'}</div>

      <h3>CLI shortcuts</h3>
      <div class="check-grid">${cliList || '<p class="hint">No CLI shortcuts configured. Add them under Tools.</p>'}</div>

      <h3>Persistent files</h3>
      <p class="hint">Files added here travel with every chat in this workspace.</p>
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
            project_root: $("#dlg-project-root").value.trim() || null,
            active_skills: collectChecked("skill"),
            active_mcp_servers: collectChecked("mcp"),
            active_cli_tools: collectChecked("cli"),
            files: pendingWorkspaceFiles,
          };
          if (!body.name) return;
          const res = await api("/api/workspaces", { method: "POST", json: body });
          await loadWorkspaces();
          if (isNew) await activateWorkspace(res.id);
          closeDialog();
        },
      },
    ],
  });

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
// Conversations: search, pin, tag, fork
// ---------------------------------------------------------------------------

async function loadConversations() {
  const data = await api("/api/conversations");
  state.conversations = data.conversations || [];
  renderConversationList();
}

function renderConversationList() {
  const ul = $("#conversation-list");
  ul.innerHTML = "";
  let convs = state.conversations;
  const q = state.convSearchQuery.trim().toLowerCase();
  if (q) {
    convs = convs.filter((c) =>
      (c.title || "").toLowerCase().includes(q) ||
      (c.tags || []).some((t) => t.toLowerCase().includes(q))
    );
  }
  if (!convs.length) {
    ul.innerHTML = `<p class="hint" style="padding: 8px;">${q ? "No results." : "No chats yet."}</p>`;
    return;
  }
  // Pinned first
  convs = [...convs.filter((c) => c.pinned), ...convs.filter((c) => !c.pinned)];
  for (const c of convs) {
    const li = document.createElement("li");
    if (c.id === state.currentConversationId) li.classList.add("active");
    const tags = (c.tags || []).map((t) => `<span class="tag-badge">${escape(t)}</span>`).join("");
    li.innerHTML = `
      <div class="list-item-title">
        <div>${escape(c.title || "Untitled")}</div>
        <div class="conv-meta">
          ${c.pinned ? '<span class="pin-badge" title="Pinned">📌</span>' : ""}
          ${tags}
        </div>
      </div>
      <div class="list-actions">
        <button data-action="pin" data-id="${c.id}" title="${c.pinned ? "Unpin" : "Pin"}">📌</button>
        <button data-action="fork" data-id="${c.id}" title="Fork this conversation">⎇</button>
        <button data-action="delete" data-id="${c.id}" title="Delete">×</button>
      </div>`;
    li.onclick = (e) => {
      const btn = e.target instanceof Element ? e.target.closest("button") : null;
      if (btn?.dataset.action === "delete") { e.stopPropagation(); deleteConversation(c.id); return; }
      if (btn?.dataset.action === "pin") { e.stopPropagation(); pinConversation(c.id, !c.pinned); return; }
      if (btn?.dataset.action === "fork") { e.stopPropagation(); forkConversation(c.id); return; }
      openConversation(c.id);
    };
    ul.appendChild(li);
  }
}

async function openConversation(id) {
  const conv = await api(`/api/conversations/${id}`);
  state.currentConversationId = id;
  state.messages = conv.messages || [];
  state.taskPlan = conv.task_plan || [];
  renderMessages();
  renderPlan();
  renderConversationList();
}

async function deleteConversation(id) {
  if (!confirm("Delete this conversation?")) return;
  await api(`/api/conversations/${id}`, { method: "DELETE" });
  if (state.currentConversationId === id) newChat();
  await loadConversations();
}

async function pinConversation(id, pin) {
  await api(`/api/conversations/${id}/pin`, { method: "POST", json: { pinned: pin } });
  await loadConversations();
}

async function forkConversation(id) {
  const conv = await api(`/api/conversations/${id}`);
  const msgCount = (conv.messages || []).length;
  const forkAt = msgCount > 1 ? msgCount - 1 : msgCount;
  const forked = await api(`/api/conversations/${id}/fork`, {
    method: "POST",
    json: { fork_at: forkAt },
  });
  await loadConversations();
  await openConversation(forked.id);
  flash("Forked into a new conversation.", "good");
}

function newChat() {
  state.currentConversationId = null;
  state.messages = [];
  state.attachments = [];
  state.taskPlan = [];
  renderMessages();
  renderAttachments();
  renderPlan();
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

  // Per-message action buttons (read-aloud, fork)
  if (m.role === "assistant" && !m._placeholder) {
    const roleEl = tpl.querySelector(".role");
    const acts = document.createElement("div");
    acts.className = "message-actions";
    acts.innerHTML = `<button class="read-aloud-btn" title="Read aloud" aria-label="Read this message aloud">🔊</button>`;
    if (m.telemetry) {
      acts.innerHTML += `<span class="telemetry-badge">${m.telemetry.tokens_in ?? "?"}→${m.telemetry.tokens_out ?? "?"}t · ${m.telemetry.elapsed_ms ?? "?"}ms</span>`;
    }
    roleEl.appendChild(acts);
  }

  const content = tpl.querySelector(".content");
  if (m._placeholder) {
    content.innerHTML =
      '<span class="typing-dots"><span></span><span></span><span></span></span>';
  } else if (m.role === "assistant") {
    // Render subagent cards before the main content
    let mainText = m.content || "";
    content.innerHTML = renderMarkdownWithMath(mainText);
    if (m.subagents?.length) {
      for (const sa of m.subagents) {
        content.appendChild(buildSubagentCard(sa));
      }
    }
  } else if (isToolResult) {
    content.textContent = m.content || "";
  } else {
    content.innerHTML = renderMarkdownWithMath(m.content || "");
  }

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
  const newEl = container.lastElementChild;

  // Wire read-aloud button
  const readBtn = newEl?.querySelector(".read-aloud-btn");
  if (readBtn) {
    readBtn.onclick = () => readAloud(newEl, m.content || "", readBtn);
  }

  if (newEl && m.role === "assistant") renderMathIn(newEl);
  container.scrollTop = container.scrollHeight;
  return newEl;
}

function buildSubagentCard(sa) {
  const card = document.createElement("div");
  card.className = "subagent-card";
  const collapsed = document.createElement("details");
  const summary = document.createElement("summary");
  summary.className = "subagent-header";
  summary.innerHTML = `<span>${escape(sa.kind || "subagent")} subagent result</span>`;
  const body = document.createElement("div");
  body.className = "subagent-body";
  body.textContent = sa.combined || "(no result)";
  collapsed.appendChild(summary);
  collapsed.appendChild(body);
  card.appendChild(collapsed);
  return card;
}

// ---------------------------------------------------------------------------
// Read aloud
// ---------------------------------------------------------------------------

async function readAloud(msgEl, text, btn) {
  if (btn.classList.contains("reading")) {
    // Toggle off: stop any playing audio in this message
    const audio = msgEl.querySelector("audio[data-tts]");
    if (audio) { audio.pause(); audio.remove(); }
    btn.classList.remove("reading");
    btn.title = "Read aloud";
    return;
  }
  btn.classList.add("reading");
  btn.title = "Stop reading";
  try {
    const res = await fetch("/api/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text.slice(0, 4096) }),
    });
    if (!res.ok) throw new Error(await res.text());
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const audio = document.createElement("audio");
    audio.dataset.tts = "1";
    audio.autoplay = true;
    audio.src = url;
    audio.onended = () => {
      audio.remove();
      URL.revokeObjectURL(url);
      btn.classList.remove("reading");
      btn.title = "Read aloud";
    };
    msgEl.querySelector(".content").appendChild(audio);
  } catch (e) {
    btn.classList.remove("reading");
    flash("Read aloud failed: " + e.message, "warn");
  }
}

// ---------------------------------------------------------------------------
// Composer / attachments
// ---------------------------------------------------------------------------

async function attachFile(file) {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/api/upload", { method: "POST", body: fd });
  if (!res.ok) { flash("Upload failed.", "warn"); return; }
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
    span.innerHTML = `${escape(a.filename)} <button data-i="${i}" title="Remove" aria-label="Remove ${escape(a.filename)}">×</button>`;
    span.querySelector("button").onclick = () => {
      state.attachments.splice(i, 1);
      renderAttachments();
    };
    wrap.appendChild(span);
  });
}

// ---------------------------------------------------------------------------
// Voice input (SpeechRecognition)
// ---------------------------------------------------------------------------

let recognition = null;

function initMic() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    const btn = $("#mic-btn");
    if (btn) { btn.hidden = true; }
    return;
  }
  recognition = new SpeechRecognition();
  recognition.continuous = false;
  recognition.interimResults = true;
  recognition.lang = navigator.language || "en-US";

  recognition.onresult = (e) => {
    let transcript = "";
    for (let i = e.resultIndex; i < e.results.length; i++) {
      transcript += e.results[i][0].transcript;
    }
    const input = $("#composer-input");
    if (input) input.value = transcript;
  };

  recognition.onend = () => {
    state.micListening = false;
    const btn = $("#mic-btn");
    if (btn) { btn.classList.remove("listening"); btn.setAttribute("aria-pressed", "false"); }
  };

  recognition.onerror = (e) => {
    state.micListening = false;
    const btn = $("#mic-btn");
    if (btn) { btn.classList.remove("listening"); btn.setAttribute("aria-pressed", "false"); }
    if (e.error !== "no-speech") flash("Microphone error: " + e.error, "warn");
  };
}

function toggleMic() {
  if (!recognition) {
    // Fallback: proxy to OpenWebUI transcription
    flash("Voice input is not supported in this browser.", "warn");
    return;
  }
  const btn = $("#mic-btn");
  if (state.micListening) {
    recognition.stop();
  } else {
    recognition.start();
    state.micListening = true;
    if (btn) { btn.classList.add("listening"); btn.setAttribute("aria-pressed", "true"); }
  }
}

// ---------------------------------------------------------------------------
// Task plan pane
// ---------------------------------------------------------------------------

function renderPlan() {
  const list = $("#plan-list");
  if (!list) return;
  list.innerHTML = "";
  if (!state.taskPlan.length) {
    list.innerHTML = `<li class="plan-item pending"><span class="plan-item-text" style="font-style:italic;color:var(--ink-faint)">No plan yet.</span></li>`;
    return;
  }
  const icons = { pending: "○", in_progress: "◉", done: "✓", blocked: "⚠" };
  for (const item of state.taskPlan) {
    const li = document.createElement("li");
    li.className = `plan-item ${item.status || "pending"}`;
    li.innerHTML = `
      <span class="plan-item-icon" aria-label="${escape(item.status || "pending")}">${icons[item.status] || "○"}</span>
      <span class="plan-item-text">
        ${escape(item.title || "")}
        ${item.note ? `<div class="plan-item-note">${escape(item.note)}</div>` : ""}
      </span>`;
    list.appendChild(li);
  }
}

function setRightRailVisible(show) {
  state.rightRailVisible = show;
  const rail = $("#right-rail");
  if (!rail) return;
  rail.hidden = !show;
}

function setPlanPaneVisible(show) {
  state.planPaneVisible = show;
  const pane = $("#plan-pane");
  if (!pane) return;
  pane.hidden = !show;
  const btn = $("#toggle-plan-btn");
  if (btn) btn.setAttribute("aria-pressed", show ? "true" : "false");
  updateRightRailVisibility();
}

function setFilesPaneVisible(show) {
  state.filesPaneVisible = show;
  const pane = $("#files-pane");
  if (!pane) return;
  pane.hidden = !show;
  const btn = $("#toggle-files-btn");
  if (btn) btn.setAttribute("aria-pressed", show ? "true" : "false");
  if (show) refreshFileTree();
  updateRightRailVisibility();
}

function updateRightRailVisibility() {
  setRightRailVisible(state.planPaneVisible || state.filesPaneVisible);
}

// ---------------------------------------------------------------------------
// File tree pane
// ---------------------------------------------------------------------------

async function refreshFileTree() {
  const hint = $("#file-tree-hint");
  const ul = $("#file-tree");
  if (hint) hint.hidden = true;
  try {
    const data = await api("/api/project/tree");
    renderFileTree(ul, data.entries || []);
  } catch (e) {
    if (ul) ul.innerHTML = `<li style="color:var(--ink-faint);font-size:12px;padding:6px 8px;">Could not load file tree.</li>`;
  }
}

function renderFileTree(ul, entries) {
  ul.innerHTML = "";
  for (const entry of entries) {
    const li = document.createElement("li");
    if (entry.type === "dir") {
      li.innerHTML = `<details><summary class="file-tree-item dir"><span class="file-tree-icon">📁</span>${escape(entry.name)}</summary><ul class="file-tree" data-path="${escape(entry.path)}"></ul></details>`;
      const sub = li.querySelector("ul");
      const details = li.querySelector("details");
      details.addEventListener("toggle", async () => {
        if (details.open && sub.children.length === 0) {
          try {
            const ws = state.workspaces.find((w) => w.id === state.config?.active_workspace_id);
            const data = await api(`/api/project/tree?path=${encodeURIComponent(entry.path)}`);
            renderFileTree(sub, data.entries || []);
          } catch (e) { /* silent */ }
        }
      });
    } else {
      li.innerHTML = `<div class="file-tree-item" role="button" tabindex="0" data-path="${escape(entry.path)}"><span class="file-tree-icon">📄</span>${escape(entry.name)}</div>`;
      li.querySelector(".file-tree-item").onclick = () => openProjectFile(entry.path);
      li.querySelector(".file-tree-item").onkeydown = (e) => {
        if (e.key === "Enter" || e.key === " ") openProjectFile(entry.path);
      };
    }
    ul.appendChild(li);
  }
}

async function openProjectFile(path) {
  const ws = state.workspaces.find((w) => w.id === state.config?.active_workspace_id);
  try {
    const data = await api(`/api/project/file?workspace_id=${encodeURIComponent(ws?.id || "")}&path=${encodeURIComponent(path)}`);
    const name = path.split("/").pop() || path;
    showDialog({
      title: name,
      wide: true,
      body: `<pre style="max-height:400px;overflow:auto;">${escape(data.content || "")}</pre>`,
      actions: [{ label: "Close", action: "cancel" }],
    });
  } catch (e) {
    flash("Could not open file.", "warn");
  }
}

// ---------------------------------------------------------------------------
// Approval dialog (shell + write) — with explain expander + trust session
// ---------------------------------------------------------------------------

async function askApproval(req) {
  // write_file: use the diff modal for a proper before/after view
  if (req.tool === "write_file") {
    return new Promise(async (resolve) => {
      const modal = document.getElementById("diff-modal");
      const pathEl = document.getElementById("diff-modal-path");
      const contentEl = document.getElementById("diff-modal-content");
      const acceptBtn = document.getElementById("diff-accept-btn");
      const rejectBtn = document.getElementById("diff-reject-btn");
      if (!modal || !acceptBtn || !rejectBtn) {
        resolve({ approved: confirm(`Save file "${req.filename}"?`) });
        return;
      }
      if (pathEl) pathEl.textContent = req.dest_path || req.filename;

      // Try to load existing content for diff
      let oldHtml = "<em>(new file)</em>";
      try {
        const existing = await api(`/api/project/file?path=${encodeURIComponent(req.filename)}`);
        if (!existing.is_binary) {
          oldHtml = `<pre>${escape(existing.content.slice(0, 3000))}</pre>`;
        }
      } catch { /* file doesn't exist yet */ }

      if (contentEl) {
        contentEl.innerHTML = `
          <div class="diff-columns">
            <div class="diff-col"><strong>Before</strong>${oldHtml}</div>
            <div class="diff-col diff-col-new"><strong>After (${req.byte_count} bytes)</strong><pre>${escape(req.preview || "")}</pre></div>
          </div>`;
      }
      modal.hidden = false;
      acceptBtn.focus();
      const cleanup = () => { modal.hidden = true; };
      acceptBtn.onclick = () => { cleanup(); resolve({ approved: true }); };
      rejectBtn.onclick = () => { cleanup(); resolve({ approved: false }); };
    });
  }

  return new Promise((resolve) => {
    let title, body;
    if (req.tool === "execute_shell") {
      title = `Run a ${req.shell} command?`;
      body = `
        <div class="danger-banner"><strong>Caution.</strong> The assistant wants to run a command on your computer. Read it carefully before approving.</div>
        ${req.reason ? `<p><b>Why:</b> ${escape(req.reason)}</p>` : ""}
        <p><b>Command:</b></p>
        <pre>${escape(req.command)}</pre>
        <details class="explain-expander" id="explain-details">
          <summary>Explain this in plain English</summary>
          <div class="explain-body" id="explain-body"><span class="spinner"></span> Loading explanation…</div>
        </details>
        <label class="trust-session-wrap">
          <input type="checkbox" id="trust-session-cb" />
          Trust this command for the rest of the session (won't ask again)
        </label>
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
            resolve({ approved: false });
          },
        },
        {
          label: "Approve",
          primary: true,
          action: async () => {
            const trustCb = document.getElementById("trust-session-cb");
            const trustSession = trustCb ? trustCb.checked : false;
            if (trustSession && req.command) {
              try {
                await api("/api/session/trust", {
                  method: "POST",
                  json: { command: req.command },
                });
              } catch (e) { /* non-critical */ }
            }
            closeDialog();
            resolve({ approved: true, trust_session: trustSession, command: req.command });
          },
        },
      ],
    });

    // Wire explain-details toggle
    setTimeout(() => {
      const det = document.getElementById("explain-details");
      if (!det || req.tool !== "execute_shell") return;
      let explained = false;
      det.addEventListener("toggle", async () => {
        if (!det.open || explained) return;
        explained = true;
        const bodyEl = document.getElementById("explain-body");
        try {
          const data = await api("/api/explain-command", {
            method: "POST",
            json: { command: req.command },
          });
          if (bodyEl) bodyEl.textContent = data.explanation || "No explanation available.";
        } catch (e) {
          if (bodyEl) bodyEl.textContent = "Could not fetch explanation.";
        }
      });
    }, 50);
  });
}

// ---------------------------------------------------------------------------
// Send + SSE chat loop
// ---------------------------------------------------------------------------

async function send() {
  if (state.busy) return;
  const text = $("#composer-input").value.trim();
  if (!text && !state.attachments.length) return;

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

  const chatMode = $("#mode-select")?.value || state.config?.chat_mode || "approve-each";

  state.busy = true;
  const sendBtn = $("#send-btn");
  sendBtn.disabled = true;
  sendBtn.innerHTML = '<span class="spinner"></span>';

  const placeholder = { role: "assistant", content: "", _placeholder: true };
  state.messages.push(placeholder);
  appendMessage(placeholder);
  const placeholderEl = $("#messages").lastElementChild;

  // Track active subagent cards for current turn
  const subagentSummaries = [];

  try {
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
        mode: chatMode,
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
        const telemetry = data.telemetry || null;
        const msg = { role: "assistant", content: data.text, telemetry, subagents: subagentSummaries.slice() };
        state.messages.push(msg);
        appendMessage(msg);
        if (telemetry) showTelemetryLine(telemetry);
        return;
      }
      if (event === "task_plan") {
        state.taskPlan = data.items || [];
        renderPlan();
        // Auto-show the plan pane when we get a plan
        if (state.taskPlan.length && !state.planPaneVisible) setPlanPaneVisible(true);
        return;
      }
      if (event === "subagent_start") {
        const sysMsg = { role: "system-event", content: `↪ Starting ${data.count} ${data.kind} subagent${data.count !== 1 ? "s" : ""}…` };
        state.messages.push(sysMsg);
        appendMessage(sysMsg);
        return;
      }
      if (event === "subagent_result") {
        subagentSummaries.push({ kind: data.kind, combined: data.combined });
        const sysMsg = { role: "system-event", content: `✓ Subagent (${data.kind}, ${data.count} result${data.count !== 1 ? "s" : ""}) done.` };
        state.messages.push(sysMsg);
        appendMessage(sysMsg);
        return;
      }
      if (event === "approval_request") {
        const result = await askApproval(data);
        await api("/api/approve", {
          method: "POST",
          json: {
            approval_id: data.approval_id,
            approved: result.approved !== undefined ? result.approved : result,
            trust_session: result.trust_session,
            command: result.command,
          },
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
        if (Array.isArray(data.messages)) {
          state.messages = data.messages;
          renderMessages();
        }
        if (data.task_plan) {
          state.taskPlan = data.task_plan;
          renderPlan();
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

function showTelemetryLine(t) {
  const el = $("#telemetry-line");
  if (!el) return;
  el.hidden = false;
  el.textContent = `${t.tokens_in ?? "?"}→${t.tokens_out ?? "?"}t · ${t.elapsed_ms ?? "?"}ms`;
  clearTimeout(el._timer);
  el._timer = setTimeout(() => { el.hidden = true; }, 8000);
}

async function handleToolResult(data) {
  const r = data.result || {};

  if (r.data_b64 && r.filename) {
    const mime = r.mime || "application/octet-stream";
    const blob = b64ToBlob(r.data_b64, mime);
    const url = storeFile(blob, r.filename, mime);
    const label =
      mime.startsWith("image/") ? "Image" :
      mime.startsWith("audio/") ? "Audio" :
      mime.startsWith("video/") ? "Video" : "File";
    let content = `${label} ready: ${r.filename}`;
    if (r.write_error) content += `\n⚠️ On-disk write failed: ${r.write_error}`;
    const sysMsg = {
      role: "tool",
      content,
      attachments: r.write_error ? [] : [{ url, content_type: mime, filename: r.filename }],
    };
    state.messages.push(sysMsg);
    appendMessage(sysMsg);
    // Refresh file tree only when the write actually succeeded
    if (state.filesPaneVisible && !r.write_error) refreshFileTree();
    return;
  }

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
// File-request dialog
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
// Generic dialog (with focus trap)
// ---------------------------------------------------------------------------

function showDialog({ title, body, actions, wide }) {
  closeDialog();
  const root = $("#dialog-root");
  const wrap = document.createElement("div");
  wrap.className = "dialog-backdrop";
  wrap.setAttribute("role", "alertdialog");
  wrap.setAttribute("aria-modal", "true");
  wrap.setAttribute("aria-label", title);
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
  // Focus first button
  const firstBtn = wrap.querySelector("button");
  if (firstBtn) firstBtn.focus();
  // Trap focus inside dialog
  wrap.addEventListener("keydown", trapFocus);
}

function trapFocus(e) {
  if (e.key !== "Tab") return;
  const focusable = Array.from(e.currentTarget.querySelectorAll(
    'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
  )).filter((el) => !el.disabled && el.offsetParent !== null);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (e.shiftKey) {
    if (document.activeElement === first) { last.focus(); e.preventDefault(); }
  } else {
    if (document.activeElement === last) { first.focus(); e.preventDefault(); }
  }
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
  t.offsetHeight;
  t.classList.add("visible");
  setTimeout(() => {
    t.classList.remove("visible");
    setTimeout(() => t.remove(), 250);
  }, 3500);
}

// ---------------------------------------------------------------------------
// Onboarding wizard
// ---------------------------------------------------------------------------

async function checkOnboarding() {
  const cfg = state.config;
  if (cfg?.onboarding_done) return;
  // Show wizard
  const overlay = $("#onboarding-overlay");
  if (overlay) overlay.hidden = false;
  // Load use-case templates
  try {
    const data = await api("/api/onboarding/templates");
    renderUseCaseGrid(data.templates || []);
  } catch (e) { /* endpoint may not exist */ }
}

function renderUseCaseGrid(templates) {
  const grid = $("#use-case-grid");
  if (!grid) return;
  grid.innerHTML = "";
  const icons = { grading: "📝", research: "🔬", "course-prep": "📚", writing: "✍️", coding: "💻" };
  for (const t of templates) {
    const card = document.createElement("div");
    card.className = "use-case-card";
    card.setAttribute("role", "option");
    card.setAttribute("tabindex", "0");
    card.setAttribute("aria-selected", "false");
    card.dataset.id = t.id;
    card.innerHTML = `<span class="use-case-icon">${icons[t.id] || "📋"}</span>${escape(t.name)}`;
    card.onclick = () => {
      grid.querySelectorAll(".use-case-card").forEach((c) => {
        c.classList.remove("selected");
        c.setAttribute("aria-selected", "false");
      });
      card.classList.add("selected");
      card.setAttribute("aria-selected", "true");
      const btn = $("#ob-usecase-btn");
      if (btn) { btn.disabled = false; btn.dataset.useCase = t.id; }
    };
    card.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") card.click(); };
    grid.appendChild(card);
  }
}

async function onboardingConnect() {
  const url = $("#ob-url")?.value.trim();
  const key = $("#ob-key")?.value.trim();
  if (!url || !key) { flash("Enter a URL and API key.", "warn"); return; }
  const status = $("#ob-status");
  if (status) status.textContent = "Testing…";
  try {
    const result = await api("/api/config", { method: "POST", json: { base_url: url, api_key: key } });
    if (!result.api_profile_label) {
      if (status) { status.textContent = "Could not connect. Check the URL and key."; status.className = "status-line warn"; }
      return;
    }
    state.config = result;
    if (status) { status.textContent = `Connected via ${result.api_profile_label}.`; status.className = "status-line good"; }
    await refreshModels();
    // Move to step 2
    setTimeout(() => {
      $("#onboarding-step-1").hidden = true;
      $("#onboarding-step-2").hidden = false;
    }, 600);
  } catch (e) {
    if (status) { status.textContent = "Error: " + e.message; status.className = "status-line warn"; }
  }
}

async function onboardingComplete(useCaseId) {
  try {
    const data = await api("/api/onboarding/complete", { method: "POST", json: { template_id: useCaseId } });
    const msg = $("#ob-done-msg");
    if (msg) msg.textContent = `Your "${data.workspace_name || useCaseId}" workspace has been created. Click "Start chatting" to begin.`;
    $("#onboarding-step-2").hidden = true;
    $("#onboarding-step-3").hidden = false;
    await loadConfig();
    await loadWorkspaces();
    await loadPrompts();
    await loadSkills();
  } catch (e) {
    flash("Onboarding error: " + e.message, "warn");
  }
}

function onboardingFinish() {
  const overlay = $("#onboarding-overlay");
  if (overlay) overlay.hidden = true;
  flash("Welcome to BetterWebUI!", "good");
}

// ---------------------------------------------------------------------------
// Keyboard shortcuts
// ---------------------------------------------------------------------------

let _gKeyPending = false;
let _gKeyTimer = null;

function handleGlobalKey(e) {
  // Don't intercept when typing in inputs
  const tag = document.activeElement?.tagName?.toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select") {
    // Only intercept Ctrl+Enter in textarea
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && tag === "textarea") {
      e.preventDefault();
      send();
    }
    return;
  }

  // Escape closes dialogs and modals
  if (e.key === "Escape") {
    closeDialog();
    const sheet = $("#shortcut-sheet"); if (sheet) sheet.hidden = true;
    const diff = $("#diff-modal"); if (diff && !diff.hidden) diff.hidden = true;
    const onboarding = $("#onboarding-overlay"); if (onboarding && !onboarding.hidden) onboarding.hidden = true;
    // Return focus to the composer for keyboard users
    const composer = $("#composer-input");
    if (composer) composer.focus();
    return;
  }

  // ? opens shortcut sheet
  if (e.key === "?") {
    const sheet = $("#shortcut-sheet");
    if (sheet) sheet.hidden = !sheet.hidden;
    return;
  }

  // N = new chat
  if (e.key === "n" || e.key === "N") { newChat(); return; }

  // P = toggle plan pane
  if (e.key === "p" || e.key === "P") { setPlanPaneVisible(!state.planPaneVisible); return; }

  // F = toggle files pane
  if (e.key === "f" || e.key === "F") { setFilesPaneVisible(!state.filesPaneVisible); return; }

  // G-chord navigation
  if (e.key === "g" || e.key === "G") {
    _gKeyPending = true;
    clearTimeout(_gKeyTimer);
    _gKeyTimer = setTimeout(() => { _gKeyPending = false; }, 1000);
    return;
  }
  if (_gKeyPending) {
    _gKeyPending = false;
    clearTimeout(_gKeyTimer);
    const chordMap = { c: "chats", w: "workspaces", s: "skills", t: "tools", x: "settings", p: "prompts" };
    const target = chordMap[e.key.toLowerCase()];
    if (target) switchTab(target);
    return;
  }
}

function switchTab(tabName) {
  $$(".tab").forEach((b) => {
    const active = b.dataset.tab === tabName;
    b.classList.toggle("active", active);
    b.setAttribute("aria-selected", active ? "true" : "false");
  });
  $$(".tab-panel").forEach((p) => p.classList.remove("active"));
  const panel = $(`#tab-${tabName}`);
  if (panel) panel.classList.add("active");
}

// ---------------------------------------------------------------------------
// Tabs and wiring
// ---------------------------------------------------------------------------

function wireTabs() {
  $$(".tab").forEach((btn) =>
    btn.addEventListener("click", () => {
      $$(".tab").forEach((b) => {
        b.classList.remove("active");
        b.setAttribute("aria-selected", "false");
      });
      $$(".tab-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      btn.setAttribute("aria-selected", "true");
      $(`#tab-${btn.dataset.tab}`).classList.add("active");
    }),
  );
}

function wireEvents() {
  // Core chat
  $("#new-chat-btn").onclick = newChat;
  $("#send-btn").onclick = send;
  $("#composer-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      send();
    }
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      send();
    }
  });
  $("#attach-input").addEventListener("change", (e) => {
    const f = e.target.files[0];
    if (f) attachFile(f);
    e.target.value = "";
  });

  // Mic
  $("#mic-btn")?.addEventListener("click", toggleMic);

  // Settings
  $("#save-connection").onclick = saveConnection;
  $("#save-defaults").onclick = saveDefaults;
  $("#save-display")?.addEventListener("click", saveDisplay);

  // Prompts
  $("#new-prompt-btn").onclick = () => openPromptDialog(null);

  // Skills
  $("#new-skill-btn").onclick = openNewSkillDialog;
  $("#upload-skill").addEventListener("change", (e) => {
    const f = e.target.files[0];
    if (f) uploadSkill(f);
    e.target.value = "";
  });

  // Workspaces
  $("#new-workspace-btn").onclick = () => openWorkspaceDialog(null);
  $("#workspace-select").onchange = (e) => activateWorkspace(e.target.value);
  $("#import-workspace-btn")?.addEventListener("click", () => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".bwui";
    input.onchange = (e) => { if (e.target.files[0]) importWorkspace(e.target.files[0]); };
    input.click();
  });

  // MCP / CLI
  $("#mcp-from-registry-btn").onclick = openMcpRegistryDialog;
  $("#mcp-custom-btn").onclick = openMcpCustomDialog;
  $("#cli-from-registry-btn").onclick = openCliRegistryDialog;
  $("#cli-custom-btn").onclick = openCliCustomDialog;

  // Right-rail toggles
  $("#toggle-plan-btn")?.addEventListener("click", () => setPlanPaneVisible(!state.planPaneVisible));
  $("#toggle-files-btn")?.addEventListener("click", () => setFilesPaneVisible(!state.filesPaneVisible));
  $("#plan-pane-close")?.addEventListener("click", () => setPlanPaneVisible(false));
  $("#files-pane-close")?.addEventListener("click", () => setFilesPaneVisible(false));

  // Conversation search
  $("#search-toggle-btn")?.addEventListener("click", () => {
    const wrap = $("#conv-search-wrap");
    if (!wrap) return;
    wrap.hidden = !wrap.hidden;
    if (!wrap.hidden) $("#conv-search")?.focus();
    else { state.convSearchQuery = ""; renderConversationList(); }
  });
  $("#conv-search")?.addEventListener("input", (e) => {
    state.convSearchQuery = e.target.value;
    renderConversationList();
  });

  // Mode select — persist per-workspace when a workspace is active, otherwise globally
  $("#mode-select")?.addEventListener("change", async (e) => {
    const mode = e.target.value;
    const activeWsId = state.config?.active_workspace_id;
    try {
      if (activeWsId) {
        const ws = await api(`/api/workspaces/${activeWsId}`);
        await api("/api/workspaces", { method: "POST", json: { ...ws, mode } });
      } else {
        await api("/api/config", { method: "POST", json: { chat_mode: mode } });
      }
    } catch (_) { /* non-critical */ }
  });

  // Keyboard shortcuts modal
  $("#shortcut-help-btn")?.addEventListener("click", () => {
    const sheet = $("#shortcut-sheet");
    if (sheet) sheet.hidden = false;
  });
  $("#shortcut-sheet")?.querySelector(".modal-close")?.addEventListener("click", () => {
    $("#shortcut-sheet").hidden = true;
  });

  // Global keyboard shortcuts
  document.addEventListener("keydown", handleGlobalKey);

  // Onboarding wizard buttons
  $("#ob-connect-btn")?.addEventListener("click", onboardingConnect);
  $("#ob-back-btn")?.addEventListener("click", () => {
    $("#onboarding-step-2").hidden = true;
    $("#onboarding-step-1").hidden = false;
  });
  $("#ob-usecase-btn")?.addEventListener("click", (e) => {
    const id = e.target.dataset.useCase;
    if (id) onboardingComplete(id);
  });
  $("#ob-finish-btn")?.addEventListener("click", onboardingFinish);
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

async function init() {
  wireTabs();
  wireEvents();
  initMic();
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
  // Check if onboarding is needed
  await checkOnboarding();
}

init().catch((e) => {
  document.body.innerHTML = `<pre style="padding: 30px;">Init failed: ${escape(e.message)}\n\n${e.stack || ""}</pre>`;
});
