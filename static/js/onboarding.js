// onboarding.js — First-run onboarding wizard.
// Split out of the former static/app.js (Phase 3); logic unchanged.
import { api } from "./api.js";
import { flash, trapFocus } from "./dialogs.js";
import { loadConfig, loadPrompts, loadSkills, refreshModels } from "./settings.js";
import { $, state } from "./state.js";
import { loadWorkspaces } from "./workspaces.js";

// Onboarding wizard
// ---------------------------------------------------------------------------

export async function checkOnboarding() {
  const cfg = state.config;
  if (cfg?.onboarding_done) return;
  // Show wizard
  const overlay = $("#onboarding-overlay");
  if (overlay) {
    overlay.hidden = false;
    overlay.addEventListener("keydown", trapFocus);
    const firstFocusable = overlay.querySelector(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    firstFocusable?.focus();
  }
  // Load use-case templates
  try {
    const data = await api("/api/onboarding/templates");
    renderUseCaseGrid(data.templates || []);
  } catch (e) { /* endpoint may not exist */ }
}

export function renderUseCaseGrid(templates) {
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
    card.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") {
        // Space scrolls the page by default — stop that and let it select
        // the card the same way Enter does (matches file-tree behavior).
        e.preventDefault();
        e.stopPropagation();
        card.click();
      }
    };
    grid.appendChild(card);
  }
}

export async function onboardingConnect() {
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

export async function onboardingComplete(useCaseId) {
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

export function onboardingFinish() {
  const overlay = $("#onboarding-overlay");
  if (overlay) {
    overlay.hidden = true;
    overlay.removeEventListener("keydown", trapFocus);
  }
  // Return focus to the composer so keyboard users can start typing immediately
  $("#user-input")?.focus();
  flash("Welcome to BetterWebUI!", "good");
}

// ---------------------------------------------------------------------------
