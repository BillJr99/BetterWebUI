#!/usr/bin/env python3
"""
BetterWebUI interactive setup wizard.

Reads deploy/.env, validates all required settings against a live
OpenWebUI instance, prompts for anything missing or broken, then writes
results back.  Uses a curses-based scrollable menu for model selection
on Unix/macOS; falls back to a numbered list on Windows or non-TTY
environments.

Usage:
    python3 scripts/setup_wizard.py               # validate; prompt only if needed
    python3 scripts/setup_wizard.py --reconfigure # always re-prompt everything

Exit codes:
    0  – configuration saved successfully (or was already valid)
    1  – user aborted
"""

import curses
import json
import pathlib
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / "deploy" / ".env"

_IS_TTY = sys.stdout.isatty() and sys.stdin.isatty()
_IS_WIN = sys.platform == "win32"


# ── ANSI colour helpers ────────────────────────────────────────────────────────

def _c(code: str, t: str) -> str:
    return f"\033[{code}m{t}\033[0m" if _IS_TTY and not _IS_WIN else t

def bold(t):   return _c("1", t)
def green(t):  return _c("32", t)
def red(t):    return _c("31", t)
def yellow(t): return _c("33", t)
def cyan(t):   return _c("36", t)
def dim(t):    return _c("2", t)


# ── .env file I/O ──────────────────────────────────────────────────────────────

def load_env(path: pathlib.Path) -> dict:
    """Parse a .env file into {key: value}, ignoring comments and blanks."""
    env: dict = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped:
            k, _, v = stripped.partition("=")
            env[k.strip()] = v.strip()
    return env


def save_env(path: pathlib.Path, values: dict) -> None:
    """Upsert keys in a .env file, preserving comments and ordering."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    else:
        lines = []

    written: set = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in values:
                new_lines.append(f"{k}={values[k]}\n")
                written.add(k)
                continue
        new_lines.append(line if line.endswith("\n") else line + "\n")

    for k, v in values.items():
        if k not in written:
            new_lines.append(f"{k}={v}\n")

    path.write_text("".join(new_lines), encoding="utf-8")


# ── OpenWebUI API helpers ──────────────────────────────────────────────────────

_MODEL_PATHS = ("/api/models", "/openai/v1/models", "/v1/models", "/api/v1/models")


def _api_get(base_url: str, api_key: str, path: str, timeout: int = 8):
    full = base_url.rstrip("/") + path
    req = urllib.request.Request(full)
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def validate_connection(base_url: str, api_key: str) -> tuple:
    """Return (ok: bool, error_message: str)."""
    for path in _MODEL_PATHS:
        try:
            _api_get(base_url, api_key, path)
            return True, ""
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                return False, "Authentication failed — check your API key."
            continue
        except Exception:
            continue
    return False, f"Cannot reach {base_url} — check the URL and that OpenWebUI is running."


def fetch_models(base_url: str, api_key: str) -> list:
    """Return sorted list of model IDs, or [] on failure."""
    for path in _MODEL_PATHS:
        try:
            data = _api_get(base_url, api_key, path)
            items = data.get("data", []) if isinstance(data, dict) else data
            ids = [m.get("id") or m.get("name", "") for m in items if isinstance(m, dict)]
            result = sorted(m for m in ids if m)
            if result:
                return result
        except Exception:
            continue
    return []


# ── config.json fallback ───────────────────────────────────────────────────────

def _read_config_json() -> dict:
    """Read base_url / api_key / default_model from BetterWebUI's data/config.json."""
    p = ROOT / "data" / "config.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ── Prompt helpers ─────────────────────────────────────────────────────────────

