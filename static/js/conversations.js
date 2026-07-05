// conversations.js — Conversation list, search, pin, tag, and fork.
// Split out of the former static/app.js (Phase 3); logic unchanged.
import { api } from "./api.js";
import { renderAttachments } from "./composer.js";
import { flash } from "./dialogs.js";
import { renderPlan } from "./panels.js";
import { renderMessages } from "./render.js";
import { $, state } from "./state.js";

// Conversations: search, pin, tag, fork
// ---------------------------------------------------------------------------

export async function loadConversations() {
  const data = await api("/api/conversations");
  state.conversations = data.conversations || [];
  renderConversationList();
}

export function renderConversationList() {
  const ul = $("#conversation-list");
  ul.innerHTML = "";
  const q = state.convSearchQuery.trim();

  // When server-side search results are available, render them instead
  if (q && state.convSearchResults !== null) {
    const results = state.convSearchResults;
    if (!results.length) {
      ul.innerHTML = `<li class="hint" role="presentation" style="padding: 8px; list-style: none;">No results.</li>`;
      return;
    }
    for (const r of results) {
      const li = document.createElement("li");
      if (r.id === state.currentConversationId) li.classList.add("active");
      li.innerHTML = `
        <div class="list-item-title">
          <div>${escape(r.title || "Untitled")}</div>
          ${r.snippet ? `<div class="conv-snippet hint">&hellip;${escape(r.snippet)}&hellip;</div>` : ""}
        </div>`;
      li.onclick = () => openConversation(r.id);
      ul.appendChild(li);
    }
    return;
  }

  // Default: all conversations, client-side tag/title filter
  let convs = state.conversations;
  if (q) {
    const ql = q.toLowerCase();
    convs = convs.filter((c) =>
      (c.title || "").toLowerCase().includes(ql) ||
      (c.tags || []).some((t) => t.toLowerCase().includes(ql))
    );
  }
  if (!convs.length) {
    ul.innerHTML = `<li class="hint" role="presentation" style="padding: 8px; list-style: none;">${q ? "No results." : "No chats yet."}</li>`;
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
          ${c.pinned ? '<span class="pin-badge" title="Pinned">&#128204;</span>' : ""}
          ${tags}
        </div>
      </div>
      <div class="list-actions">
        <button data-action="pin" data-id="${c.id}" title="${c.pinned ? "Unpin" : "Pin"}" aria-label="${c.pinned ? "Unpin" : "Pin"} conversation ${escape(c.title || "")}" aria-pressed="${c.pinned ? "true" : "false"}">&#128204;</button>
        <button data-action="fork" data-id="${c.id}" title="Fork this conversation" aria-label="Fork conversation ${escape(c.title || "")}">&#10167;</button>
        <button data-action="delete" data-id="${c.id}" title="Delete" aria-label="Delete conversation ${escape(c.title || "")}">&#215;</button>
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

export async function openConversation(id) {
  const conv = await api(`/api/conversations/${id}`);
  state.currentConversationId = id;
  state.messages = conv.messages || [];
  state.taskPlan = conv.task_plan || [];
  renderMessages();
  renderPlan();
  renderConversationList();
}

export async function deleteConversation(id) {
  if (!confirm("Delete this conversation?")) return;
  await api(`/api/conversations/${id}`, { method: "DELETE" });
  if (state.currentConversationId === id) newChat();
  await loadConversations();
}

export async function pinConversation(id, pin) {
  await api(`/api/conversations/${id}/pin`, { method: "POST", json: { pinned: pin } });
  await loadConversations();
}

export async function forkConversation(id) {
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

export function newChat() {
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
