// state.js — Shared client state and DOM query helpers. Imported by every other module; imports nothing.
// Split out of the former static/app.js (Phase 3); logic unchanged.

export const $ = (sel) => document.querySelector(sel);
export const $$ = (sel) => Array.from(document.querySelectorAll(sel));

export const state = {
  config: null,
  models: [],
  prompts: [],
  skills: [],
  conversations: [],
  workspaces: [],
  mcpServers: [],
  mcpRegistry: [],
  cliTools: [],
  cliRegistry: [],
  currentConversationId: null,
  messages: [],
  attachments: [],
  busy: false,
  fileStore: {},
  taskPlan: [],          // current plan items from backend
  convSearchQuery: "",   // conversation search filter
  convSearchResults: null, // server-side full-text search results (null = not active)
  micListening: false,   // voice input state
  rightRailVisible: false,
  planPaneVisible: false,
  filesPaneVisible: false,
  // last-turn telemetry
  lastTelemetry: null,
  // Callback invoked by global Escape to cancel a pending modal (askApproval / handleFileRequest / diff)
  pendingDialogCancel: null,
};
