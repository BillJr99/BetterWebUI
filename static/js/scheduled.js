// scheduled.js — Scheduled tasks tab.
// Split out of the former static/app.js (Phase 3); logic unchanged.
import { api } from "./api.js";
import { closeDialog, flash, showDialog } from "./dialogs.js";
import { $, state } from "./state.js";

// Scheduled tasks
// ---------------------------------------------------------------------------

export async function renderScheduledList() {
  const ul = $("#scheduled-list");
  if (!ul) return;
  let tasks;
  try {
    const data = await api("/api/scheduled-tasks");
    tasks = data.tasks || [];
  } catch (e) {
    ul.innerHTML = `<li class="hint">${escape(friendlyError(e, "loading scheduled tasks"))}</li>`;
    return;
  }
  if (!tasks.length) {
    ul.innerHTML = '<li class="hint">No scheduled tasks. Click "+ Schedule a task" to create one.</li>';
    return;
  }
  ul.innerHTML = "";
  for (const t of tasks) {
    const li = document.createElement("li");
    li.className = "scheduled-item";
    const nextStr = t.next_run_at
      ? new Date(t.next_run_at * 1000).toLocaleString()
      : "—";
    li.innerHTML = `
      <div class="scheduled-header">
        <strong>${escape(t.name)}</strong>
        <span class="scheduled-meta">${t.enabled ? "On" : "Paused"} · next: ${escape(nextStr)}</span>
      </div>
      <div class="scheduled-prompt">${escape((t.prompt || "").slice(0, 200))}</div>
      <div class="scheduled-actions">
        <button data-action="toggle">${t.enabled ? "Pause" : "Resume"}</button>
        <button data-action="delete">Delete</button>
      </div>
    `;
    li.querySelector('[data-action="toggle"]').onclick = async () => {
      await api("/api/scheduled-tasks", { method: "POST", json: { ...t, enabled: !t.enabled } });
      renderScheduledList();
    };
    li.querySelector('[data-action="delete"]').onclick = async () => {
      if (!confirm(`Delete scheduled task "${t.name}"?`)) return;
      await api(`/api/scheduled-tasks/${encodeURIComponent(t.id)}`, { method: "DELETE" });
      renderScheduledList();
    };
    ul.appendChild(li);
  }
}

export function openNewScheduledDialog() {
  showDialog({
    title: "Schedule a task",
    body: `
      <label>Name <input id="sch-name" type="text" placeholder="Morning email summary" /></label>
      <label>Prompt <textarea id="sch-prompt" rows="3" placeholder="Summarise my Outlook unread"></textarea></label>
      <label>Repeat
        <select id="sch-kind">
          <option value="once">Once</option>
          <option value="cron-lite" selected>Daily / Weekly (pick days &amp; time)</option>
          <option value="interval">Every N seconds</option>
        </select>
      </label>
      <div id="sch-once-wrap" hidden>
        <label>When (date &amp; time) <input id="sch-at" type="datetime-local" /></label>
      </div>
      <div id="sch-cron-wrap">
        <label>Time <input id="sch-time" type="time" value="09:00" /></label>
        <label>Days
          <span class="day-row">
            <label class="day-chip"><input type="checkbox" data-d="1" checked /> Mon</label>
            <label class="day-chip"><input type="checkbox" data-d="2" checked /> Tue</label>
            <label class="day-chip"><input type="checkbox" data-d="3" checked /> Wed</label>
            <label class="day-chip"><input type="checkbox" data-d="4" checked /> Thu</label>
            <label class="day-chip"><input type="checkbox" data-d="5" checked /> Fri</label>
            <label class="day-chip"><input type="checkbox" data-d="6" /> Sat</label>
            <label class="day-chip"><input type="checkbox" data-d="0" /> Sun</label>
          </span>
        </label>
      </div>
      <div id="sch-interval-wrap" hidden>
        <label>Every N seconds <input id="sch-every" type="number" min="60" value="3600" /></label>
      </div>
    `,
    actions: [
      { label: "Cancel", role: "close" },
      { label: "Create", role: "primary", onClick: async () => {
        const name = document.getElementById("sch-name").value.trim();
        const promptText = document.getElementById("sch-prompt").value.trim();
        const kind = document.getElementById("sch-kind").value;
        if (!name || !promptText) { flash("Name and prompt are required.", "warn"); return; }
        let schedule;
        if (kind === "once") {
          const at = document.getElementById("sch-at").value;
          if (!at) { flash("Pick a date/time.", "warn"); return; }
          schedule = { kind: "once", at_iso: new Date(at).toISOString() };
        } else if (kind === "interval") {
          const every = Math.max(60, parseInt(document.getElementById("sch-every").value, 10) || 3600);
          schedule = { kind: "interval", every_seconds: every };
        } else {
          const t = document.getElementById("sch-time").value || "09:00";
          const [h, m] = t.split(":").map((x) => parseInt(x, 10));
          const days = [];
          document.querySelectorAll(".day-chip input").forEach((cb) => {
            if (cb.checked) days.push(parseInt(cb.dataset.d, 10));
          });
          schedule = { kind: "cron-lite", hour: h, minute: m, weekdays: days };
        }
        await api("/api/scheduled-tasks", {
          method: "POST",
          json: { name, prompt: promptText, schedule, enabled: true, workspace_id: state.config?.active_workspace_id || "" },
        });
        closeDialog();
        renderScheduledList();
        flash(`Scheduled "${name}".`, "good");
      } },
    ],
  });
  setTimeout(() => {
    const kindSel = document.getElementById("sch-kind");
    const onChange = () => {
      document.getElementById("sch-once-wrap").hidden = (kindSel.value !== "once");
      document.getElementById("sch-cron-wrap").hidden = (kindSel.value !== "cron-lite");
      document.getElementById("sch-interval-wrap").hidden = (kindSel.value !== "interval");
    };
    kindSel.addEventListener("change", onChange);
    onChange();
  }, 50);
}

// Poll for scheduled-task notifications every 30s.
export async function pollScheduledNotifications() {
  try {
    const data = await api("/api/scheduled-tasks/notifications");
    for (const n of (data.notifications || [])) {
      const icon = n.ok ? "✓" : "⚠";
      flash(`${icon} ${n.name}: ${(n.summary || "").slice(0, 200)}`, n.ok ? "good" : "warn");
      if ("Notification" in window && Notification.permission === "granted") {
        try { new Notification(`BetterWebUI · ${n.name}`, { body: (n.summary || "").slice(0, 200) }); }
        catch (_) {}
      }
    }
  } catch (_) {}
}

// ---------------------------------------------------------------------------
