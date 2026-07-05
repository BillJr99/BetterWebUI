// main.js — Entry point: global keyboard shortcuts, tab wiring, and init(). Loaded via <script type="module"> from index.html.
// Split out of the former static/app.js (Phase 3); logic unchanged.
import { api } from "./api.js";
import { addMemoryFromPrompt, openNewBundleDialog, renderBundleList, renderMemoryList, updateMemoryBell } from "./bundles_memories.js";
import { send } from "./chat.js";
import { _initAnnotationModal, attachFile, captureScreenshot, initMic, toggleMic } from "./composer.js";
import { loadConversations, newChat, renderConversationList } from "./conversations.js";
import { closeDialog, flash, trapFocus } from "./dialogs.js";
import { checkOnboarding, onboardingComplete, onboardingConnect, onboardingFinish } from "./onboarding.js";
import { setFilesPaneVisible, setPlanPaneVisible } from "./panels.js";
import { openNewScheduledDialog, pollScheduledNotifications, renderScheduledList } from "./scheduled.js";
import { loadConfig, loadPrompts, loadSkills, openNewSkillDialog, openPromptDialog, refreshModels, saveConnection, saveDefaults, saveDisplay, saveVerification, saveWebSearch, toggleService, uploadSkill } from "./settings.js";
import { $, $$, state } from "./state.js";
import { loadCli, loadMcp, openCliCustomDialog, openCliRegistryDialog, openMcpCustomDialog, openMcpRegistryDialog, renderCloudServices } from "./tools_tab.js";
import { activateWorkspace, importWorkspace, loadWorkspaces, openWorkspaceDialog, populateWorkspaceSelect } from "./workspaces.js";

// Keyboard shortcuts
// ---------------------------------------------------------------------------

export let _gKeyPending = false;
export let _gKeyTimer = null;

export let _shortcutPriorFocus = null;

export function openShortcutSheet() {
  const sheet = $("#shortcut-sheet");
  if (!sheet || !sheet.hidden) return;
  _shortcutPriorFocus = document.activeElement;
  sheet.hidden = false;
  sheet.addEventListener("keydown", trapFocus);
  const firstFocusable = sheet.querySelector(
    'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
  );
  firstFocusable?.focus();
}

export function closeShortcutSheet() {
  const sheet = $("#shortcut-sheet");
  if (!sheet || sheet.hidden) return;
  sheet.hidden = true;
  sheet.removeEventListener("keydown", trapFocus);
  _shortcutPriorFocus?.focus();
  _shortcutPriorFocus = null;
}

