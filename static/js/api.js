// api.js — Backend API fetch wrappers and local-download helpers.
// Split out of the former static/app.js (Phase 3); logic unchanged.
import { state } from "./state.js";

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

export async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: opts.json ? { "Content-Type": "application/json" } : {},
    ...opts,
    body: opts.json ? JSON.stringify(opts.json) : opts.body,
  });
  if (!res.ok) {
    const text = await res.text();
    const err = new Error(`${res.status}: ${text}`);
    err.status = res.status;
    err.body = text;
    throw err;
  }
  return res.json();
}

// escape(), humanLabelForTool(), friendlyError(), fillTemplate() and
// parseSSEBlock() live in static/lib.js (pure helpers, unit-tested with node).

// ---------------------------------------------------------------------------
// Local-download helpers
// ---------------------------------------------------------------------------

export function b64ToBlob(b64, mime) {
  const bin = atob(b64);
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  return new Blob([arr], { type: mime || "application/octet-stream" });
}

export function storeFile(blob, filename, mime) {
  const url = URL.createObjectURL(blob);
  state.fileStore[filename] = { url, mime: mime || blob.type || "application/octet-stream", filename };
  return url;
}

export async function fileToContentEntry(file) {
  const isText =
    file.type.startsWith("text/") ||
    /\.(md|markdown|csv|tsv|json|ya?ml|log|txt|py|js|ts|tsx|jsx|html|css|java|c|cpp|h|sh|tex|bib)$/i.test(file.name);
  const entry = {
    filename: file.name,
    content_type: file.type || "application/octet-stream",
    size: file.size,
  };
  if (isText) {
    entry.content = await file.text();
  } else {
    const buf = await file.arrayBuffer();
    let bin = "";
    const bytes = new Uint8Array(buf);
    for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    entry.data_b64 = btoa(bin);
  }
  return entry;
}

// ---------------------------------------------------------------------------
