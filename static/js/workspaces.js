// workspaces.js — Workspace CRUD, export/import, and the workspace switcher.
// Split out of the former static/app.js (Phase 3); logic unchanged.
import { api } from "./api.js";
import { newChat } from "./conversations.js";
import { closeDialog, flash, showDialog } from "./dialogs.js";
import { refreshFileTree } from "./panels.js";
import { loadConfig, modelOptionLabel } from "./settings.js";
import { $, state } from "./state.js";

// Workspaces
// ---------------------------------------------------------------------------

export async function loadWorkspaces() {
  const data = await api("/api/workspaces");
  state.workspaces = data.workspaces || [];
  renderWorkspaceList();
  populateWorkspaceSelect();
}

export function renderWorkspaceList() {
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
        <button data-action="activate" data-id="${w.id}" aria-label="Use workspace ${escape(w.name)}">Use</button>
        <button data-action="edit" data-id="${w.id}" aria-label="Edit workspace ${escape(w.name)}">Edit</button>
        <button data-action="export" data-id="${w.id}" title="Export as .bwui bundle" aria-label="Export workspace ${escape(w.name)} as .bwui bundle">↓</button>
        <button data-action="delete" data-id="${w.id}" aria-label="Delete workspace ${escape(w.name)}">Delete</button>
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

export async function exportWorkspace(id) {
  const w = state.workspaces.find((x) => x.id === id);
  // Push bundle manifest so the .bwui export contains file-group metadata
  if (window.bws && w?.bundle_ids?.length) {
    try {
      const bundles = await bws.bundleList();
      const manifest = await Promise.all(
        (w.bundle_ids || []).map(async (bid) => {
          const bundle = bundles.find((b) => b.id === bid);
          if (!bundle) return null;
          const files = await bws.bundleFiles(bid);
          return {
            bundle_id: bid,
            name: bundle.name || bid,
            files: files.map((f) => ({ filename: f.filename, sha256: "" })),
          };
        })
      );
      const validManifest = manifest.filter(Boolean);
      if (validManifest.length) {
        await api(`/api/workspaces/${encodeURIComponent(id)}/bundle-manifest`, {
          method: "POST",
          json: { manifest: validManifest },
        }).catch(() => {});
      }
    } catch (_) {}
  }
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

export async function importWorkspace(file) {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/api/workspaces/import", { method: "POST", body: fd });
  if (!res.ok) { flash("Import failed: " + (await res.text()), "warn"); return; }
  const data = await res.json();
  await loadWorkspaces();
  flash(`Workspace "${data.name}" imported.`, "good");
}

export function populateWorkspaceSelect() {
  const sel = $("#workspace-select");
  sel.innerHTML =
    '<option value="">— No workspace —</option>' +
    state.workspaces.map((w) => `<option value="${w.id}">${escape(w.name)}</option>`).join("");
  sel.value = state.config?.active_workspace_id || "";
  const label = $("#active-workspace-label");
  const active = state.workspaces.find((w) => w.id === state.config?.active_workspace_id);
  label.textContent = active ? active.name : "";
}

export async function activateWorkspace(id) {
  await api("/api/config", {
    method: "POST",
    json: { active_workspace_id: id || "" },
  });
  // Optimistically update state so populateWorkspaceSelect() (called inside
  // loadWorkspaces()) sees the new active ID before loadConfig() round-trips.
  if (state.config) state.config.active_workspace_id = id || "";
  // Refresh workspaces before config so loadConfig's mode-select lookup
  // can find the new active workspace's stored mode.
  await loadWorkspaces();
  await loadConfig();
  newChat();
  // Refresh file tree for the new workspace
  if (state.filesPaneVisible) refreshFileTree();
}

export function openWorkspaceDialog(workspace) {
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
          ${state.models.map((m) => `<option value="${escape(m.id)}" ${m.id === w.default_model ? "selected" : ""}>${escape(modelOptionLabel(m))}</option>`).join("")}
        </select>
      </label>
      <label>Project root <em>(optional — for file tree &amp; checkpoints)</em>
        <input id="dlg-project-root" type="text" value="${escape(w.project_root || "")}" placeholder="${escape((state.config?.workspace_dir || "") + "/my-project")}" />
        <small>Must be a folder under the server's workspace directory${state.config?.workspace_dir ? ` (<code>${escape(state.config.workspace_dir)}</code>)` : ""}. The file tree pane will show its contents.</small>
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

  const pendingWorkspaceFiles = [...(w.files || [])];
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

export function collectChecked(kind) {
  return Array.from(document.querySelectorAll(`[data-kind="${kind}"]`))
    .filter((el) => el.checked)
    .map((el) => el.dataset.id);
}

// ---------------------------------------------------------------------------
