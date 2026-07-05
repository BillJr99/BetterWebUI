// bundles_memories.js — IndexedDB-backed file bundles and user memories (via the bws global from browser-store.js).
// Split out of the former static/app.js (Phase 3); logic unchanged.
import { api } from "./api.js";
import { closeDialog, flash, showDialog } from "./dialogs.js";
import { $, state } from "./state.js";

// File bundles (IndexedDB-backed; mounted bundles ride per-message attachments)
// ---------------------------------------------------------------------------

state.mountedBundleIds = state.mountedBundleIds || [];

export async function renderBundleList() {
  if (!window.bws) return;
  const ul = $("#bundle-list");
  if (!ul) return;
  let bundles;
  try { bundles = await bws.bundleList(); }
  catch (e) { ul.innerHTML = `<li class="hint">Couldn't read browser storage: ${escape(e.message || e)}</li>`; return; }
  if (!bundles.length) {
    ul.innerHTML = '<li class="hint">No bundles yet. Click "+ New file bundle" to create one.</li>';
  } else {
    ul.innerHTML = "";
    for (const b of bundles) {
      const li = document.createElement("li");
      li.className = "bundle-item";
      const mounted = state.mountedBundleIds.includes(b.id);
      li.innerHTML = `
        <div class="bundle-header">
          <strong>${escape(b.name)}</strong>
          <span class="bundle-meta">${b.file_count} file(s) · ${(b.total_bytes/1024).toFixed(0)} KB</span>
        </div>
        ${b.description ? `<div class="bundle-desc">${escape(b.description)}</div>` : ""}
        <div class="bundle-actions">
          <label class="checkbox">
            <input type="checkbox" data-action="mount" ${mounted ? "checked" : ""} />
            Mount in this chat
          </label>
          <button data-action="open">Open</button>
          <button data-action="delete">Delete</button>
        </div>
      `;
      li.querySelector('[data-action="mount"]').addEventListener("change", (e) => {
        toggleBundleMount(b.id, e.target.checked);
      });
      li.querySelector('[data-action="open"]').onclick = () => openBundleDialog(b.id);
      li.querySelector('[data-action="delete"]').onclick = async () => {
        if (!confirm(`Delete bundle "${b.name}" and all its files?`)) return;
        await bws.bundleDelete(b.id);
        state.mountedBundleIds = state.mountedBundleIds.filter((id) => id !== b.id);
        renderBundleList();
        renderMountedBundleChip();
      };
      ul.appendChild(li);
    }
  }
  // Show storage quota
  const quota = await bws.storageEstimate();
  const quotaEl = $("#bundles-quota");
  if (quotaEl && quota) {
    const usedMb = (quota.usage / (1024 * 1024)).toFixed(1);
    const totalMb = (quota.quota / (1024 * 1024)).toFixed(0);
    quotaEl.textContent = ` Storage: ${usedMb} MB used of ~${totalMb} MB.`;
  }
}

export function toggleBundleMount(bundleId, mount) {
  if (mount) {
    if (!state.mountedBundleIds.includes(bundleId)) state.mountedBundleIds.push(bundleId);
  } else {
    state.mountedBundleIds = state.mountedBundleIds.filter((id) => id !== bundleId);
  }
  renderMountedBundleChip();
}

export async function renderMountedBundleChip() {
  const wrap = $("#mounted-bundles-chip");
  if (!wrap) return;
  if (!state.mountedBundleIds.length) {
    wrap.hidden = true;
    wrap.innerHTML = "";
    return;
  }
  const bundles = await bws.bundleList();
  const mounted = bundles.filter((b) => state.mountedBundleIds.includes(b.id));
  if (!mounted.length) {
    wrap.hidden = true;
    return;
  }
  wrap.hidden = false;
  wrap.innerHTML = `📎 ${mounted.length} bundle(s) mounted: ${mounted.map((b) => escape(b.name)).join(", ")}`;
}

export async function openNewBundleDialog() {
  const name = prompt("Bundle name?", "Untitled bundle");
  if (!name) return;
  const desc = prompt("Short description? (optional)", "") || "";
  const b = await bws.bundleCreate(name, desc);
  flash(`Created bundle "${b.name}".`, "good");
  renderBundleList();
}

export async function openBundleDialog(bundleId) {
  const files = await bws.bundleFiles(bundleId);
  const bundles = await bws.bundleList();
  const b = bundles.find((x) => x.id === bundleId);
  if (!b) return;
  const list = files.map((f) =>
    `<li>${escape(f.filename)} <small>(${(f.size/1024).toFixed(0)} KB)</small>
      <button data-fid="${f.id}" class="btn-remove">Remove</button></li>`
  ).join("");
  showDialog({
    title: `${b.name} — ${files.length} file(s)`,
    body: `
      <p>${escape(b.description || "")}</p>
      <input type="file" id="bundle-add-input" multiple />
      <ul id="bundle-file-list" class="bundle-file-list">${list}</ul>
    `,
    actions: [{ label: "Done", role: "close" }],
  });
  setTimeout(() => {
    document.getElementById("bundle-add-input").addEventListener("change", async (e) => {
      for (const f of e.target.files) {
        try { await bws.bundleAddFile(bundleId, f); }
        catch (err) { flash(friendlyError(err, "adding the file"), "bad"); }
      }
      closeDialog();
      openBundleDialog(bundleId);
    });
    document.querySelectorAll(".btn-remove").forEach((btn) => {
      btn.onclick = async () => {
        await bws.bundleRemoveFile(btn.dataset.fid);
        closeDialog();
        openBundleDialog(bundleId);
      };
    });
  }, 50);
}

