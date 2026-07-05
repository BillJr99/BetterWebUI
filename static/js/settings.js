// settings.js — Display settings, the Settings tab (connection/defaults/lint), models, prompts, and skills.
// Split out of the former static/app.js (Phase 3); logic unchanged.
import { api } from "./api.js";
import { closeDialog, flash, showDialog } from "./dialogs.js";
import { $, state } from "./state.js";

// Display settings
// ---------------------------------------------------------------------------

export const _FONT_SIZE_VALUES = ["sm", "md", "lg", "xl"];
export const _LINE_HEIGHT_VALUES = ["normal", "relaxed", "loose"];

export function applyDisplaySettings(display) {
  const body = document.body;
  // Validate persisted values against an allowlist; classList tokens cannot
  // contain whitespace, so a corrupted config value would otherwise throw.
  const fontSize = _FONT_SIZE_VALUES.includes(display.font_size) ? display.font_size : "md";
  const lineHeight = _LINE_HEIGHT_VALUES.includes(display.line_height) ? display.line_height : "normal";
  // Font size
  body.classList.remove("font-sm", "font-md", "font-lg", "font-xl");
  body.classList.add("font-" + fontSize);
  // Line height
  body.classList.remove("lh-normal", "lh-relaxed", "lh-loose");
  body.classList.add("lh-" + lineHeight);
  // Dyslexic font
  body.classList.toggle("dyslexic-font", !!display.dyslexic_font);
  // High contrast
  body.classList.toggle("high-contrast", !!display.high_contrast);
  // Reduce motion
  body.classList.toggle("reduce-motion", !!display.reduce_motion);
}

export function loadDisplaySettingsUI(display) {
  if (!display) return;
  const fs = $("#cfg-font-size");
  const lh = $("#cfg-line-height");
  const dy = $("#cfg-dyslexic");
  const hc = $("#cfg-high-contrast");
  const rm = $("#cfg-reduce-motion");
  // Clamp persisted values to the supported set so the select UI doesn't
  // end up blank when config holds an older/unexpected value while
  // applyDisplaySettings has already coerced the applied class to the default.
  const fontSize = _FONT_SIZE_VALUES.includes(display.font_size) ? display.font_size : "md";
  const lineHeight = _LINE_HEIGHT_VALUES.includes(display.line_height) ? display.line_height : "normal";
  if (fs) fs.value = fontSize;
  if (lh) lh.value = lineHeight;
  if (dy) dy.checked = !!display.dyslexic_font;
  if (hc) hc.checked = !!display.high_contrast;
  if (rm) rm.checked = !!display.reduce_motion;
}

