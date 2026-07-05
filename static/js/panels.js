// panels.js — Right-rail panes: task plan and project file tree (diff/checkpoints).
// Split out of the former static/app.js (Phase 3); logic unchanged.
import { api } from "./api.js";
import { flash, showDialog } from "./dialogs.js";
import { $, state } from "./state.js";

// Task plan pane
// ---------------------------------------------------------------------------

export const _PLAN_STATUSES = ["pending", "in_progress", "done", "blocked"];

export function renderPlan() {
  const list = $("#plan-list");
  if (!list) return;
  list.innerHTML = "";
  if (!state.taskPlan.length) {
    list.innerHTML = `<li class="plan-item pending"><span class="plan-item-text" style="font-style:italic;color:var(--ink-faint)">No plan yet.</span></li>`;
    return;
  }
  const icons = { pending: "○", in_progress: "◉", done: "✓", blocked: "⚠" };
  for (const item of state.taskPlan) {
    // Clamp item.status to the known set — values come from model output and
    // could otherwise inject whitespace or extra tokens into className.
    const status = _PLAN_STATUSES.includes(item.status) ? item.status : "pending";
    const li = document.createElement("li");
    li.className = `plan-item ${status}`;
    li.innerHTML = `
      <span class="plan-item-icon" aria-label="${escape(status)}">${icons[status]}</span>
      <span class="plan-item-text">
        ${escape(item.title || "")}
        ${item.note ? `<div class="plan-item-note">${escape(item.note)}</div>` : ""}
      </span>`;
    list.appendChild(li);
  }
}

export function setRightRailVisible(show) {
  state.rightRailVisible = show;
  const rail = $("#right-rail");
  if (!rail) return;
  rail.hidden = !show;
}

export function setPlanPaneVisible(show) {
  state.planPaneVisible = show;
  const pane = $("#plan-pane");
  if (!pane) return;
  pane.hidden = !show;
  const btn = $("#toggle-plan-btn");
  if (btn) btn.setAttribute("aria-pressed", show ? "true" : "false");
  updateRightRailVisibility();
}

export function setFilesPaneVisible(show) {
  state.filesPaneVisible = show;
  const pane = $("#files-pane");
  if (!pane) return;
  pane.hidden = !show;
  const btn = $("#toggle-files-btn");
  if (btn) btn.setAttribute("aria-pressed", show ? "true" : "false");
  if (show) refreshFileTree();
  updateRightRailVisibility();
}

export function updateRightRailVisibility() {
  setRightRailVisible(state.planPaneVisible || state.filesPaneVisible);
}

// ---------------------------------------------------------------------------
// File tree pane
// ---------------------------------------------------------------------------

export async function refreshFileTree() {
  const hint = $("#file-tree-hint");
  const ul = $("#file-tree");
  try {
    const data = await api("/api/project/tree");
    // Three distinct states surfaced by the backend:
    //   project_root_clamped=true → configured but invalid (silently fell back)
    //   project_root_set=false    → not configured yet
    //   project_root_set=true     → configured and honored (hide hint)
    if (hint) {
      if (data.project_root_clamped) {
        hint.hidden = false;
        hint.textContent = "This workspace's project root is invalid (outside the workspace directory). Update it in the workspace settings.";
      } else if (data.project_root_set === false) {
        hint.hidden = false;
        hint.textContent = "No project root set for this workspace. Open the workspace settings to point it at a folder.";
      } else {
        hint.hidden = true;
      }
    }
    renderFileTree(ul, data.entries || []);
  } catch (e) {
    if (ul) ul.innerHTML = "";
    if (hint) {
      hint.hidden = false;
      const status = e && e.status ? e.status : 0;
      if (status === 404 || status === 403) {
        // The endpoint exists but the workspace has no usable project_root —
        // guide the user toward the workspace settings.
        hint.textContent = "No project root set for this workspace. Open the workspace settings to point it at a folder.";
      } else if (status >= 500) {
        hint.textContent = `Couldn't load file tree (server error ${status}). Try again, or check the server logs.`;
      } else if (status === 0) {
        hint.textContent = "Couldn't reach the server. Check your network connection and try again.";
      } else {
        hint.textContent = `Couldn't load file tree (HTTP ${status}).`;
      }
    }
  }
}

export function renderFileTree(ul, entries) {
  ul.innerHTML = "";
  for (const entry of entries) {
    const li = document.createElement("li");
    if (entry.type === "dir") {
      li.innerHTML = `<details><summary class="file-tree-item dir"><span class="file-tree-icon">📁</span>${escape(entry.name)}</summary><ul class="file-tree" data-path="${escape(entry.path)}"></ul></details>`;
      const sub = li.querySelector("ul");
      const details = li.querySelector("details");
      details.addEventListener("toggle", async () => {
        // Use a data-loaded flag so empty directories aren't refetched on
        // every expand (children.length === 0 stays true for empty results).
        // Only mark loaded on success so a transient failure stays retryable.
        if (details.open && details.dataset.loaded !== "1") {
          try {
            const data = await api(`/api/project/tree?path=${encodeURIComponent(entry.path)}`);
            renderFileTree(sub, data.entries || []);
            details.dataset.loaded = "1";
          } catch (e) { /* silent — leave unloaded so next expand retries */ }
        }
      });
    } else {
      li.innerHTML = `<div class="file-tree-item" role="button" tabindex="0" data-path="${escape(entry.path)}"><span class="file-tree-icon">📄</span>${escape(entry.name)}</div>`;
      li.querySelector(".file-tree-item").onclick = () => openProjectFile(entry.path);
      li.querySelector(".file-tree-item").onkeydown = (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();   // stop Space from scrolling the page
          e.stopPropagation();
          openProjectFile(entry.path);
        }
      };
    }
    ul.appendChild(li);
  }
}

export async function openProjectFile(path) {
  const ws = state.workspaces.find((w) => w.id === state.config?.active_workspace_id);
  try {
    // First fetch metadata only — for binary files we never display the bytes,
    // so save bandwidth by skipping the second fetch. For text files do a
    // second request with include_content=true to populate the preview.
    const wsParam = `workspace_id=${encodeURIComponent(ws?.id || "")}`;
    const meta = await api(`/api/project/file?${wsParam}&path=${encodeURIComponent(path)}`);
    const name = path.split("/").pop() || path;
    let body;
    if (meta.is_binary) {
      const sizeStr = meta.size != null ? `${meta.size} bytes` : "unknown size";
      const truncated = meta.truncated ? " (truncated)" : "";
      body = `<p><em>Binary file (${sizeStr})${truncated} — preview not available.</em></p>`;
    } else {
      const data = await api(`/api/project/file?${wsParam}&path=${encodeURIComponent(path)}&include_content=true`);
      // The full read can decode differently than the 4 KB header sniff
      // (e.g., a NUL byte later in the file). Re-check is_binary on the
      // second response before rendering the content as text.
      if (data.is_binary) {
        const sizeStr = data.size != null ? `${data.size} bytes` : "unknown size";
        const truncated = data.truncated ? " (truncated)" : "";
        body = `<p><em>Binary file (${sizeStr})${truncated} — preview not available.</em></p>`;
      } else {
        const truncated = data.truncated
          ? `<p class="hint">File was truncated to the first 1 MB for preview.</p>` : "";
        body = `${truncated}<pre style="max-height:400px;overflow:auto;">${escape(data.content || "")}</pre>`;
      }
    }
    showDialog({
      title: name,
      wide: true,
      body,
      actions: [{ label: "Close", action: "cancel" }],
    });
  } catch (e) {
    flash("Could not open file.", "warn");
  }
}

// ---------------------------------------------------------------------------
