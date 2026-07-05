// composer.js — Composer attachments, the image-annotation modal, and voice input.
// Split out of the former static/app.js (Phase 3); logic unchanged.
import { flash } from "./dialogs.js";
import { $, state } from "./state.js";

// Composer / attachments
// ---------------------------------------------------------------------------

export async function attachFile(file) {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/api/upload", { method: "POST", body: fd });
  if (!res.ok) {
    const err = new Error(`${res.status}: ${await res.text()}`);
    err.status = res.status;
    flash(friendlyError(err, "uploading the file"), "warn");
    return;
  }
  const a = await res.json();
  state.attachments.push(a);
  renderAttachments();
}

// ---------------------------------------------------------------------------
// Image annotation modal
// ---------------------------------------------------------------------------

export const _anno = {
  attachIdx: -1,
  tool: "freehand",
  color: "#ff3b30",
  size: 3,
  strokes: [],
  drawing: false,
  startX: 0,
  startY: 0,
  img: null,
  offscreen: null, // offscreen canvas with base image only
};

export function openAnnotationModal(attachIdx) {
  const a = state.attachments[attachIdx];
  if (!a) return;
  const modal = $("#annotation-modal");
  if (!modal) return;
  _anno.attachIdx = attachIdx;
  _anno.strokes = [];
  _anno.img = null;
  _anno.offscreen = null;
  // Load the image
  const imgEl = new Image();
  imgEl.onload = () => {
    _anno.img = imgEl;
    const canvas = $("#annotation-canvas");
    canvas.width = imgEl.naturalWidth;
    canvas.height = imgEl.naturalHeight;
    const offscreen = document.createElement("canvas");
    offscreen.width = imgEl.naturalWidth;
    offscreen.height = imgEl.naturalHeight;
    offscreen.getContext("2d").drawImage(imgEl, 0, 0);
    _anno.offscreen = offscreen;
    _annoRedraw(canvas);
    modal.hidden = false;
  };
  imgEl.onerror = () => flash("Could not load image for annotation.", "warn");
  if (a.url) {
    imgEl.src = a.url;
  } else if (a._blob) {
    imgEl.src = URL.createObjectURL(a._blob);
  } else {
    flash("Image data not available for annotation.", "warn");
    return;
  }
}

export function _annoRedraw(canvas) {
  const ctx = canvas.getContext("2d");
  if (_anno.offscreen) ctx.drawImage(_anno.offscreen, 0, 0);
  for (const s of _anno.strokes) _annoDrawStroke(ctx, s, false);
}

export function _annoDrawStroke(ctx, s, preview) {
  ctx.save();
  ctx.strokeStyle = s.color;
  ctx.lineWidth = s.size;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  if (s.tool === "freehand" && s.points && s.points.length > 1) {
    ctx.beginPath();
    ctx.moveTo(s.points[0].x, s.points[0].y);
    for (let i = 1; i < s.points.length; i++) ctx.lineTo(s.points[i].x, s.points[i].y);
    ctx.stroke();
  } else if (s.tool === "rect" && !preview) {
    ctx.beginPath();
    ctx.strokeRect(s.x, s.y, s.w, s.h);
  } else if (s.tool === "rect" && preview) {
    ctx.beginPath();
    ctx.strokeRect(s.x, s.y, s.w, s.h);
  } else if (s.tool === "arrow") {
    const { x, y, x2, y2 } = s;
    const angle = Math.atan2(y2 - y, x2 - x);
    const headLen = Math.max(12, s.size * 4);
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.lineTo(x2, y2);
    ctx.lineTo(x2 - headLen * Math.cos(angle - Math.PI / 6), y2 - headLen * Math.sin(angle - Math.PI / 6));
    ctx.moveTo(x2, y2);
    ctx.lineTo(x2 - headLen * Math.cos(angle + Math.PI / 6), y2 - headLen * Math.sin(angle + Math.PI / 6));
    ctx.stroke();
  }
  ctx.restore();
}

export function _annoCanvasPos(canvas, e) {
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / rect.width;
  const scaleY = canvas.height / rect.height;
  const src = e.touches ? e.touches[0] : e;
  return {
    x: (src.clientX - rect.left) * scaleX,
    y: (src.clientY - rect.top) * scaleY,
  };
}

