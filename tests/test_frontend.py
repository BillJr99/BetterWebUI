"""
Frontend static-asset and structural tests.

These tests don't require a browser or JavaScript runtime. They parse the
HTML and CSS files with Python to verify:

  - ARIA, accessibility, and structural requirements in index.html
  - Required CSS rules and custom properties in style.css
  - Required JS functions / SSE event handlers in app.js
  - Content-Security-Policy-friendliness (no inline event handlers in HTML)
"""

from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"
INDEX_HTML = (STATIC / "index.html").read_text(encoding="utf-8")
STYLE_CSS = (STATIC / "style.css").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")


# ===========================================================================
# index.html — structure and accessibility
# ===========================================================================

class TestIndexHtml:
    def test_has_lang_attribute(self):
        assert 'lang="en"' in INDEX_HTML

    def test_has_charset(self):
        assert 'charset="utf-8"' in INDEX_HTML.lower()

    def test_has_viewport_meta(self):
        assert "viewport" in INDEX_HTML

    def test_has_skip_link(self):
        assert 'class="skip-link"' in INDEX_HTML
        assert 'href="#main"' in INDEX_HTML

    def test_main_has_id(self):
        assert 'id="main"' in INDEX_HTML

    def test_messages_has_aria_live(self):
        assert 'aria-live' in INDEX_HTML
        # The messages div should have aria-live="polite"
        assert 'aria-live="polite"' in INDEX_HTML

    def test_messages_has_role_log(self):
        assert 'role="log"' in INDEX_HTML

    def test_tabs_have_role_tab(self):
        assert 'role="tab"' in INDEX_HTML

    def test_tabs_have_aria_selected(self):
        assert 'aria-selected' in INDEX_HTML

    def test_nav_has_role_tablist(self):
        assert 'role="tablist"' in INDEX_HTML

    def test_tab_panels_have_role_tabpanel(self):
        assert 'role="tabpanel"' in INDEX_HTML

    def test_dialog_root_has_aria_live_assertive(self):
        assert 'aria-live="assertive"' in INDEX_HTML

    def test_onboarding_overlay_has_aria_modal(self):
        assert 'aria-modal="true"' in INDEX_HTML

    def test_composer_input_has_aria_label(self):
        assert 'aria-label="Message input"' in INDEX_HTML

    def test_send_button_has_aria_label(self):
        assert 'aria-label="Send message"' in INDEX_HTML

    def test_mic_button_present(self):
        assert 'id="mic-btn"' in INDEX_HTML

    def test_mic_button_has_aria_pressed(self):
        assert 'aria-pressed="false"' in INDEX_HTML

    def test_mode_select_present(self):
        assert 'id="mode-select"' in INDEX_HTML

    def test_plan_pane_present(self):
        assert 'id="plan-pane"' in INDEX_HTML

    def test_files_pane_present(self):
        assert 'id="files-pane"' in INDEX_HTML

    def test_right_rail_present(self):
        assert 'id="right-rail"' in INDEX_HTML

    def test_toggle_plan_btn_present(self):
        assert 'id="toggle-plan-btn"' in INDEX_HTML

    def test_toggle_files_btn_present(self):
        assert 'id="toggle-files-btn"' in INDEX_HTML

    def test_shortcut_sheet_present(self):
        assert 'id="shortcut-sheet"' in INDEX_HTML

    def test_onboarding_overlay_present(self):
        assert 'id="onboarding-overlay"' in INDEX_HTML

    def test_display_settings_controls_present(self):
        for eid in ("cfg-font-size", "cfg-line-height", "cfg-dyslexic",
                    "cfg-high-contrast", "cfg-reduce-motion"):
            assert f'id="{eid}"' in INDEX_HTML, f"Missing element: {eid}"

    def test_import_workspace_btn_present(self):
        assert 'id="import-workspace-btn"' in INDEX_HTML

    def test_conv_search_input_present(self):
        assert 'id="conv-search"' in INDEX_HTML

    def test_no_inline_onclick_in_html(self):
        # All event wiring should be in app.js, not inline in HTML
        assert " onclick=" not in INDEX_HTML

    def test_katex_css_loaded(self):
        assert "katex" in INDEX_HTML

    def test_app_js_linked(self):
        assert "/static/app.js" in INDEX_HTML

    def test_style_css_linked(self):
        assert "/static/style.css" in INDEX_HTML


# ===========================================================================
# style.css — required rules
# ===========================================================================

