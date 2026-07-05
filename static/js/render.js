// render.js — Markdown + KaTeX rendering, chat message rendering, and read-aloud.
// Split out of the former static/app.js (Phase 3); logic unchanged.
import { api } from "./api.js";
import { send } from "./chat.js";
import { openConversation } from "./conversations.js";
import { closeDialog, flash, showDialog } from "./dialogs.js";
import { $, state } from "./state.js";

// Markdown + KaTeX rendering
// ---------------------------------------------------------------------------

export const MATH_STASH_OPEN = "";
export const MATH_STASH_CLOSE = "";
export const MATH_STASH_RE = new RegExp(MATH_STASH_OPEN + "(\\d+)" + MATH_STASH_CLOSE, "g");

export function renderMarkdownWithMath(text) {
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

export function renderMathIn(el) {
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

// Messages rendering
// ---------------------------------------------------------------------------

export function renderMessages() {
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
    renderRecentChatsResume(container);
    return;
  }
  for (const m of state.messages) {
    appendMessage(m);
  }
}

export async function renderRecentChatsResume(container) {
  if (!state.config?.api_key_set) return;
  try {
    const data = await api("/api/conversations/recent?limit=3");
    const recent = (data.recent || []).filter((c) => c.message_count > 0);
    if (!recent.length) return;
    const section = document.createElement("div");
    section.className = "resume-section";
    const heading = document.createElement("p");
    heading.className = "hint";
    heading.textContent = "Pick up where you left off:";
    section.appendChild(heading);
    const grid = document.createElement("div");
    grid.className = "resume-grid";
    for (const c of recent) {
      const card = document.createElement("button");
      card.className = "resume-card";
      card.type = "button";
      const when = _relativeTime(c.updated_at);
      card.innerHTML = `<span class="resume-title">${escape(c.title || "Untitled")}</span>
        <span class="resume-meta">${escape(when)} &middot; ${c.message_count} message${c.message_count === 1 ? "" : "s"}</span>
        ${c.summary ? `<span class="resume-summary">${escape(c.summary)}</span>` : ""}`;
      card.onclick = () => openConversation(c.id);
      grid.appendChild(card);
    }
    section.appendChild(grid);
    container.appendChild(section);
  } catch (_) { /* non-critical */ }
}

export function _relativeTime(ts) {
  if (!ts) return "";
  const diff = Math.floor(Date.now() / 1000) - ts;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

export function renderVerificationTrace(trace) {
  const container = $("#messages");
  if (!container || !trace) return;
  const card = document.createElement("div");
  card.className = "verification-card " + (trace.final_ok ? "vc-ok" : "vc-fail");
  card.setAttribute("role", "status");
  const icon = trace.final_ok ? "✓" : "⚠";
  const summary = document.createElement("div");
  summary.className = "verification-summary";
  summary.innerHTML = `<span class="vc-icon">${icon}</span><span>${
    trace.final_ok ? "Verified" : "Verification failed"
  } · ${escape(trace.tool)}${
    trace.final_attempt > 1 ? ` · attempt ${trace.final_attempt}` : ""
  }</span>`;
  card.appendChild(summary);

  // Undo button for write_file when we have a checkpoint id from the
  // most recent tool_result. The verification trace doesn't carry the
  // checkpoint, but the tool_result event preceded it; we look up the
  // last tool_result for this tool from the in-memory event log.
  if (trace.tool === "write_file" && state.lastWriteCheckpoint) {
    const undo = document.createElement("button");
    undo.type = "button";
    undo.className = "vc-undo-btn";
    undo.textContent = "Undo this write";
    const { filename, checkpoint_id } = state.lastWriteCheckpoint;
    undo.onclick = async () => {
      undo.disabled = true;
      undo.textContent = "Reverting…";
      try {
        await api("/api/project/revert", {
          method: "POST",
          json: { filename, checkpoint_id },
        });
        undo.textContent = "Reverted ✓";
        flash(`Reverted ${filename} to the previous version.`, "good");
      } catch (e) {
        undo.disabled = false;
        undo.textContent = "Undo this write";
        flash(friendlyError(e, "reverting the file"), "bad");
      }
    };
    card.appendChild(undo);
  }

  if (Array.isArray(trace.events) && trace.events.length) {
    const det = document.createElement("details");
    det.className = "verification-details";
    const sumEl = document.createElement("summary");
    sumEl.textContent = "Details";
    det.appendChild(sumEl);
    const ul = document.createElement("ul");
    for (const ev of trace.events) {
      const li = document.createElement("li");
      const okMark = ev.ok ? "✓" : "✗";
      const conf = ev.extras && typeof ev.extras.confidence === "number"
        ? ` (${Math.round(ev.extras.confidence * 100)}% confidence)`
        : "";
      li.textContent = `${okMark} [${ev.kind}] ${ev.detail || ""}${conf}`;
      ul.appendChild(li);
    }
    det.appendChild(ul);
    card.appendChild(det);
  }
  container.appendChild(card);
  container.scrollTop = container.scrollHeight;
}

export function renderBrokenImagePlaceholder(a, m) {
  const card = document.createElement("div");
  card.className = "broken-image-card";
  card.setAttribute("role", "alert");
  const label = document.createElement("div");
  label.className = "broken-image-label";
  label.textContent = "Image didn't load";
  const detail = document.createElement("div");
  detail.className = "broken-image-detail";
  const promptHint = (a && a.filename) ? a.filename : "";
  detail.textContent = promptHint
    ? `The file "${promptHint}" couldn't be displayed.`
    : "The image came back broken.";
  const actions = document.createElement("div");
  actions.className = "broken-image-actions";
  const retryBtn = document.createElement("button");
  retryBtn.type = "button";
  retryBtn.className = "broken-image-retry";
  retryBtn.textContent = "Try again";
  retryBtn.onclick = () => {
    const composer = $("#composer-input");
    if (!composer) return;
    const seed = (m && m.content) || (promptHint ? `Regenerate this image: ${promptHint}` : "Regenerate the previous image");
    composer.value = seed;
    composer.focus();
    flash("Edit the prompt and send to try again.", "info");
  };
  actions.appendChild(retryBtn);
  if (a && a.url) {
    const openBtn = document.createElement("a");
    openBtn.href = a.url;
    openBtn.target = "_blank";
    openBtn.rel = "noopener";
    openBtn.className = "broken-image-open";
    openBtn.textContent = "Open raw";
    actions.appendChild(openBtn);
  }
  card.appendChild(label);
  card.appendChild(detail);
  card.appendChild(actions);
  return card;
}

export function appendMessage(m) {
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

  // Per-message action buttons (read-aloud, why, try-again, fork)
  if (m.role === "assistant" && !m._placeholder) {
    const roleEl = tpl.querySelector(".role");
    const acts = document.createElement("div");
    acts.className = "message-actions";
    acts.innerHTML = `
      <button class="read-aloud-btn" title="Read aloud" aria-label="Read this message aloud">🔊</button>
      <button class="why-btn" title="Why did you answer that way?" aria-label="Ask for an explanation">Why?</button>
      <button class="retry-btn" title="Try another phrasing" aria-label="Try another phrasing">Try again</button>
    `;
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
    // Render the main answer first, then append subagent cards below as
    // collapsible "supporting research" sections. Reads top-down: final
    // synthesis, then the parallel research that informed it.
    const mainText = m.content || "";
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
        img.onerror = () => {
          img.replaceWith(renderBrokenImagePlaceholder(a, m));
        };
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
  const whyBtn = newEl?.querySelector(".why-btn");
  if (whyBtn) {
    whyBtn.onclick = () => askWhy(m.content || "");
  }
  const retryBtn = newEl?.querySelector(".retry-btn");
  if (retryBtn) {
    retryBtn.onclick = () => offerRetryVariant(m);
  }

  if (newEl && m.role === "assistant") renderMathIn(newEl);
  container.scrollTop = container.scrollHeight;
  return newEl;
}

export function askWhy(originalAnswer) {
  const composer = $("#composer-input");
  if (!composer) return;
  composer.value =
    "In one or two paragraphs, explain how you arrived at your last answer, what assumptions you made, and what you're least sure about.";
  composer.focus();
  flash("Press Enter to ask for an explanation.", "info");
}

export function offerRetryVariant(m) {
  showDialog({
    title: "Try this another way",
    body: `
      <p>Pick how you'd like the assistant to retry the previous answer:</p>
      <button data-variant="shorter" class="retry-variant">Shorter</button>
      <button data-variant="simpler" class="retry-variant">Simpler (explain like I'm 12)</button>
      <button data-variant="concrete" class="retry-variant">More concrete (give a real example)</button>
      <button data-variant="formal" class="retry-variant">More formal</button>
    `,
    actions: [{ label: "Cancel", role: "close" }],
  });
  setTimeout(() => {
    document.querySelectorAll(".retry-variant").forEach((btn) => {
      btn.onclick = () => {
        const variant = btn.dataset.variant;
        const prompts = {
          shorter: "Rewrite your previous answer in three sentences or fewer.",
          simpler: "Rewrite your previous answer in language a 12-year-old would understand. Keep it accurate.",
          concrete: "Rewrite your previous answer with a specific, concrete example showing the key idea in action.",
          formal: "Rewrite your previous answer in a more formal tone, suitable for a professional document.",
        };
        const composer = $("#composer-input");
        if (composer) {
          composer.value = prompts[variant];
          closeDialog();
          send();
        }
      };
    });
  }, 50);
}

export function buildSubagentCard(sa) {
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

export async function readAloud(msgEl, text, btn) {
  if (btn.classList.contains("reading")) {
    // Toggle off: stop any playing audio in this message and revoke its blob URL
    const audio = msgEl.querySelector("audio[data-tts]");
    if (audio) {
      audio.pause();
      const src = audio.src;
      audio.remove();
      if (src && src.startsWith("blob:")) URL.revokeObjectURL(src);
    }
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
    btn.title = "Read aloud";
    flash("Read aloud failed: " + e.message, "warn");
  }
}

// ---------------------------------------------------------------------------