export function _initAnnotationCanvas() {
  const canvas = $("#annotation-canvas");
  if (!canvas || canvas._annoInited) return;
  canvas._annoInited = true;

  const onStart = (e) => {
    if (!_anno.img) return;
    e.preventDefault();
    _anno.drawing = true;
    const pos = _annoCanvasPos(canvas, e);
    _anno.startX = pos.x;
    _anno.startY = pos.y;
    if (_anno.tool === "freehand") {
      _anno.strokes.push({ tool: "freehand", color: _anno.color, size: _anno.size, points: [pos] });
    }
  };
  const onMove = (e) => {
    if (!_anno.drawing || !_anno.img) return;
    e.preventDefault();
    const pos = _annoCanvasPos(canvas, e);
    const ctx = canvas.getContext("2d");
    _annoRedraw(canvas);
    if (_anno.tool === "freehand") {
      const stroke = _anno.strokes[_anno.strokes.length - 1];
      stroke.points.push(pos);
      _annoDrawStroke(ctx, stroke, true);
    } else if (_anno.tool === "rect") {
      _annoDrawStroke(ctx, {
        tool: "rect", color: _anno.color, size: _anno.size,
        x: _anno.startX, y: _anno.startY,
        w: pos.x - _anno.startX, h: pos.y - _anno.startY,
      }, true);
    } else if (_anno.tool === "arrow") {
      _annoDrawStroke(ctx, {
        tool: "arrow", color: _anno.color, size: _anno.size,
        x: _anno.startX, y: _anno.startY, x2: pos.x, y2: pos.y,
      }, true);
    }
  };
  const onEnd = (e) => {
    if (!_anno.drawing || !_anno.img) return;
    _anno.drawing = false;
    const pos = e.changedTouches
      ? _annoCanvasPos(canvas, { clientX: e.changedTouches[0].clientX, clientY: e.changedTouches[0].clientY })
      : _annoCanvasPos(canvas, e);
    if (_anno.tool === "rect") {
      _anno.strokes.push({
        tool: "rect", color: _anno.color, size: _anno.size,
        x: _anno.startX, y: _anno.startY,
        w: pos.x - _anno.startX, h: pos.y - _anno.startY,
      });
    } else if (_anno.tool === "arrow") {
      _anno.strokes.push({
        tool: "arrow", color: _anno.color, size: _anno.size,
        x: _anno.startX, y: _anno.startY, x2: pos.x, y2: pos.y,
      });
    }
    _annoRedraw(canvas);
  };

  canvas.addEventListener("mousedown", onStart);
  canvas.addEventListener("mousemove", onMove);
  canvas.addEventListener("mouseup", onEnd);
  canvas.addEventListener("touchstart", onStart, { passive: false });
  canvas.addEventListener("touchmove", onMove, { passive: false });
  canvas.addEventListener("touchend", onEnd);
}

export async function _applyAnnotation() {
  const canvas = $("#annotation-canvas");
  const a = state.attachments[_anno.attachIdx];
  if (!canvas || !a) return;
  const blob = await new Promise((res) => canvas.toBlob(res, "image/png"));
  if (!blob) { flash("Could not export annotated image.", "warn"); return; }
  // Upload to server
  const form = new FormData();
  const name = (a.filename || "annotated").replace(/\.[^.]+$/, "") + "_annotated.png";
  form.append("file", blob, name);
  try {
    const resp = await fetch("/api/upload", { method: "POST", body: form });
    if (!resp.ok) throw new Error(await resp.text());
    const data = await resp.json();
    // Replace the original attachment with the annotated one
    state.attachments[_anno.attachIdx] = {
      url: data.url,
      filename: data.filename || name,
      content_type: "image/png",
    };
    renderAttachments();
    flash("Annotated image attached.", "good");
  } catch (err) {
    flash(friendlyError(err, "uploading annotated image"), "warn");
  }
  $("#annotation-modal").hidden = true;
}

export function _initAnnotationModal() {
  _initAnnotationCanvas();
  document.querySelectorAll(".anno-tool-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      _anno.tool = btn.dataset.tool;
      document.querySelectorAll(".anno-tool-btn").forEach((b) => b.classList.toggle("active", b === btn));
    });
  });
  $("#anno-color")?.addEventListener("input", (e) => { _anno.color = e.target.value; });
  $("#anno-size")?.addEventListener("input", (e) => { _anno.size = +e.target.value; });
  $("#anno-undo-btn")?.addEventListener("click", () => {
    _anno.strokes.pop();
    _annoRedraw($("#annotation-canvas"));
  });
  $("#anno-clear-btn")?.addEventListener("click", () => {
    _anno.strokes = [];
    _annoRedraw($("#annotation-canvas"));
  });
  $("#anno-apply-btn")?.addEventListener("click", _applyAnnotation);
  $("#anno-cancel-btn")?.addEventListener("click", () => { $("#annotation-modal").hidden = true; });
}