export async function saveDisplay() {
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

export async function loadConfig() {
  state.config = await api("/api/config");
  $("#cfg-base-url").value = state.config.base_url || "";
  $("#cfg-api-key-status").textContent = state.config.api_key_set
    ? "API key is set (enter a new one to replace it)"
    : "Not set";
  $("#cfg-image-model").value = state.config.image_model || "";
  $("#cfg-tts-voice").value = state.config.tts_voice || "alloy";
  // Snap the consensus value to one of the three select options (1, 3, or 5).
  const cr = state.config.consensus_runs ?? 1;
  const snap = cr <= 1 ? "1" : (cr <= 3 ? "3" : "5");
  $("#cfg-consensus-runs").value = snap;
  $("#cfg-shell-enabled").checked = state.config.shell_enabled !== false;
  const ws = state.config.web_search || {};
  const wsProvider = $("#cfg-websearch-provider"); if (wsProvider) wsProvider.value = ws.provider || "";
  const wsCustomWrap = $("#cfg-websearch-custom-wrap");
  if (wsCustomWrap) wsCustomWrap.hidden = (ws.provider !== "custom");
  const wsCustom = $("#cfg-websearch-custom"); if (wsCustom) wsCustom.value = ws.custom_url || "";
  const ver = state.config.verification || {};
  const verMode = $("#cfg-verification-mode"); if (verMode) verMode.value = ver.mode || "validators_only";
  const verRetries = $("#cfg-verification-retries"); if (verRetries) verRetries.value = ver.retries ?? 1;
  // Mode select: prefer the active workspace's mode (if any) so the
  // workspace-scoped setting actually takes effect; fall back to the
  // global config mode, then to "approve-each".
  const ms = $("#mode-select");
  if (ms) {
    const activeWsId = state.config.active_workspace_id;
    const activeWs = activeWsId && state.workspaces
      ? state.workspaces.find((w) => w.id === activeWsId)
      : null;
    ms.value = (activeWs && activeWs.mode) || state.config.chat_mode || "approve-each";
  }
  // Display
  loadDisplaySettingsUI(state.config.display || {});
  applyDisplaySettings(state.config.display || {});
  renderConnectionStatus(state.config);
  await loadHealth();
  await loadServicesStatus();
}

export function renderConnectionStatus(cfg) {
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

export async function loadHealth() {
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

export async function loadServicesStatus() {
  try {
    const s = await api("/api/services/status");
    const map = s.services || {};
    ["clk", "autogui", "osso"].forEach((name) => {
      const el = $(`#svc-${name}-enabled`);
      if (el) el.checked = map[name]?.enabled !== false;
    });
  } catch (_) { /* services module not running — ignore */ }
}

export async function toggleService(name, enabled) {
  const statusEl = $("#services-toggle-status");
  try {
    await api(`/api/services/${name}/${enabled ? "enable" : "disable"}`, { method: "POST" });
    if (statusEl) {
      statusEl.textContent = `${name} ${enabled ? "enabled" : "disabled"}.`;
      statusEl.className = "status-line good";
      setTimeout(() => { statusEl.textContent = ""; statusEl.className = "status-line"; }, 2500);
    }
  } catch (e) {
    if (statusEl) {
      statusEl.textContent = `Failed to update ${name}: ${e.message}`;
      statusEl.className = "status-line bad";
    }
    // Revert the checkbox
    const el = $(`#svc-${name}-enabled`);
    if (el) el.checked = !enabled;
  }
}

export async function saveConnection() {
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

export async function saveDefaults() {
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

export async function saveWebSearch() {
  const provider = $("#cfg-websearch-provider").value || "";
  const apiKey = $("#cfg-websearch-key").value.trim();
  const customUrl = $("#cfg-websearch-custom").value.trim();
  const patch = {
    web_search: {
      provider,
      ...(apiKey ? { api_key: apiKey } : {}),
      custom_url: customUrl,
    },
  };
  const el = $("#websearch-status");
  try {
    await api("/api/config", { method: "POST", json: patch });
    if (el) {
      el.textContent = provider ? `Saved. Web search will use ${provider}.` : "Saved. Web search is disabled.";
      el.className = "status-line good";
    }
    $("#cfg-websearch-key").value = "";
    await loadConfig();
  } catch (e) {
    if (el) {
      el.textContent = friendlyError(e, "saving web search settings");
      el.className = "status-line bad";
    }
  }
}

export async function saveVerification() {
  const mode = $("#cfg-verification-mode").value || "validators_only";
  const retries = Math.min(3, Math.max(0, parseInt($("#cfg-verification-retries").value, 10) || 1));
  const enabled = (mode !== "off");
  const patch = {
    verification: {
      ...(state.config.verification || {}),
      enabled,
      mode,
      retries,
    },
  };
  const el = $("#verification-status");
  try {
    await api("/api/config", { method: "POST", json: patch });
    if (el) {
      el.textContent = "Verification settings saved.";
      el.className = "status-line good";
    }
    await loadConfig();
  } catch (e) {
    if (el) {
      el.textContent = friendlyError(e, "saving verification settings");
      el.className = "status-line bad";
    }
  }
}

// ---------------------------------------------------------------------------
// Models
// ---------------------------------------------------------------------------

export async function refreshModels() {
  const data = await api("/api/models");
  state.models = data.models || [];
  if (data.error) flash(data.error, "warn");
  populateModelSelects();
}

export function modelOptionLabel(m) {
  if (!m.name || m.name === m.id) return m.id;
  return `${m.name} (${m.id})`;
}

export function populateModelSelects() {
  const optionHtml =
    '<option value="">— pick a model —</option>' +
    state.models.map((m) => `<option value="${escape(m.id)}">${escape(modelOptionLabel(m))}</option>`).join("");
  $("#cfg-default-model").innerHTML = optionHtml;
  $("#cfg-default-model").value = state.config?.default_model || "";
  $("#chat-model-select").innerHTML = optionHtml;
  $("#chat-model-select").value = state.config?.default_model || "";
}

// ---------------------------------------------------------------------------
// Prompts
// ---------------------------------------------------------------------------

export async function loadPrompts() {
  const data = await api("/api/system-prompts");
  state.prompts = data.prompts || [];
  renderPromptList();
}

export function renderPromptList() {
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

export function openPromptDialog(prompt) {
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

export async function deletePrompt(id) {
  if (!confirm("Delete this prompt?")) return;
  await api(`/api/system-prompts/${id}`, { method: "DELETE" });
  await loadPrompts();
}

// ---------------------------------------------------------------------------
// Skills
// ---------------------------------------------------------------------------

export async function loadSkills() {
  const data = await api("/api/skills");
  state.skills = data.skills || [];
  renderSkillList();
  await loadLintWarnings();
}

export function renderSkillList() {
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

export async function loadLintWarnings() {
  try {
    const lint = await api("/api/lint");
    renderLintSection("skill-lint-warnings", lint.skills || []);
    renderLintSection("mcp-lint-warnings", lint.mcp || []);
    renderLintSection("cli-lint-warnings", lint.cli || []);
  } catch (e) { /* lint endpoint may not exist yet */ }
}

export function renderLintSection(elId, warnings) {
  const el = document.getElementById(elId);
  if (!el) return;
  if (!warnings.length) { el.hidden = true; return; }
  el.hidden = false;
  el.innerHTML = `<strong>⚠ Issues found:</strong><ul>${warnings.map((w) => `<li>${escape(w)}</li>`).join("")}</ul>`;
}

export function openNewSkillDialog() {
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

export async function uploadSkill(file) {
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
