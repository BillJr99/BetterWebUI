// tools_tab.js — Tools tab: MCP server and CLI shortcut management.
// Split out of the former static/app.js (Phase 3); logic unchanged.
import { api } from "./api.js";
import { closeDialog, flash, showDialog } from "./dialogs.js";
import { $, state } from "./state.js";

// MCP servers (Tools tab)
// ---------------------------------------------------------------------------

export async function loadMcp() {
  const [servers, registry] = await Promise.all([
    api("/api/mcp/servers"),
    api("/api/mcp/registry"),
  ]);
  state.mcpServers = servers.servers || [];
  state.mcpRegistry = registry.registry || [];
  renderMcpServers();
}

export function renderMcpServers() {
  const ul = $("#mcp-server-list");
  ul.innerHTML = "";
  if (!state.mcpServers.length) {
    ul.innerHTML = `<p class="hint" style="padding:8px;">No MCP servers configured.</p>`;
    return;
  }
  for (const s of state.mcpServers) {
    const li = document.createElement("li");
    const dot = s.running ? "good" : (s.error ? "warn" : "muted");
    li.innerHTML = `
      <div class="list-item-title">
        <div><span class="status-dot ${dot}"></span>${escape(s.name)}
          ${s.running ? `<small>(${s.tool_count} tool${s.tool_count === 1 ? "" : "s"})</small>` : ""}
        </div>
        <div class="list-item-desc">${escape(s.description || "")}${
          s.error ? ` <span class="warn-text">${escape(s.error)}</span>` : ""
        }</div>
      </div>
      <div class="list-actions">
        <button data-action="tools" data-name="${escape(s.name)}">Tools</button>
        <button data-action="delete" data-name="${escape(s.name)}">Remove</button>
      </div>`;
    ul.appendChild(li);
  }
  ul.onclick = async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const name = btn.dataset.name;
    if (btn.dataset.action === "delete") {
      if (!confirm(`Remove MCP server '${name}'?`)) return;
      await api(`/api/mcp/servers/${encodeURIComponent(name)}`, { method: "DELETE" });
      await loadMcp();
    }
    if (btn.dataset.action === "tools") {
      const s = state.mcpServers.find((x) => x.name === name);
      const tools = (s?.tools || []).map((t) => `<li><b>${escape(t.name)}</b> — <small>${escape(t.description || "")}</small></li>`).join("");
      showDialog({
        title: `${name} — exposed tools`,
        body: tools ? `<ul class="plain-list">${tools}</ul>` : `<p class="hint">No tools (server may still be starting, or in error).</p>`,
        actions: [{ label: "Close", action: "cancel" }],
      });
    }
  };
}

export const _CLOUD_SERVICE_META = {
  "google": { icon: "&#128241;", label: "Google", desc: "Calendar, Gmail, Drive" },
  "microsoft": { icon: "&#128203;", label: "Microsoft 365", desc: "Outlook, Teams, OneDrive" },
};

export async function renderCloudServices() {
  const wrap = $("#cloud-services-list");
  if (!wrap) return;
  wrap.innerHTML = "";
  const grid = document.createElement("div");
  grid.className = "cloud-services-grid";
  for (const [provider, meta] of Object.entries(_CLOUD_SERVICE_META)) {
    const card = document.createElement("div");
    card.className = "cloud-service-card";
    let statusHtml = '<span class="cloud-status-chip disconnected">Not connected</span>';
    let btnHtml = `<button class="primary" data-action="connect" data-provider="${provider}">Connect</button>`;
    try {
      const status = await api(`/api/oauth/status/${provider}`);
      if (status.connected) {
        const label = status.expired ? "Token expired" : "Connected";
        const cls = status.expired ? "expired" : "connected";
        const emailPart = status.email ? ` <small>${escape(status.email)}</small>` : "";
        statusHtml = `<span class="cloud-status-chip ${cls}">${label}</span>${emailPart}`;
        btnHtml = `<button data-action="reconnect" data-provider="${provider}">Reconnect</button>
          <button data-action="disconnect" data-provider="${provider}" class="danger-btn">Disconnect</button>`;
      }
    } catch (_) { /* not connected */ }
    card.innerHTML = `
      <div class="cloud-service-header">
        <span class="cloud-service-icon">${meta.icon}</span>
        <div>
          <div class="cloud-service-name">${meta.label}</div>
          <div class="cloud-service-desc hint">${meta.desc}</div>
        </div>
      </div>
      <div class="cloud-service-status">${statusHtml}</div>
      <div class="cloud-service-actions">${btnHtml}</div>`;
    card.addEventListener("click", async (e) => {
      const btn = e.target.closest("button[data-action]");
      if (!btn) return;
      const action = btn.dataset.action;
      const prov = btn.dataset.provider;
      if (action === "disconnect") {
        await api(`/api/oauth/disconnect/${prov}`, { method: "DELETE" });
        renderCloudServices();
        return;
      }
      if (action === "connect" || action === "reconnect") {
        try {
          const resp = await api(`/api/oauth/connect/${prov}`, { method: "POST" });
          if (resp.auth_url) {
            window.open(resp.auth_url, "_blank");
            flash(`Sign in to ${_CLOUD_SERVICE_META[prov]?.label || prov} in the opened tab, then return here.`, "info");
            // Poll for completion
            let tries = 0;
            const poll = setInterval(async () => {
              tries++;
              if (tries > 60) { clearInterval(poll); return; }
              try {
                const st = await api(`/api/oauth/status/${prov}`);
                if (st.connected && !st.expired) {
                  clearInterval(poll);
                  flash(`Connected to ${_CLOUD_SERVICE_META[prov]?.label || prov}!`, "good");
                  renderCloudServices();
                }
              } catch (_) {}
            }, 2000);
          }
        } catch (err) {
          flash(friendlyError(err, `connecting to ${_CLOUD_SERVICE_META[prov]?.label || prov}`), "warn");
        }
      }
    });
    grid.appendChild(card);
  }
  wrap.appendChild(grid);
}

