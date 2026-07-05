// Unit tests for static/lib.js — run with `node --test tests/js/`.
// Plain node:test + node:assert; no bundler, no browser.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const lib = require(path.join(__dirname, "..", "..", "static", "lib.js"));

// ---------------------------------------------------------------------------
// escape
// ---------------------------------------------------------------------------

test("escape: escapes HTML-significant characters", () => {
  assert.equal(lib.escape('<b a="c">&x</b>'), "&lt;b a=&quot;c&quot;&gt;&amp;x&lt;/b&gt;");
});

test("escape: stringifies non-strings and passes plain text through", () => {
  assert.equal(lib.escape(42), "42");
  assert.equal(lib.escape("hello world"), "hello world");
});

test("escape: null/undefined become the empty string", () => {
  assert.equal(lib.escape(null), "");
  assert.equal(lib.escape(undefined), "");
});

// ---------------------------------------------------------------------------
// humanLabelForTool
// ---------------------------------------------------------------------------

test("humanLabelForTool: maps known tool names to friendly verbs", () => {
  assert.equal(lib.humanLabelForTool("execute_shell"), "Running a command");
  assert.equal(lib.humanLabelForTool("web_search"), "Searching the web");
  assert.equal(lib.humanLabelForTool("clk_research"), "Researching");
});

test("humanLabelForTool: passes unknown tool names through unchanged", () => {
  assert.equal(lib.humanLabelForTool("my_custom_tool"), "my_custom_tool");
});

// ---------------------------------------------------------------------------
// friendlyError
// ---------------------------------------------------------------------------

test("friendlyError: maps HTTP statuses to actionable text", () => {
  assert.match(lib.friendlyError({ status: 401, message: "401: nope" }), /API key/);
  assert.match(lib.friendlyError({ status: 404, message: "404: gone" }), /Not found/);
  assert.match(lib.friendlyError({ status: 429, message: "429" }), /rate-limiting/);
  assert.match(lib.friendlyError({ status: 503, message: "boom" }), /server hit an error/);
});

test("friendlyError: recognises network failures from message text", () => {
  assert.match(lib.friendlyError(new TypeError("Failed to fetch")), /reach the server/);
});

test("friendlyError: includes the context clause when provided", () => {
  assert.match(lib.friendlyError({ status: 404 }, "loading models"), / while loading models/);
});

test("friendlyError: falls back to trimmed raw text, capped at 200 chars", () => {
  assert.equal(lib.friendlyError({ message: "418: teapot overflow" }), "teapot overflow");
  const long = "x".repeat(500);
  assert.equal(lib.friendlyError({ message: long }).length, 200);
  assert.equal(lib.friendlyError(null), "Something went wrong.");
});

// ---------------------------------------------------------------------------
// fillTemplate
// ---------------------------------------------------------------------------

test("fillTemplate: substitutes {key} placeholders", () => {
  assert.equal(lib.fillTemplate("run {cmd} on {host}", { cmd: "ls", host: "web1" }), "run ls on web1");
});

test("fillTemplate: unknown keys become empty strings", () => {
  assert.equal(lib.fillTemplate("a{missing}b", {}), "ab");
});

test("fillTemplate: non-string templates are stringified", () => {
  assert.equal(lib.fillTemplate(123, {}), "123");
});

// ---------------------------------------------------------------------------
// parseSSEBlock
// ---------------------------------------------------------------------------

test("parseSSEBlock: parses event + data lines", () => {
  const r = lib.parseSSEBlock('event: token\ndata: {"text":"hi"}');
  assert.equal(r.event, "token");
  assert.equal(r.data, '{"text":"hi"}');
});

test("parseSSEBlock: defaults the event name to message", () => {
  const r = lib.parseSSEBlock('data: {"a":1}');
  assert.equal(r.event, "message");
  assert.equal(r.data, '{"a":1}');
});

test("parseSSEBlock: concatenates multiple data lines", () => {
  const r = lib.parseSSEBlock('data: {"a":\ndata: 1}');
  assert.equal(r.data, '{"a":1}');
});

test("parseSSEBlock: comment/empty blocks yield empty data", () => {
  assert.equal(lib.parseSSEBlock(": keepalive").data, "");
  assert.equal(lib.parseSSEBlock("").data, "");
});