def prompt_text(label: str, default: str = "", secret: bool = False) -> str:
    """Single-line prompt; empty input keeps the default."""
    if secret and default:
        shown = "*" * min(len(default), 6) + "…"
    else:
        shown = default
    suffix = f" [{cyan(shown)}]" if shown else ""
    while True:
        try:
            if secret:
                import getpass
                val = getpass.getpass(f"  {label}{suffix}: ")
            else:
                val = input(f"  {label}{suffix}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            raise KeyboardInterrupt
        return val if val else default


def _numbered_menu(options: list, title: str, current: str = "") -> str:
    """Numbered-list fallback for non-curses environments. Returns chosen item or ''."""
    PAGE = 20
    start = 0
    print(f"\n  {bold(title)}")
    while True:
        page = options[start : start + PAGE]
        for i, opt in enumerate(page, 1):
            marker = bold(">") if opt == current else " "
            print(f"  {marker} {start + i:3d}. {opt}")
        hints = []
        if start + PAGE < len(options):
            hints.append("n = next page")
        if start > 0:
            hints.append("p = prev page")
        hints.append("s = skip")
        print(f"\n  {dim('  |  '.join(hints))}")
        raw = input("  Choice (number or exact name, s to skip): ").strip()
        if raw.lower() == "s" or raw == "":
            return ""
        if raw.lower() == "n" and start + PAGE < len(options):
            start += PAGE
        elif raw.lower() == "p" and start > 0:
            start -= PAGE
        elif raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return options[idx]
            print(f"  {red('Number out of range.')}")
        elif raw in options:
            return raw
        else:
            print(f"  {red('Not recognised — enter a number, exact name, or s to skip.')}")


def _curses_menu(options: list, title: str, current: str = "") -> str:
    """Curses scrollable/filterable menu. Returns selected item or ''."""
    result_holder = [""]

    def _run(stdscr):
        curses.curs_set(0)
        try:
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)  # selected
            curses.init_pair(2, curses.COLOR_CYAN, -1)                   # header
        except curses.error:
            pass

        filter_str = ""
        filtered = list(options)
        idx = max(0, next((i for i, o in enumerate(options) if o == current), 0))
        scroll = max(0, idx - 5)

        while True:
            h, w = stdscr.getmaxyx()
            visible = max(1, h - 6)

            # Recompute filtered list
            if filter_str:
                filtered = [o for o in options if filter_str.lower() in o.lower()]
            else:
                filtered = list(options)

            # Clamp
            if not filtered:
                idx = 0
                scroll = 0
            else:
                idx = min(idx, len(filtered) - 1)
                if idx < scroll:
                    scroll = idx
                if idx >= scroll + visible:
                    scroll = idx - visible + 1
                scroll = max(0, min(scroll, max(0, len(filtered) - visible)))

            stdscr.erase()
            try:
                # Header
                header = f" {title} "
                stdscr.addstr(0, 0, header[: w - 1].ljust(w - 1), curses.color_pair(2) | curses.A_BOLD)
                # Filter line
                fline = f" Filter: {filter_str}_"
                stdscr.addstr(1, 0, fline[: w - 1])
                # Separator
                stdscr.addstr(2, 0, ("─" * (w - 1))[: w - 1])

                # Items
                for row, i in enumerate(range(scroll, min(scroll + visible, len(filtered)))):
                    y = 3 + row
                    if y >= h - 2:
                        break
                    opt = filtered[i]
                    is_sel = i == idx
                    text = f"  {'▶' if is_sel else ' '} {opt}"[: w - 1]
                    if is_sel:
                        stdscr.addstr(y, 0, text.ljust(w - 1), curses.color_pair(1) | curses.A_BOLD)
                    else:
                        stdscr.addstr(y, 0, text)

                # Footer
                if h - 2 >= 3:
                    stdscr.addstr(h - 2, 0, ("─" * (w - 1))[: w - 1])
                if h - 1 >= 3:
                    footer = " ↑↓/jk navigate   Enter select   type to filter   Backspace clear   Esc/q skip "
                    stdscr.addstr(h - 1, 0, footer[: w - 1], curses.A_DIM)
            except curses.error:
                pass

            stdscr.refresh()
            key = stdscr.getch()

            if key in (curses.KEY_UP, ord("k")):
                if idx > 0:
                    idx -= 1
            elif key in (curses.KEY_DOWN, ord("j")):
                if idx < len(filtered) - 1:
                    idx += 1
            elif key == curses.KEY_PPAGE:
                idx = max(0, idx - visible)
            elif key == curses.KEY_NPAGE:
                idx = min(max(0, len(filtered) - 1), idx + visible)
            elif key in (10, 13, curses.KEY_ENTER):
                if filtered:
                    result_holder[0] = filtered[idx]
                return
            elif key in (27, ord("q")):
                return
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                filter_str = filter_str[:-1]
                idx = 0
                scroll = 0
            elif 32 <= key < 127 and chr(key) != "q":
                filter_str += chr(key)
                idx = 0
                scroll = 0

    curses.wrapper(_run)
    return result_holder[0]