export function openMcpRegistryDialog() {
  const items = state.mcpRegistry
    .map(
      (r, i) => `
      <div class="registry-card" data-i="${i}">
        <h3>${escape(r.name)}</h3>
        <p class="hint">${escape(r.description)}</p>
        <p class="hint"><b>Requires:</b> ${escape(r.requires || "—")}</p>
        <button class="primary" data-i="${i}">Add</button>
      </div>`,
    )
    .join("");
  showDialog({
    title: "Add an MCP server",
    wide: true,
    body: `<div class="registry-grid">${items}</div>`,
    actions: [{ label: "Close", action: "cancel" }],
  });
  document.querySelectorAll(".registry-card button").forEach((btn) => {
    btn.onclick = () => {
      const r = state.mcpRegistry[+btn.dataset.i];
      openMcpFieldsDialog(r);
    };
  });
}

export function openMcpFieldsDialog(reg) {
  const fields = reg.fields || [];
  const fieldHtml =
    fields
      .map(
        (f) => `
      <label>${escape(f.label)}
        <input id="dlg-f-${f.name}" type="${f.type === "password" ? "password" : "text"}" />
      </label>`,
      )
      .join("") || `<p class="hint">No additional configuration needed.</p>`;
  showDialog({
    title: `Add: ${reg.name}`,
    body: `
      <p class="hint">${escape(reg.description)}</p>
      <p class="hint"><b>Requires:</b> ${escape(reg.requires || "—")}</p>
      ${fieldHtml}
      <label>Server name
        <input id="dlg-name" type="text" value="${escape(reg.id)}" />
        <small>Used to refer to this server in tool calls and workspaces.</small>
      </label>
    `,
    actions: [
      { label: "Cancel", action: "cancel" },
      {
        label: "Add",
        primary: true,
        action: async () => {
          const values = {};
          for (const f of fields) {
            values[f.name] = $(`#dlg-f-${f.name}`).value.trim();
            if (!values[f.name]) {
              flash(`${f.label} is required.`, "warn");
              return;
            }
          }
          const args = (reg.args_template || []).map((a) => fillTemplate(a, values));
          const env = Object.fromEntries(
            Object.entries(reg.env_template || {}).map(([k, v]) => [k, fillTemplate(v, values)]),
          );
          const body = {
            name: $("#dlg-name").value.trim() || reg.id,
            command: reg.command,
            args,
            env,
            description: reg.description,
            enabled: true,
          };
          await api("/api/mcp/servers", { method: "POST", json: body });
          await loadMcp();
          closeDialog();
          flash("MCP server added — bringing it up may take a moment.", "good");
        },
      },
    ],
  });
}