export function handleGlobalKey(e) {
  // Don't intercept when typing in inputs — with two exceptions:
  //  - Ctrl/Cmd+Enter in textareas still sends the message
  //  - Escape always falls through so it can close approval dialogs,
  //    diff modals, and the file picker even when an input has focus
  //    (without this the Esc cancel/deny path becomes unreachable for
  //    keyboard users mid-form).
  const tag = document.activeElement?.tagName?.toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select") {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && tag === "textarea") {
      e.preventDefault();
      send();
      return;
    }
    if (e.key !== "Escape") return;
  }

  // Escape closes dialogs and modals
  if (e.key === "Escape") {
    // If a modal is awaiting user resolution (approval / file-pick / diff), cancel it
    // explicitly so the pending Promise resolves and the backend isn't left waiting.
    if (typeof state.pendingDialogCancel === "function") {
      const cancel = state.pendingDialogCancel;
      state.pendingDialogCancel = null;
      try { cancel(); } catch (_) { /* ignore */ }
    } else {
      closeDialog();
    }
    closeShortcutSheet();
    const diff = $("#diff-modal"); if (diff && !diff.hidden) diff.hidden = true;
    const onboarding = $("#onboarding-overlay"); if (onboarding && !onboarding.hidden) onboarding.hidden = true;
    // Return focus to the composer for keyboard users
    const composer = $("#composer-input");
    if (composer) composer.focus();
    return;
  }

  // ? toggles shortcut sheet
  if (e.key === "?") {
    const sheet = $("#shortcut-sheet");
    if (sheet?.hidden) openShortcutSheet(); else closeShortcutSheet();
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

export function switchTab(tabName) {
  $$(".tab").forEach((b) => {
    const active = b.dataset.tab === tabName;
    b.classList.toggle("active", active);
    b.setAttribute("aria-selected", active ? "true" : "false");
  });
  $$(".tab-panel").forEach((p) => p.classList.remove("active"));
  const panel = $(`#tab-${tabName}`);
  if (panel) panel.classList.add("active");
  // Lazy-load tab content the first time the user visits.
  if (tabName === "files") renderBundleList().catch(() => {});
  if (tabName === "memory") renderMemoryList().catch(() => {});
  if (tabName === "scheduled") renderScheduledList().catch(() => {});
  if (tabName === "tools") renderCloudServices().catch(() => {});
}

// ---------------------------------------------------------------------------

// Tabs and wiring
// ---------------------------------------------------------------------------

export function wireTabs() {
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

export function wireEvents() {
  // Core chat
  $("#new-chat-btn").onclick = newChat;
  $("#send-btn").onclick = send;
  $("#composer-input").addEventListener("keydown", (e) => {
    // Plain Enter in the textarea sends. Ctrl/Cmd+Enter is handled by
    // handleGlobalKey() — if we also handled it here, send() would fire
    // twice (once per listener). Don't duplicate that branch.
    if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      send();
    }
  });
  $("#composer-input").addEventListener("paste", async (e) => {
    const items = e.clipboardData && e.clipboardData.items;
    if (!items) return;
    for (const it of items) {
      if (it.kind === "file" && it.type.startsWith("image/")) {
        e.preventDefault();
        const f = it.getAsFile();
        if (!f) continue;
        const named = new File([f], f.name || `pasted-${Date.now()}.png`, { type: f.type });
        await attachFile(named);
        const vis = $("#toggle-vision"); if (vis) vis.checked = true;
        flash("Pasted image attached.", "good");
        return;
      }
    }
  });
  $("#attach-input").addEventListener("change", (e) => {
    const f = e.target.files[0];
    if (f) {
      attachFile(f);
      if (f.type && f.type.startsWith("image/")) {
        const vis = $("#toggle-vision"); if (vis) vis.checked = true;
      }
    }
    e.target.value = "";
  });

  // Screenshot capture (Chromium / Firefox; hidden on Safari which lacks getDisplayMedia)
  const screenshotBtn = $("#screenshot-btn");
  if (screenshotBtn) {
    if (navigator.mediaDevices && typeof navigator.mediaDevices.getDisplayMedia === "function") {
      screenshotBtn.hidden = false;
      screenshotBtn.addEventListener("click", captureScreenshot);
    } else {
      screenshotBtn.hidden = true;
    }
  }

  // Mic
  $("#mic-btn")?.addEventListener("click", toggleMic);

  // Settings
  $("#save-connection").onclick = saveConnection;
  $("#save-defaults").onclick = saveDefaults;
  $("#save-display")?.addEventListener("click", saveDisplay);
  $("#save-websearch")?.addEventListener("click", saveWebSearch);
  $("#save-verification")?.addEventListener("click", saveVerification);
  $("#cfg-websearch-provider")?.addEventListener("change", (e) => {
    const wrap = $("#cfg-websearch-custom-wrap");
    if (wrap) wrap.hidden = (e.target.value !== "custom");
  });

  // Services enable/disable
  ["clk", "autogui", "osso"].forEach((name) => {
    $(`#svc-${name}-enabled`)?.addEventListener("change", (e) => {
      toggleService(name, e.target.checked);
    });
  });

  // Prompts
  $("#new-prompt-btn").onclick = () => openPromptDialog(null);

  // Skills
  $("#new-skill-btn").onclick = openNewSkillDialog;
  $("#upload-skill").addEventListener("change", (e) => {
    const f = e.target.files[0];
    if (f) uploadSkill(f);
    e.target.value = "";
  });

  // File bundles, memories, scheduled tasks
  $("#new-bundle-btn")?.addEventListener("click", openNewBundleDialog);
  $("#new-memory-btn")?.addEventListener("click", addMemoryFromPrompt);
  $("#memory-pause-toggle")?.addEventListener("change", (e) => {
    state.memoryPaused = !!e.target.checked;
  });
  $("#new-scheduled-btn")?.addEventListener("click", openNewScheduledDialog);
  $("#memory-bell")?.addEventListener("click", () => switchTab("memory"));

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

  // Conversation search (debounced, uses server-side full-text search)
  let _searchTimer = null;
  $("#search-toggle-btn")?.addEventListener("click", () => {
    const wrap = $("#conv-search-wrap");
    if (!wrap) return;
    wrap.hidden = !wrap.hidden;
    if (!wrap.hidden) $("#conv-search")?.focus();
    else {
      state.convSearchQuery = "";
      state.convSearchResults = null;
      renderConversationList();
    }
  });
  $("#conv-search")?.addEventListener("input", (e) => {
    const q = e.target.value;
    state.convSearchQuery = q;
    clearTimeout(_searchTimer);
    if (!q.trim()) {
      state.convSearchResults = null;
      renderConversationList();
      return;
    }
    _searchTimer = setTimeout(async () => {
      try {
        const data = await api(`/api/conversations/search?q=${encodeURIComponent(q)}`);
        state.convSearchResults = data.results || [];
        renderConversationList();
      } catch (_) {}
    }, 250);
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
  $("#shortcut-help-btn")?.addEventListener("click", openShortcutSheet);
  $("#shortcut-sheet")?.querySelector(".modal-close")?.addEventListener("click", closeShortcutSheet);

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

export async function init() {
  wireTabs();
  wireEvents();
  initMic();
  _initAnnotationModal();
  // Load workspaces before config so loadConfig's #mode-select lookup can
  // see the active workspace's stored mode on first paint; otherwise the
  // select would fall back to config.chat_mode until the next config
  // refresh.
  await loadWorkspaces();
  await loadConfig();
  await Promise.all([
    refreshModels(),
    loadPrompts(),
    loadSkills(),
    loadMcp(),
    loadCli(),
    loadConversations(),
  ]);
  populateWorkspaceSelect();
  newChat();
  // Memory bell + scheduled notification poll
  updateMemoryBell().catch(() => {});
  setInterval(() => { try { pollScheduledNotifications(); } catch (_) {} }, 30000);
  pollScheduledNotifications();
  // Request notification permission once, non-blocking.
  if ("Notification" in window && Notification.permission === "default") {
    setTimeout(() => { try { Notification.requestPermission(); } catch (_) {} }, 3000);
  }
  // Check if onboarding is needed
  await checkOnboarding();
}

init().catch((e) => {
  document.body.innerHTML = `<pre style="padding: 30px;">Init failed: ${escape(e.message)}\n\n${escape(e.stack || "")}</pre>`;
});
