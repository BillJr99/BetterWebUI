// ESLint flat config for the zero-build frontend (static/*.js) and the Node
// unit tests (tests/js/). Deliberately self-contained: no plugin or preset
// requires, so it runs with any eslint >= 9 (e.g. `npx eslint`) without a
// package.json / node_modules at the repo root.
//
// Rule selection targets genuine errors (undefined/unused names, duplicate
// keys, unreachable code) — NOT reformatting. Style lives in .prettierrc and
// is intentionally not gated so the existing hand-written layout stays put.
"use strict";

const browserGlobals = {
  // DOM / BOM
  window: "readonly",
  document: "readonly",
  navigator: "readonly",
  location: "readonly",
  history: "readonly",
  localStorage: "readonly",
  sessionStorage: "readonly",
  indexedDB: "readonly",
  fetch: "readonly",
  console: "readonly",
  alert: "readonly",
  confirm: "readonly",
  prompt: "readonly",
  atob: "readonly",
  btoa: "readonly",
  setTimeout: "readonly",
  clearTimeout: "readonly",
  setInterval: "readonly",
  clearInterval: "readonly",
  requestAnimationFrame: "readonly",
  getComputedStyle: "readonly",
  crypto: "readonly",
  performance: "readonly",
  // Constructors
  Blob: "readonly",
  File: "readonly",
  FileReader: "readonly",
  FormData: "readonly",
  URL: "readonly",
  URLSearchParams: "readonly",
  Image: "readonly",
  Audio: "readonly",
  AbortController: "readonly",
  TextDecoder: "readonly",
  TextEncoder: "readonly",
  MutationObserver: "readonly",
  ResizeObserver: "readonly",
  IntersectionObserver: "readonly",
  CustomEvent: "readonly",
  Event: "readonly",
  KeyboardEvent: "readonly",
  Element: "readonly",
  ImageCapture: "readonly",
  Notification: "readonly",
  // Speech APIs (voice input / read-aloud)
  SpeechRecognition: "readonly",
  webkitSpeechRecognition: "readonly",
  speechSynthesis: "readonly",
  SpeechSynthesisUtterance: "readonly",
  // CDN-loaded libraries (see static/index.html)
  marked: "readonly",
  DOMPurify: "readonly",
  katex: "readonly",
  renderMathInElement: "readonly",
  // CommonJS export guard in lib.js / browser-store.js
  module: "readonly",
};

// Globals our own classic scripts define for each other (load order in
// index.html: lib.js -> browser-store.js -> app.js).
const appGlobals = {
  // static/lib.js
  escape: "readonly",
  humanLabelForTool: "readonly",
  friendlyError: "readonly",
  fillTemplate: "readonly",
  parseSSEBlock: "readonly",
  // static/browser-store.js
  bws: "readonly",
};

const errorRules = {
  "no-undef": "error",
  "no-unused-vars": [
    "error",
    // args/caught errors stay callback-shaped in this codebase; only flag
    // genuinely dead variables and functions.
    { vars: "all", args: "none", caughtErrors: "none" },
  ],
  "no-redeclare": "error",
  "no-dupe-keys": "error",
  "no-dupe-args": "error",
  "no-dupe-else-if": "error",
  "no-duplicate-case": "error",
  "no-unreachable": "error",
  "no-unsafe-negation": "error",
  "no-compare-neg-zero": "error",
  "no-cond-assign": ["error", "except-parens"],
  "no-constant-condition": ["error", { checkLoops: false }], // while(true) SSE reader loops
  "no-self-assign": "error",
  "no-sparse-arrays": "error",
  "use-isnan": "error",
  "valid-typeof": "error",
  "no-var": "error",
  "prefer-const": ["error", { destructuring: "all" }],
  eqeqeq: ["error", "smart"],
};

module.exports = [
  {
    // Classic scripts: lib.js (pure helpers, also require()-able from Node
    // tests) and browser-store.js (defines the `bws` global). Loaded before
    // the module graph so their globals are visible inside every module.
    files: ["static/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script",
      globals: { ...browserGlobals, ...appGlobals },
    },
    rules: errorRules,
  },
  {
    // Native ES modules (zero-build). Split out of the former static/app.js.
    files: ["static/js/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: { ...browserGlobals, ...appGlobals },
    },
    rules: {
      ...errorRules,
      // Imported bindings are read-only live views; assigning to one is a
      // runtime TypeError, so catch it statically.
      "no-import-assign": "error",
    },
  },
  {
    files: ["tests/js/**/*.js", "eslint.config.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "commonjs",
      globals: {
        require: "readonly",
        module: "writable",
        __dirname: "readonly",
        console: "readonly",
        process: "readonly",
      },
    },
    rules: errorRules,
  },
];
