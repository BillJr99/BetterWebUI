// static/app.js — retired entry point (Phase 3).
//
// The client was decomposed into native ES modules under /static/js/
// (entry: /static/js/main.js), which index.html now loads via
// <script type="module">. This shim keeps the old /static/app.js URL
// serving real JavaScript so that (a) browsers holding a stale cached
// index.html that still references app.js boot the new module graph, and
// (b) existing consumers of the URL keep getting a 200.
//
// Dynamic import() is legal in classic scripts, so this works even though
// this file itself is not a module.
import("/static/js/main.js");