// Upload mounted bundles to the transient store and return attachment records.
export async function gatherMountedBundleAttachments() {
  if (!state.mountedBundleIds.length || !window.bws) return [];
  const chatId = state.currentConversationId || "anon";
  const out = [];
  for (const bundleId of state.mountedBundleIds) {
    let files;
    try { files = await bws.bundleFiles(bundleId); } catch (_) { continue; }
    for (const f of files) {
      try {
        const fd = new FormData();
        fd.append("file", f.blob, f.filename);
        const res = await fetch(`/api/uploads/transient?chat_id=${encodeURIComponent(chatId)}`, {
          method: "POST",
          body: fd,
        });
        if (!res.ok) continue;
        const a = await res.json();
        out.push({ url: a.url, filename: a.filename, content_type: a.content_type });
      } catch (_) { continue; }
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// User memories (IndexedDB-backed; injected via system prompt)
// ---------------------------------------------------------------------------

state.memoryPaused = false;

export async function renderMemoryList() {
  if (!window.bws) return;
  const ul = $("#memory-list");
  if (!ul) return;
  const mems = await bws.memoryList();
  if (!mems.length) {
    ul.innerHTML = '<li class="hint">No memories yet. Add one yourself or wait for BetterWebUI to suggest some.</li>';
    return;
  }
  ul.innerHTML = "";
  for (const m of mems) {
    const li = document.createElement("li");
    li.className = "memory-item mem-" + (m.source || "user");
    li.innerHTML = `
      <label class="memory-toggle">
        <input type="checkbox" data-action="toggle" ${m.enabled ? "checked" : ""} ${m.source === "auto_extracted_pending" ? "disabled" : ""} />
        <span class="memory-text">${escape(m.text)}</span>
        <span class="memory-meta">${m.category} · ${m.source === "auto_extracted_pending" ? "Pending — accept below" : m.source}</span>
      </label>
      <div class="memory-actions">
        ${m.source === "auto_extracted_pending"
          ? `<button data-action="accept">Accept</button>`
          : ""}
        <button data-action="delete">Delete</button>
      </div>`;
    li.querySelector('[data-action="toggle"]').addEventListener("change", async (e) => {
      await bws.memoryUpdate(m.id, { enabled: e.target.checked });
    });
    const acceptBtn = li.querySelector('[data-action="accept"]');
    if (acceptBtn) {
      acceptBtn.onclick = async () => {
        await bws.memoryUpdate(m.id, { source: "auto_extracted_accepted", enabled: true });
        renderMemoryList();
        updateMemoryBell();
      };
    }
    li.querySelector('[data-action="delete"]').onclick = async () => {
      await bws.memoryDelete(m.id);
      renderMemoryList();
      updateMemoryBell();
    };
    ul.appendChild(li);
  }
}

export async function addMemoryFromPrompt() {
  const text = prompt("What should I remember about you?");
  if (!text) return;
  try {
    await bws.memoryAdd({ text, category: "preference", source: "user_added", enabled: true });
    flash("I'll remember that.", "good");
    renderMemoryList();
  } catch (e) {
    flash(friendlyError(e, "saving the memory"), "bad");
  }
}

export async function updateMemoryBell() {
  const bell = $("#memory-bell");
  if (!bell || !window.bws) return;
  const pending = await bws.memoryPendingCount();
  if (pending > 0) {
    bell.hidden = false;
    bell.textContent = `🔔 ${pending}`;
    bell.title = `${pending} memory candidate(s) pending`;
  } else {
    bell.hidden = true;
  }
}

export async function suggestMemoryCandidates(userMessage, assistantMessage) {
  if (state.memoryPaused || !window.bws) return;
  let data;
  try {
    data = await api("/api/memory/extract", {
      method: "POST",
      json: { user_message: userMessage, assistant_message: assistantMessage },
    });
  } catch (_) { return; }
  const candidates = (data && data.candidates) || [];
  for (const c of candidates) {
    try {
      await bws.memoryAdd({
        text: c.text,
        category: c.category || "other",
        source: "auto_extracted_pending",
        enabled: false,
      });
    } catch (_) { continue; }
  }
  if (candidates.length) {
    updateMemoryBell();
    flash(`${candidates.length} memory suggestion(s) ready in the Memory tab.`, "info");
  }
}

// ---------------------------------------------------------------------------
