// BetterWebUI pure helpers. No DOM access, no fetch, no app state — every
// function here is a pure(ish) function of its arguments, so they can be
// unit-tested with `node --test tests/js/` without a browser or bundler.
//
// Loaded as a classic <script> BEFORE app.js (see static/index.html), which
// makes each helper a global that app.js calls directly. The module.exports
// guard at the bottom makes the same file require()-able from Node tests.
(function (root) {
  "use strict";

  // Escape a value for safe interpolation into innerHTML.
  function escape(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // Map raw tool names to verbs a non-technical user understands.
  function humanLabelForTool(tool) {
    const map = {
      execute_shell: "Running a command",
      cli_call: "Running a CLI shortcut",
      write_file: "Writing a file",
      delete_file: "Deleting a file",
      read_file: "Reading a file",
      generate_image: "Drawing an image",
      generate_audio: "Generating speech",
      web_search: "Searching the web",
      mcp_call: "Calling a connected service",
      autogui_task: "Controlling the desktop",
      clk_research: "Researching",
      screen_action: "Acting on the screen",
      update_task_plan: "Updating the plan",
      load_skill: "Loading a skill",
    };
    return map[tool] || tool;
  }

  // Map technical errors to language a non-technical user can act on.
  // `raw` is the raw error (Error object or string); `context` is an
  // optional short description ("uploading a file", "loading models").
  function friendlyError(raw, context) {
    const text = (raw && (raw.message || raw.body || raw.toString())) || "";
    const status = raw && raw.status;
    const ctx = context ? ` while ${context}` : "";
    if (status === 401 || /unauthor/i.test(text)) {
      return `Your API key isn't being accepted${ctx}. Open Settings → Connection to update it.`;
    }
    if (status === 403) {
      return `You don't have permission for that${ctx}. Check Settings → Connection.`;
    }
    if (status === 404) {
      return `Not found${ctx}. The item may have been deleted or moved.`;
    }
    if (status === 413 || /too large|payload/i.test(text)) {
      return `That file is too large${ctx}. Try a smaller one.`;
    }
    if (status === 429 || /rate limit|too many/i.test(text)) {
      return `The server is rate-limiting requests${ctx}. Wait a moment and try again.`;
    }
    if (/timeout|timed out|took too long/i.test(text)) {
      return `The server took too long to respond${ctx}. Try again, or pick a smaller task.`;
    }
    if (/network|failed to fetch|ECONN|ENOTFOUND/i.test(text)) {
      return `Couldn't reach the server${ctx}. Check your internet connection.`;
    }
    if (/invalid data|broken|undecodable/i.test(text)) {
      return `The result came back broken${ctx}. Click "Try again" or rephrase.`;
    }
    if (status >= 500) {
      return `The server hit an error${ctx}. Try again in a moment.`;
    }
    // Last resort: trim and de-jargon the raw text.
    return text.replace(/^\d+:\s*/, "").slice(0, 200) || `Something went wrong${ctx}.`;
  }

  // Fill "{key}" placeholders in a template string from a values object.
  // Unknown keys become "".
  function fillTemplate(tpl, values) {
    return String(tpl).replace(/\{(\w+)\}/g, (_, k) => values[k] ?? "");
  }

  // Parse one SSE block (the text between two "\n\n" separators) into its
  // event name and concatenated data payload. Mirrors what consumeSSE in
  // app.js feeds to its onEvent callback.
  function parseSSEBlock(block) {
    const lines = String(block).split("\n");
    let eventName = "message";
    let dataStr = "";
    for (const ln of lines) {
      if (ln.startsWith("event:")) eventName = ln.slice(6).trim();
      else if (ln.startsWith("data:")) dataStr += ln.slice(5).trim();
    }
    return { event: eventName, data: dataStr };
  }

  const lib = { escape, humanLabelForTool, friendlyError, fillTemplate, parseSSEBlock };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = lib; // Node (unit tests)
  }
  if (root) {
    Object.assign(root, lib); // Browser: expose as plain globals for app.js
  }
})(typeof window !== "undefined" ? window : null);
