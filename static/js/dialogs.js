// dialogs.js — Modal dialogs: tool approval (with explain/trust), file-request picker, and the generic focus-trapped dialog.
// Split out of the former static/app.js (Phase 3); logic unchanged.
import { api, fileToContentEntry } from "./api.js";
import { $, state } from "./state.js";

// Approval dialog (shell + write) — with explain expander + trust session
// ---------------------------------------------------------------------------

export async function askApproval(req) {
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
        const existing = await api(`/api/project/file?path=${encodeURIComponent(req.filename)}&include_content=true`);
        if (existing.is_binary) {
          oldHtml = `<em>(binary file, ${existing.size ?? "?"} bytes — preview not available)</em>`;
        } else {
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
      // Remember the previously focused element so we can restore focus on close
      const previousFocus = document.activeElement;
      modal.hidden = false;
      acceptBtn.focus();
      // Trap focus inside the diff modal while it's open, matching the
      // accessibility behavior of showDialog().
      modal.addEventListener("keydown", trapFocus);
      const cleanup = () => {
        modal.removeEventListener("keydown", trapFocus);
        modal.hidden = true;
        state.pendingDialogCancel = null;
        if (previousFocus && typeof previousFocus.focus === "function") {
          try { previousFocus.focus(); } catch (_) { /* ignore */ }
        }
      };
      acceptBtn.onclick = () => { cleanup(); resolve({ approved: true }); };
      rejectBtn.onclick = () => { cleanup(); resolve({ approved: false }); };
      // Escape (via global handler) cancels with deny
      state.pendingDialogCancel = () => { cleanup(); resolve({ approved: false }); };
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
        <details class="explain-expander" id="explain-details" open>
          <summary>Explain this in plain English</summary>
          <div class="explain-body" id="explain-body"><span class="spinner"></span> Loading explanation…</div>
        </details>
        <label class="trust-session-wrap">
          <input type="checkbox" id="trust-session-cb" />
          Trust this command for the rest of the session (won't ask again)
        </label>
      `;
    } else if (req.tool === "delete_file") {
      title = "Delete a file?";
      body = `
        <div class="danger-banner"><strong>This cannot be undone.</strong> The file will be permanently deleted from your workspace.</div>
        ${req.reason ? `<p><b>Why:</b> ${escape(req.reason)}</p>` : ""}
        <p><b>File:</b> <code>${escape(req.filename)}</code></p>
        <p><small>${escape(req.dest_path || "")}</small></p>
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
            state.pendingDialogCancel = null;
            resolve({ approved: false });
          },
        },
        {
          label: "Approve",
          primary: true,
          action: async () => {
            const trustCb = document.getElementById("trust-session-cb");
            const trustSession = trustCb ? trustCb.checked : false;
            // Don't call /api/session/trust here: /api/approve already handles
            // trust_session+command atomically, so an early call would trust
            // the command even if the approve request later fails.
            closeDialog();
            state.pendingDialogCancel = null;
            resolve({ approved: true, trust_session: trustSession, command: req.command });
          },
        },
      ],
    });
    // Escape cancels with deny so the backend isn't left waiting
    state.pendingDialogCancel = () => {
      closeDialog();
      resolve({ approved: false });
    };

    // Wire explain-details toggle. The expander is open by default so
    // non-technical users see the explanation without having to know
    // to click; we kick the fetch immediately when the dialog opens.
    setTimeout(async () => {
      const det = document.getElementById("explain-details");
      if (!det || req.tool !== "execute_shell") return;
      let explained = false;
      const runExplain = async () => {
        if (explained) return;
        explained = true;
        const bodyEl = document.getElementById("explain-body");
        try {
          const data = await api("/api/explain-command", {
            method: "POST",
            json: { command: req.command },
          });
          if (bodyEl) bodyEl.textContent = data.explanation || "No explanation available.";
        } catch (e) {
          if (bodyEl) bodyEl.textContent = friendlyError(e, "explaining the command");
        }
      };
      det.addEventListener("toggle", () => {
        if (det.open) runExplain();
      });
      if (det.open) runExplain();
    }, 50);
  });
}

// ---------------------------------------------------------------------------

// File-request dialog
// ---------------------------------------------------------------------------

export async function handleFileRequest(req) {
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
            state.pendingDialogCancel = null;
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
      state.pendingDialogCancel = null;
      resolve(entries);
    };
    // Escape resolves as "skipped" so the backend isn't left waiting
    state.pendingDialogCancel = () => {
      closeDialog();
      resolve([]);
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

export function showDialog({ title, body, actions, wide }) {
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

export function trapFocus(e) {
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

export function closeDialog() {
  $("#dialog-root").innerHTML = "";
}

export function flash(msg, level = "info") {
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