def pick_from_list(options: list, title: str, current: str = "") -> str:
    """Pick from a list; curses on Unix TTY, numbered otherwise. Returns '' to skip."""
    if not options:
        return ""
    use_curses = _IS_TTY and not _IS_WIN
    if use_curses:
        try:
            return _curses_menu(options, title, current)
        except Exception:
            pass
    return _numbered_menu(options, title, current)


# ── Section / status helpers ───────────────────────────────────────────────────

def section(title: str) -> None:
    line = "─" * 54
    print(f"\n{bold(line)}")
    print(f"  {bold(title)}")
    print(f"{bold(line)}")


def status(label: str, ok: bool, detail: str = "") -> None:
    icon = green("✓") if ok else red("✗")
    tail = f"  {dim(detail)}" if detail else ""
    print(f"  {icon}  {label}{tail}")


def banner() -> None:
    print()
    print(bold("╔══════════════════════════════════════════════════════╗"))
    print(bold("║          BetterWebUI  ·  Setup Wizard               ║"))
    print(bold("╚══════════════════════════════════════════════════════╝"))
    print()


# ── Wizard sections ────────────────────────────────────────────────────────────

def _prompt_openwebui(env: dict, force: bool) -> tuple:
    """
    Validate / prompt for OpenWebUI URL, API key, and default model.
    Returns (url, key, model, models_list, changed: bool).
    """
    url   = env.get("OPENWEBUI_BASE_URL", "")
    key   = env.get("OPENWEBUI_API_KEY", "")
    model = env.get("OPENWEBUI_MODEL", "")

    # Fall back to data/config.json for initial defaults
    if not url or not key:
        cfg = _read_config_json()
        url   = url   or cfg.get("base_url", "")
        key   = key   or cfg.get("api_key", "")
        model = model or cfg.get("default_model", "")

    section("OpenWebUI Connection")

    conn_ok = False
    models: list = []
    model_ok = True
    changed = False

    if url and key and not force:
        print(f"  Checking {cyan(url)} …", end=" ", flush=True)
        conn_ok, conn_err = validate_connection(url, key)
        if conn_ok:
            print(green("✓"))
            models = fetch_models(url, key)
            model_ok = (not model) or (model in models)
            if not model_ok:
                print(f"  {red('✗')}  Model {yellow(model)} not found in this OpenWebUI instance.")
        else:
            print(red("✗"))
            print(f"  {red(conn_err)}")
    else:
        conn_ok = False
        conn_err = "Not configured." if not url else "Missing API key."

    needs_prompt = force or not conn_ok or not model_ok

    if not needs_prompt:
        status("OpenWebUI", True, url)
        if model:
            status(f"Model  {yellow(model)}", True)
        else:
            print(f"  {dim('(no default model set)')}")
        return url, key, model, models, False

    # ── Prompt for URL ──
    # Only re-prompt URL if forced, URL is missing, or connection failed for a
    # URL-specific reason.  An "Authentication failed" error means the server IS
    # reachable — the problem is the key, not the URL, so skip ahead to the key
    # prompt in that case.
    url_unreachable = not conn_ok and "Cannot reach" in conn_err
    if force or not url or url_unreachable:
        print()
        while True:
            new_url = prompt_text("OpenWebUI URL", default=url or "http://localhost:3000")
            new_url = new_url.rstrip("/")
            print(f"  {dim('Connecting…')}", end="\r", flush=True)
            conn_ok, conn_err = validate_connection(new_url, key)
            if conn_ok:
                print(f"  {green('✓')} Connected to {cyan(new_url)}          ")
                url = new_url
                changed = True
                break
            if not key and "Authentication" in conn_err:
                # Server is reachable but requires an API key — treat URL as valid
                # and let the key prompt do the full validation.
                print(f"  {green('✓')} URL reachable — API key required          ")
                url = new_url
                changed = True
                conn_ok = False
                break
            print(f"  {red('✗')} {conn_err}")
            retry = input(f"  Try a different URL? [{bold('Y')}/n]: ").strip().lower()
            if retry == "n":
                url = new_url
                changed = True
                break

    # ── Prompt for API key ──
    if force or not key:
        while True:
            new_key = prompt_text("API key", default=key, secret=True)
            if not new_key:
                print(f"  {yellow('⚠')}  No API key set — some endpoints may reject requests.")
                key = new_key
                changed = True
                break
            print(f"  {dim('Verifying…')}", end="\r", flush=True)
            conn_ok, conn_err = validate_connection(url, new_key)
            if conn_ok:
                print(f"  {green('✓')} API key accepted          ")
                key = new_key
                changed = True
                models = fetch_models(url, key)
                break
            print(f"  {red('✗')} {conn_err}")
            retry = input(f"  Try a different key? [{bold('Y')}/n]: ").strip().lower()
            if retry == "n":
                key = new_key
                changed = True
                break

    # Fetch models if we haven't yet
    if conn_ok and not models:
        models = fetch_models(url, key)

    # ── Model selection ──
    # Also prompt when no model is set at all (first-run scenario).
    needs_model_prompt = force or not model or not model_ok
    if needs_model_prompt:
        if models:
            print(f"\n  {len(models)} model(s) available — use ↑↓ to scroll, type to filter.")
            chosen = pick_from_list(models, "Select default model", current=model)
            if chosen:
                model = chosen
                changed = True
                print(f"  {green('✓')} Default model: {yellow(model)}")
            else:
                manual = prompt_text(
                    "Enter model name manually (leave blank to skip)",
                    default=model,
                )
                if manual:
                    model = manual
                    changed = True
        else:
            manual = prompt_text(
                "Default model name (leave blank to skip)",
                default=model,
            )
            if manual:
                model = manual
                changed = True

    return url, key, model, models, changed