export function openMcpCustomDialog() {
  showDialog({
    title: "Custom MCP server",
    body: `
      <label>Name <input id="dlg-name" type="text" placeholder="my-server" /></label>
      <label>Command <input id="dlg-cmd" type="text" placeholder="npx, uvx, python, /path/to/binary" /></label>
      <label>Arguments (one per line)
        <textarea id="dlg-args" rows="4" placeholder="-y\\n@my-org/my-mcp-server"></textarea>
      </label>
      <label>Environment variables (KEY=value, one per line)
        <textarea id="dlg-env" rows="3"></textarea>
      </label>
      <label>Description <input id="dlg-desc" type="text" /></label>
    `,
    actions: [
      { label: "Cancel", action: "cancel" },
      {
        label: "Add",
        primary: true,
        action: async () => {
          const env = {};
          for (const line of $("#dlg-env").value.split("\n")) {
            const m = line.match(/^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$/);
            if (m) env[m[1]] = m[2];
          }
          const body = {
            name: $("#dlg-name").value.trim(),
            command: $("#dlg-cmd").value.trim(),
            args: $("#dlg-args").value.split("\n").map((s) => s.trim()).filter(Boolean),
            env,
            description: $("#dlg-desc").value.trim(),
            enabled: true,
          };
          if (!body.name || !body.command) return;
          await api("/api/mcp/servers", { method: "POST", json: body });
          await loadMcp();
          closeDialog();
        },
      },
    ],
  });
}

// ---------------------------------------------------------------------------
// CLI shortcuts (Tools tab)
// ---------------------------------------------------------------------------

export async function loadCli() {
  const [tools, registry] = await Promise.all([
    api("/api/cli/tools"),
    api("/api/cli/registry"),
  ]);
  state.cliTools = tools.tools || [];
  state.cliRegistry = registry.registry || [];
  renderCliTools();
}

export function renderCliTools() {
  const ul = $("#cli-tool-list");
  ul.innerHTML = "";
  if (!state.cliTools.length) {
    ul.innerHTML = `<p class="hint" style="padding:8px;">No CLI shortcuts yet.</p>`;
    return;
  }
  for (const c of state.cliTools) {
    const li = document.createElement("li");
    li.innerHTML = `
      <div class="list-item-title">
        <div>${escape(c.name)} <small>(${escape(c.id)})</small></div>
        <div class="list-item-desc">${escape(c.description || "")}</div>
        <div class="list-item-meta"><code>${escape(c.command_template || "")}</code></div>
      </div>
      <div class="list-actions">
        <button data-action="delete" data-id="${escape(c.id)}">Remove</button>
      </div>`;
    ul.appendChild(li);
  }
  ul.onclick = async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    if (btn.dataset.action === "delete") {
      await api(`/api/cli/tools/${encodeURIComponent(btn.dataset.id)}`, { method: "DELETE" });
      await loadCli();
    }
  };
}

export function openCliRegistryDialog() {
  const items = state.cliRegistry
    .map(
      (r, i) => `
      <div class="registry-card" data-i="${i}">
        <h3>${escape(r.name)}</h3>
        <p class="hint">${escape(r.description)}</p>
        <p class="hint"><code>${escape(r.command_template)}</code></p>
        <button class="primary" data-i="${i}">Add</button>
      </div>`,
    )
    .join("");
  showDialog({
    title: "Add a CLI shortcut",
    wide: true,
    body: `<div class="registry-grid">${items}</div>`,
    actions: [{ label: "Close", action: "cancel" }],
  });
  document.querySelectorAll(".registry-card button").forEach((btn) => {
    btn.onclick = async () => {
      const r = state.cliRegistry[+btn.dataset.i];
      await api("/api/cli/tools", { method: "POST", json: r });
      await loadCli();
      closeDialog();
    };
  });
}

export function openCliCustomDialog() {
  showDialog({
    title: "Custom CLI shortcut",
    body: `
      <label>ID <input id="dlg-id" type="text" placeholder="summarize-pdf" /></label>
      <label>Name <input id="dlg-name" type="text" placeholder="Summarize PDF" /></label>
      <label>Description <input id="dlg-desc" type="text" placeholder="When to use this shortcut" /></label>
      <label>Command template
        <input id="dlg-cmd" type="text" placeholder="pandoc {args}" />
        <small>Use <code>{args}</code> as the placeholder for arguments the assistant fills in.</small>
      </label>
    `,
    actions: [
      { label: "Cancel", action: "cancel" },
      {
        label: "Add",
        primary: true,
        action: async () => {
          const body = {
            id: $("#dlg-id").value.trim(),
            name: $("#dlg-name").value.trim(),
            description: $("#dlg-desc").value.trim(),
            command_template: $("#dlg-cmd").value.trim(),
          };
          if (!body.id || !body.name || !body.command_template) return;
          await api("/api/cli/tools", { method: "POST", json: body });
          await loadCli();
          closeDialog();
        },
      },
    ],
  });
}

// ---------------------------------------------------------------------------