class TestStyleCss:
    def test_has_css_custom_properties(self):
        assert ":root {" in STYLE_CSS
        assert "--accent:" in STYLE_CSS
        assert "--font-sans:" in STYLE_CSS
        assert "--ui-font-size:" in STYLE_CSS

    def test_has_skip_link_styles(self):
        assert ".skip-link" in STYLE_CSS

    def test_skip_link_is_offscreen_by_default(self):
        # Skip link must be visually hidden until focused
        assert "top: -9999px" in STYLE_CSS or "clip:" in STYLE_CSS

    def test_skip_link_visible_on_focus(self):
        assert ".skip-link:focus" in STYLE_CSS

    def test_has_focus_visible_ring(self):
        assert ":focus-visible" in STYLE_CSS
        assert "outline:" in STYLE_CSS

    def test_has_high_contrast_theme(self):
        assert "body.high-contrast" in STYLE_CSS

    def test_has_dyslexic_font_class(self):
        assert "body.dyslexic-font" in STYLE_CSS

    def test_has_reduce_motion(self):
        assert "reduce-motion" in STYLE_CSS or "prefers-reduced-motion" in STYLE_CSS

    def test_has_font_size_classes(self):
        for cls in ("body.font-sm", "body.font-md", "body.font-lg", "body.font-xl"):
            assert cls in STYLE_CSS

    def test_has_right_rail(self):
        assert "#right-rail" in STYLE_CSS

    def test_has_plan_list(self):
        assert ".plan-list" in STYLE_CSS
        assert ".plan-item" in STYLE_CSS

    def test_has_file_tree(self):
        assert ".file-tree" in STYLE_CSS
        assert ".file-tree-item" in STYLE_CSS

    def test_has_subagent_card(self):
        assert ".subagent-card" in STYLE_CSS

    def test_has_mode_select(self):
        assert ".mode-select" in STYLE_CSS

    def test_has_mic_button(self):
        assert ".mic-btn" in STYLE_CSS
        assert ".mic-btn.listening" in STYLE_CSS

    def test_has_telemetry_line(self):
        assert ".telemetry-line" in STYLE_CSS

    def test_has_onboarding_overlay(self):
        assert ".onboarding-overlay" in STYLE_CSS

    def test_has_use_case_grid(self):
        assert ".use-case-grid" in STYLE_CSS
        assert ".use-case-card" in STYLE_CSS

    def test_has_explain_expander(self):
        assert ".explain-expander" in STYLE_CSS

    def test_has_trust_session_wrap(self):
        assert ".trust-session-wrap" in STYLE_CSS

    def test_has_diff_view(self):
        assert ".diff-view" in STYLE_CSS

    def test_has_lint_warnings(self):
        assert ".lint-warnings" in STYLE_CSS

    def test_has_shortcut_list(self):
        assert ".shortcut-list" in STYLE_CSS
        assert "kbd" in STYLE_CSS

    def test_has_responsive_breakpoints(self):
        assert "@media" in STYLE_CSS

    def test_has_message_actions(self):
        assert ".message-actions" in STYLE_CSS


# ===========================================================================
# app.js — required functions and SSE handlers
# ===========================================================================

class TestAppJs:
    def test_has_api_helper(self):
        assert "async function api(" in APP_JS

    def test_has_escape_helper(self):
        assert "function escape(" in APP_JS

    def test_has_render_markdown(self):
        assert "function renderMarkdownWithMath(" in APP_JS

    def test_has_render_math(self):
        assert "function renderMathIn(" in APP_JS

    def test_has_load_config(self):
        assert "async function loadConfig(" in APP_JS

    def test_has_save_connection(self):
        assert "async function saveConnection(" in APP_JS

    def test_has_save_defaults(self):
        assert "async function saveDefaults(" in APP_JS

    def test_has_save_display(self):
        assert "async function saveDisplay(" in APP_JS

    def test_has_apply_display_settings(self):
        assert "function applyDisplaySettings(" in APP_JS

    def test_has_task_plan_handler(self):
        assert "task_plan" in APP_JS

    def test_has_render_plan(self):
        assert "function renderPlan(" in APP_JS

    def test_has_subagent_sse_handlers(self):
        assert "subagent_start" in APP_JS
        assert "subagent_result" in APP_JS

    def test_has_telemetry_handler(self):
        assert "showTelemetryLine(" in APP_JS

    def test_has_voice_input(self):
        assert "SpeechRecognition" in APP_JS
        assert "function toggleMic(" in APP_JS or "toggleMic" in APP_JS

    def test_has_read_aloud(self):
        assert "async function readAloud(" in APP_JS

    def test_has_approval_dialog(self):
        assert "function askApproval(" in APP_JS

    def test_has_explain_expander_wiring(self):
        assert "explain-details" in APP_JS or "explain_command" in APP_JS or "explain-command" in APP_JS

    def test_has_trust_session_in_approval(self):
        assert "trust_session" in APP_JS

    def test_has_onboarding_check(self):
        assert "async function checkOnboarding(" in APP_JS

    def test_has_workspace_export(self):
        assert "async function exportWorkspace(" in APP_JS

    def test_has_workspace_import(self):
        assert "async function importWorkspace(" in APP_JS

    def test_has_fork_conversation(self):
        assert "async function forkConversation(" in APP_JS

    def test_has_pin_conversation(self):
        assert "async function pinConversation(" in APP_JS

    def test_has_conversation_search(self):
        assert "convSearchQuery" in APP_JS

    def test_has_file_tree_refresh(self):
        assert "async function refreshFileTree(" in APP_JS

    def test_has_keyboard_shortcut_handler(self):
        assert "function handleGlobalKey(" in APP_JS

    def test_has_shortcut_sheet_toggle(self):
        assert "shortcut-sheet" in APP_JS

    def test_has_mode_select_persist(self):
        assert "mode-select" in APP_JS

    def test_has_right_rail_toggle(self):
        assert "function setPlanPaneVisible(" in APP_JS
        assert "function setFilesPaneVisible(" in APP_JS

    def test_has_lint_warnings_render(self):
        assert "function renderLintSection(" in APP_JS

    def test_has_focus_trap(self):
        assert "function trapFocus(" in APP_JS

    def test_no_console_log_in_production_paths(self):
        # console.warn is fine (KaTeX errors), but console.log calls in
        # hot paths indicate debug code left in
        log_lines = [ln for ln in APP_JS.splitlines()
                     if "console.log(" in ln and not ln.strip().startswith("//")]
        assert log_lines == [], f"Found console.log calls: {log_lines}"

    def test_send_includes_mode(self):
        # The fetch to /api/chat must include the mode field
        assert '"mode"' in APP_JS or "mode:" in APP_JS

    def test_init_calls_check_onboarding(self):
        assert "checkOnboarding" in APP_JS

    def test_init_calls_init_mic(self):
        assert "initMic()" in APP_JS

    def test_wire_events_has_mic(self):
        assert "mic-btn" in APP_JS