def _prompt_ports_paths(env: dict, force: bool) -> tuple:
    """
    Validate / prompt for port and path settings.
    Returns (updated_fields: dict, changed: bool).
    """
    defaults = {
        "PORT":              "8765",
        "CLK_PORT":          "8001",
        "AUTOGUI_PORT":      "8002",
        "OSSO_PORT":         "5001",
        "CLK_WORKSPACES_DIR": "./data/clk-workspaces",
    }
    current = {k: env.get(k, v) for k, v in defaults.items()}

    section("Ports & Paths")

    if not force:
        # Show current values without prompting; pass --reconfigure to change them.
        labels = {
            "PORT":              "BetterWebUI port",
            "CLK_PORT":          "CognitiveLoopKernel port",
            "AUTOGUI_PORT":      "AutoGUI port",
            "OSSO_PORT":         "OSScreenObserver port",
            "CLK_WORKSPACES_DIR": "CLK workspaces directory",
        }
        for k, label in labels.items():
            v = current[k]
            is_default = (v == defaults[k])
            tag = "default" if is_default else ""
            status(f"{label} = {dim(v) if is_default else cyan(v)}", True, tag)
        print(f"  {dim('Pass --reconfigure to change ports or paths.')}")
        return current, False

    print()
    updated = {}
    changed = False
    labels = {
        "PORT":              "BetterWebUI port",
        "CLK_PORT":          "CognitiveLoopKernel port",
        "AUTOGUI_PORT":      "AutoGUI port",
        "OSSO_PORT":         "OSScreenObserver port",
        "CLK_WORKSPACES_DIR": "CLK workspaces directory",
    }
    for k, label in labels.items():
        val = prompt_text(label, default=current[k])
        updated[k] = val
        if val != current[k]:
            changed = True

    return updated, changed


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    force = "--reconfigure" in sys.argv or "--force" in sys.argv

    banner()

    env = load_env(ENV_PATH)
    to_save: dict = {}
    any_changed = False

    try:
        url, key, model, _, ow_changed = _prompt_openwebui(env, force)
        to_save["OPENWEBUI_BASE_URL"] = url
        to_save["OPENWEBUI_API_KEY"]  = key
        to_save["OPENWEBUI_MODEL"]    = model
        any_changed = any_changed or ow_changed

        ports, ports_changed = _prompt_ports_paths(env, force)
        to_save.update(ports)
        any_changed = any_changed or ports_changed

    except KeyboardInterrupt:
        print(f"\n\n  {yellow('Setup cancelled.')}  No changes were written.\n")
        return 1

    if any_changed or not ENV_PATH.exists():
        section("Saving")
        save_env(ENV_PATH, to_save)
        print(f"  {green('✓')} Written to {cyan(str(ENV_PATH.relative_to(ROOT)))}")
    else:
        section("Configuration")
        print(f"  {green('✓')} All settings are valid — nothing to update.")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
