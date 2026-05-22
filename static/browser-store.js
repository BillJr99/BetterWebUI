// browser-store.js — IndexedDB-backed storage for file bundles and user
// memories. Loaded before app.js. Exposes a small global `bws` namespace.
//
// Why IndexedDB rather than localStorage: file bundles can hold large
// binary blobs (PDFs, images), and localStorage tops out around 5-10 MB
// per origin. IndexedDB allows hundreds of MB on most browsers.
//
// All operations are async; callers should await everything.

(function () {
  const DB_NAME = "betterwebui";
  const DB_VERSION = 1;

  function openDB() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = (e) => {
        const db = req.result;
        if (!db.objectStoreNames.contains("bundles")) {
          db.createObjectStore("bundles", { keyPath: "id" });
        }
        if (!db.objectStoreNames.contains("bundle_files")) {
          const store = db.createObjectStore("bundle_files", { keyPath: "id" });
          store.createIndex("by_bundle", "bundleId", { unique: false });
        }
        if (!db.objectStoreNames.contains("memories")) {
          db.createObjectStore("memories", { keyPath: "id" });
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  function txn(db, stores, mode) {
    return db.transaction(stores, mode);
  }

  function reqAll(req) {
    return new Promise((resolve, reject) => {
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  function uid() {
    return (
      Date.now().toString(36) +
      Math.random().toString(36).slice(2, 10)
    );
  }

  // -----------------------------------------------------------------------
  // Bundles
  // -----------------------------------------------------------------------

  async function bundleList() {
    const db = await openDB();
    const all = await reqAll(txn(db, ["bundles"], "readonly").objectStore("bundles").getAll());
    // Decorate with file counts and total size.
    for (const b of all) {
      const files = await reqAll(
        txn(db, ["bundle_files"], "readonly")
          .objectStore("bundle_files").index("by_bundle").getAll(b.id)
      );
      b.file_count = files.length;
      b.total_bytes = files.reduce((n, f) => n + (f.size || 0), 0);
    }
    all.sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
    return all;
  }

  async function bundleCreate(name, description) {
    const db = await openDB();
    const bundle = {
      id: uid(),
      name: (name || "Untitled").trim() || "Untitled",
      description: (description || "").trim(),
      updatedAt: Date.now(),
    };
    await reqAll(txn(db, ["bundles"], "readwrite").objectStore("bundles").add(bundle));
    return bundle;
  }

  async function bundleUpdate(id, patch) {
    const db = await openDB();
    const store = txn(db, ["bundles"], "readwrite").objectStore("bundles");
    const existing = await reqAll(store.get(id));
    if (!existing) throw new Error(`Bundle ${id} not found`);
    const updated = { ...existing, ...patch, updatedAt: Date.now() };
    await reqAll(store.put(updated));
    return updated;
  }

  async function bundleDelete(id) {
    const db = await openDB();
    const t = txn(db, ["bundles", "bundle_files"], "readwrite");
    const filesIdx = t.objectStore("bundle_files").index("by_bundle");
    const fileKeys = await reqAll(filesIdx.getAllKeys(id));
    for (const k of fileKeys) {
      t.objectStore("bundle_files").delete(k);
    }
    t.objectStore("bundles").delete(id);
    return new Promise((resolve, reject) => {
      t.oncomplete = () => resolve(true);
      t.onerror = () => reject(t.error);
    });
  }

  async function bundleFiles(bundleId) {
    const db = await openDB();
    return reqAll(
      txn(db, ["bundle_files"], "readonly")
        .objectStore("bundle_files").index("by_bundle").getAll(bundleId)
    );
  }

  async function bundleAddFile(bundleId, file) {
    const db = await openDB();
    const buf = await file.arrayBuffer();
    const record = {
      id: uid(),
      bundleId,
      filename: file.name || "file",
      mime: file.type || "application/octet-stream",
      size: file.size,
      blob: new Blob([buf], { type: file.type || "application/octet-stream" }),
      addedAt: Date.now(),
    };
    await reqAll(txn(db, ["bundle_files"], "readwrite").objectStore("bundle_files").add(record));
    await bundleUpdate(bundleId, {});
    return record;
  }

  async function bundleRemoveFile(fileId) {
    const db = await openDB();
    const t = txn(db, ["bundle_files"], "readwrite");
    const rec = await reqAll(t.objectStore("bundle_files").get(fileId));
    if (!rec) return false;
    await reqAll(t.objectStore("bundle_files").delete(fileId));
    if (rec.bundleId) await bundleUpdate(rec.bundleId, {});
    return true;
  }

  async function storageEstimate() {
    if (navigator.storage && navigator.storage.estimate) {
      try { return await navigator.storage.estimate(); } catch (_) { return null; }
    }
    return null;
  }

  // -----------------------------------------------------------------------
  // Memories
  // -----------------------------------------------------------------------

  async function memoryList() {
    const db = await openDB();
    const all = await reqAll(txn(db, ["memories"], "readonly").objectStore("memories").getAll());
    all.sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0));
    return all;
  }

  async function memoryAdd({ text, category = "other", source = "user_added", enabled = true, workspace_ids = null }) {
    const db = await openDB();
    const mem = {
      id: uid(),
      text: (text || "").trim(),
      category,
      source,
      enabled: !!enabled,
      workspace_ids,
      createdAt: Date.now(),
      lastUsedAt: null,
    };
    if (!mem.text) throw new Error("Memory text is required.");
    await reqAll(txn(db, ["memories"], "readwrite").objectStore("memories").add(mem));
    return mem;
  }

  async function memoryUpdate(id, patch) {
    const db = await openDB();
    const store = txn(db, ["memories"], "readwrite").objectStore("memories");
    const existing = await reqAll(store.get(id));
    if (!existing) throw new Error(`Memory ${id} not found`);
    const updated = { ...existing, ...patch };
    await reqAll(store.put(updated));
    return updated;
  }

  async function memoryDelete(id) {
    const db = await openDB();
    await reqAll(txn(db, ["memories"], "readwrite").objectStore("memories").delete(id));
    return true;
  }

  async function memoryEnabledTexts(workspaceId) {
    const all = await memoryList();
    return all
      .filter((m) => m.enabled && m.source !== "auto_extracted_pending")
      .filter((m) => !m.workspace_ids || m.workspace_ids.includes(workspaceId))
      .map((m) => m.text);
  }

  async function memoryPendingCount() {
    const all = await memoryList();
    return all.filter((m) => m.source === "auto_extracted_pending").length;
  }

  window.bws = {
    bundleList, bundleCreate, bundleUpdate, bundleDelete,
    bundleFiles, bundleAddFile, bundleRemoveFile,
    storageEstimate,
    memoryList, memoryAdd, memoryUpdate, memoryDelete,
    memoryEnabledTexts, memoryPendingCount,
  };
})();
