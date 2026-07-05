// chat.js — Send pipeline and the /api/chat SSE consumption loop (parseSSEBlock itself lives in lib.js).
// Split out of the former static/app.js (Phase 3); logic unchanged.
import { api, b64ToBlob, storeFile } from "./api.js";
import { gatherMountedBundleAttachments, suggestMemoryCandidates } from "./bundles_memories.js";
import { renderAttachments } from "./composer.js";
import { loadConversations } from "./conversations.js";
import { askApproval, flash, handleFileRequest } from "./dialogs.js";
import { refreshFileTree, renderPlan, setPlanPaneVisible } from "./panels.js";
import { appendMessage, renderMessages, renderVerificationTrace } from "./render.js";
import { $, state } from "./state.js";

// Send + SSE chat loop
// ---------------------------------------------------------------------------

export async function send() {
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

  // Show a Stop button next to the send button while the request is in flight.
  let stopBtn = $("#stop-btn");
  if (!stopBtn) {
    stopBtn = document.createElement("button");
    stopBtn.id = "stop-btn";
    stopBtn.type = "button";
    stopBtn.textContent = "Stop";
    stopBtn.className = "stop-btn";
    sendBtn.parentElement.insertBefore(stopBtn, sendBtn.nextSibling);
  }
  stopBtn.hidden = false;
  stopBtn.onclick = () => {
    try { state.sendAbortController?.abort(); } catch (_) {}
    flash("Stopped.", "info");
  };

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
    const visionToggle = $("#toggle-vision");
    const webToggle = $("#toggle-websearch");
    const useVision = !!(visionToggle && visionToggle.checked);
    const webMode = (webToggle && webToggle.value) || "off";

    // Collect user memories (if enabled in browser storage and not paused)
    let userMemories = [];
    if (window.bws && !state.memoryPaused) {
      try {
        userMemories = await bws.memoryEnabledTexts(state.config?.active_workspace_id || "");
      } catch (_) { userMemories = []; }
    }
    // Upload mounted file bundles transiently for this turn
    let bundleAttachments = [];
    try { bundleAttachments = await gatherMountedBundleAttachments(); } catch (_) { bundleAttachments = []; }

    state.sendAbortController = new AbortController();
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        conversation_id: state.currentConversationId,
        messages: sendable,
        model,
        mode: chatMode,
        use_vision: useVision,
        web_search_mode: webMode,
        user_memories: userMemories,
        bundle_attachments: bundleAttachments,
      }),
      signal: state.sendAbortController.signal,
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
        const human = humanLabelForTool(data.tool);
        const sysMsg = {
          role: "system-event",
          content: data.command ? `${human}: ${data.command}` : human,
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
      if (event === "verification") {
        renderVerificationTrace(data);
        return;
      }
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
        // Trigger memory extraction on the last user+assistant pair.
        const lastAssistant = [...state.messages].reverse().find((m) => m.role === "assistant" && m.content);
        const lastUser = [...state.messages].reverse().find((m) => m.role === "user" && m.content);
        if (lastAssistant && lastUser) {
          suggestMemoryCandidates(lastUser.content, lastAssistant.content);
        }
        // Cache a one-line summary for the resume surface (uses the title + first user message)
        if (state.currentConversationId && !state.messages.some((m) => m._hasSummary)) {
          const firstUser = state.messages.find((m) => m.role === "user" && m.content);
          if (firstUser) {
            const rawSummary = (typeof firstUser.content === "string"
              ? firstUser.content
              : "").slice(0, 120).replace(/\n/g, " ").trim();
            if (rawSummary) {
              api(`/api/conversations/${state.currentConversationId}/summary`, {
                method: "POST",
                json: { summary: rawSummary },
              }).catch(() => {});
            }
          }
        }
        return;
      }
      if (event === "error") {
        // The server sends a structured envelope in data.error
        // ({code, message, hint, request_id}); data.message is the legacy
        // plain-text field kept for older servers.
        const env = data.error || {};
        const human = friendlyError({ message: env.message || data.message || "" }, "running that step");
        const parts = [human];
        if (env.hint) parts.push(env.hint);
        if (env.request_id) parts.push(`(ref: ${env.request_id})`);
        const sysMsg = { role: "system-event", content: parts.join(" ") };
        state.messages.push(sysMsg);
        appendMessage(sysMsg);
        flash(human, "warn");
        return;
      }
    });
  } catch (e) {
    placeholderEl?.remove();
    state.messages = state.messages.filter((m) => !m._placeholder);
    flash(friendlyError(e, "sending the message"), "warn");
  } finally {
    state.busy = false;
    state.sendAbortController = null;
    const btn = $("#send-btn");
    btn.disabled = false;
    btn.textContent = "Send";
    const sb = $("#stop-btn"); if (sb) sb.hidden = true;
  }
}

export function showTelemetryLine(t) {
  const el = $("#telemetry-line");
  if (!el) return;
  el.hidden = false;
  el.textContent = `${t.tokens_in ?? "?"}→${t.tokens_out ?? "?"}t · ${t.elapsed_ms ?? "?"}ms`;
  clearTimeout(el._timer);
  el._timer = setTimeout(() => { el.hidden = true; }, 8000);
}

export async function handleToolResult(data) {
  const r = data.result || {};

  // Stash checkpoint info from the most recent write_file so the
  // verification card (which arrives moments later) can render an
  // "Undo" button without re-plumbing the trace.
  if (data.tool === "write_file" && r && r.checkpoint_id && r.filename) {
    state.lastWriteCheckpoint = { filename: r.filename, checkpoint_id: r.checkpoint_id };
  }

  // write_file result: backend sends data_b64 only on failure (so the user
  // can still recover the bytes via download). On success, the file is on
  // disk at project_root and the file-tree pane shows it — no SSE-bloating
  // base64 in that path.
  const isWriteResult = data.tool === "write_file" && r.filename;
  if (isWriteResult || (r.data_b64 && r.filename)) {
    const mime = r.mime || "application/octet-stream";
    const label =
      mime.startsWith("image/") ? "Image" :
      mime.startsWith("audio/") ? "Audio" :
      mime.startsWith("video/") ? "Video" : "File";
    const attachments = [];
    let content;
    if (r.data_b64) {
      const blob = b64ToBlob(r.data_b64, mime);
      const url = storeFile(blob, r.filename, mime);
      attachments.push({ url, content_type: mime, filename: r.filename });
      content = `${label} ready: ${r.filename}`;
      if (r.write_error) {
        content += `\n⚠️ On-disk write failed: ${r.write_error}. ` +
          `You can still download the generated file from the link below.`;
      }
    } else {
      // Successful write with no inlined bytes — point the user at the file
      // tree where the saved file now lives.
      content = `${label} saved: ${r.filename} (open from the Files pane to view).`;
    }
    const sysMsg = { role: "tool", content, attachments };
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

  if (data.tool === "delete_file") {
    const content = r.error
      ? `Delete failed: ${r.error}`
      : `Deleted: ${r.deleted}`;
    const sysMsg = { role: "tool", content };
    state.messages.push(sysMsg);
    appendMessage(sysMsg);
    if (!r.error && state.filesPaneVisible) refreshFileTree();
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

export async function consumeSSE(res, onEvent) {
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
      const { event: eventName, data: dataStr } = parseSSEBlock(block);
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