export async function captureScreenshot() {
  let stream;
  try {
    stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
  } catch (e) {
    flash(friendlyError(e, "capturing the screen"), "warn");
    return;
  }
  try {
    const track = stream.getVideoTracks()[0];
    // Briefly wait so the browser has time to render the first frame.
    await new Promise((r) => setTimeout(r, 200));
    const settings = track.getSettings();
    const w = settings.width || 1280;
    const h = settings.height || 720;
    // ImageCapture is the most reliable path in Chromium; fall back to a
    // video-element draw for Firefox.
    let blob;
    if (typeof ImageCapture !== "undefined") {
      const cap = new ImageCapture(track);
      const bitmap = await cap.grabFrame();
      const canvas = document.createElement("canvas");
      canvas.width = bitmap.width; canvas.height = bitmap.height;
      canvas.getContext("2d").drawImage(bitmap, 0, 0);
      blob = await new Promise((r) => canvas.toBlob(r, "image/png"));
    } else {
      const video = document.createElement("video");
      video.srcObject = stream;
      await video.play();
      const canvas = document.createElement("canvas");
      canvas.width = w; canvas.height = h;
      canvas.getContext("2d").drawImage(video, 0, 0, w, h);
      blob = await new Promise((r) => canvas.toBlob(r, "image/png"));
    }
    if (!blob) {
      flash("Screenshot capture produced no image.", "warn");
      return;
    }
    const file = new File([blob], `screenshot-${Date.now()}.png`, { type: "image/png" });
    await attachFile(file);
    const vis = $("#toggle-vision"); if (vis) vis.checked = true;
    flash("Screenshot attached.", "good");
  } finally {
    stream.getTracks().forEach((t) => t.stop());
  }
}

export function renderAttachments() {
  const wrap = $("#attachments-preview");
  wrap.innerHTML = "";
  state.attachments.forEach((a, i) => {
    const span = document.createElement("span");
    span.className = "pill";
    const isImage = a.content_type && a.content_type.startsWith("image/");
    const annoBtn = isImage
      ? `<button class="anno-open-btn" data-i="${i}" title="Annotate this image" aria-label="Annotate ${escape(a.filename)}">&#9998;</button> `
      : "";
    span.innerHTML = `${annoBtn}${escape(a.filename)} <button data-rm="${i}" title="Remove" aria-label="Remove ${escape(a.filename)}">&#215;</button>`;
    span.querySelector(`button[data-rm]`).onclick = () => {
      state.attachments.splice(i, 1);
      renderAttachments();
    };
    const annoOpenBtn = span.querySelector(".anno-open-btn");
    if (annoOpenBtn) {
      annoOpenBtn.onclick = () => openAnnotationModal(i);
    }
    wrap.appendChild(span);
  });
}

// ---------------------------------------------------------------------------
// Voice input (SpeechRecognition)
// ---------------------------------------------------------------------------

export let recognition = null;

export function initMic() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    const btn = $("#mic-btn");
    if (btn) { btn.hidden = true; }
    return;
  }
  recognition = new SpeechRecognition();
  recognition.continuous = false;
  recognition.interimResults = true;
  recognition.lang = navigator.language || "en-US";

  recognition.onresult = (e) => {
    let transcript = "";
    for (let i = e.resultIndex; i < e.results.length; i++) {
      transcript += e.results[i][0].transcript;
    }
    const input = $("#composer-input");
    if (input) input.value = transcript;
  };

  recognition.onend = () => {
    state.micListening = false;
    const btn = $("#mic-btn");
    if (btn) { btn.classList.remove("listening"); btn.setAttribute("aria-pressed", "false"); }
  };

  recognition.onerror = (e) => {
    state.micListening = false;
    const btn = $("#mic-btn");
    if (btn) { btn.classList.remove("listening"); btn.setAttribute("aria-pressed", "false"); }
    if (e.error !== "no-speech") flash("Microphone error: " + e.error, "warn");
  };
}

export function toggleMic() {
  if (!recognition) {
    // Fallback: proxy to OpenWebUI transcription
    flash("Voice input is not supported in this browser.", "warn");
    return;
  }
  const btn = $("#mic-btn");
  if (state.micListening) {
    recognition.stop();
  } else {
    recognition.start();
    state.micListening = true;
    if (btn) { btn.classList.add("listening"); btn.setAttribute("aria-pressed", "true"); }
  }
}

// ---------------------------------------------------------------------------
