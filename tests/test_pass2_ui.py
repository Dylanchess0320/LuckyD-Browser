"""Pass-2 UI overhaul (2026-10-22): tabs-over-bar layout, one menu button,
focus-mode toggle fix, Gmail sign-in identity, AI Assistant declutter,
homepage declutter, Arctic Light fixes, custom display name.

Mostly functional tests on the pure pieces (omnibox display rule, settings
display-name helper, agent findings journal, interceptor headers,
dashboard HTML) plus source-level assertions for the Qt chrome, matching
the suite's established patterns for main_window/ai_sidebar/dialogs.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

for _mod in (
    "PySide6",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# AdBlockInterceptor subclasses QWebEngineUrlRequestInterceptor — a
# MagicMock base would silently degrade it to a mock (no real methods).
# Give it a real stub class when the ambient stub is not already a type
# (test_98 installs its own; either is fine as a do-nothing base).
_QTWE = sys.modules["PySide6.QtWebEngineCore"]
if not isinstance(getattr(_QTWE, "QWebEngineUrlRequestInterceptor", None), type):

    class _InterceptorStub:
        def __init__(self, *args, **kwargs):
            pass

    _QTWE.QWebEngineUrlRequestInterceptor = _InterceptorStub


def _src(rel: str) -> str:
    return (_BROWSER_DIR / rel).read_text(encoding="utf-8")


# ── custom display name ───────────────────────────────────────────────


def test_display_name_defaults_custom_and_empty_fallback(tmp_path: Path) -> None:
    from browser_core.settings import SettingsStore, display_name

    store = SettingsStore(tmp_path / "settings.json")
    assert display_name(store) == "LuckyD Browser"
    assert display_name(None) == "LuckyD Browser"
    store.set("browser_display_name", "  Dylan's Browser  ")
    assert display_name(store) == "Dylan's Browser"
    store.set("browser_display_name", "   ")
    assert display_name(store) == "LuckyD Browser"


def test_display_name_surfaces_are_wired() -> None:
    mw = _src("browser_ui/main_window.py")
    assert "self._app_display = _settings_display_name(self.settings)" in mw
    assert 'f"{title} — {self._app_display}{suffix}"' in mw
    dialogs = _src("browser_ui/dialogs.py")
    assert 'self._settings.set("browser_display_name", self.display_name.text().strip())' in dialogs
    assert 'aform.addRow("Browser name", self.display_name)' in dialogs


# ── address bar: no internal IP on the homepage ───────────────────────


def test_display_url_text_masks_only_the_dashboard() -> None:
    from browser_ui.omnibox import display_url_text

    assert display_url_text("http://127.0.0.1:9777/dashboard", 9777) == ""
    assert display_url_text("http://127.0.0.1:9777/", 9777) == ""
    assert display_url_text("http://localhost:9777/dashboard", 9777) == ""
    # Other internal pages and real localhost dev pages keep their URL.
    assert (
        display_url_text("http://127.0.0.1:9777/terminal", 9777) == "http://127.0.0.1:9777/terminal"
    )
    assert display_url_text("http://127.0.0.1:3000/dashboard", 9777) == (
        "http://127.0.0.1:3000/dashboard"
    )
    assert display_url_text("https://example.com/dashboard", 9777) == (
        "https://example.com/dashboard"
    )


# ── main window chrome: tabs, menu button, badge, focus ───────────────


def test_menu_button_replaces_menu_bar_and_update_badge() -> None:
    mw = _src("browser_ui/main_window.py")
    assert "self._update_act" not in mw  # ⬆ badge removed
    assert "self.menu_btn = QToolButton(self)" in mw
    assert "root_menu.addMenu(menu)" in mw
    assert "self.menu_btn.setMenu(root_menu)" in mw
    assert "self.menuBar().setVisible(False)" in mw
    # Menus are standalone objects now, not menu-bar children.
    assert 'mbar.addMenu("&File")' not in mw
    assert 'file_menu = QMenu("&File", self)' in mw
    # Help → Check for Updates is the surviving update path.
    assert '"Check for Updates…", self.check_for_updates' in mw


def test_tabs_strip_sits_above_nav_bar_in_central_layout() -> None:
    mw = _src("browser_ui/main_window.py")
    assert "def _build_central" in mw
    assert "tab_bar.setParent(strip)" in mw
    assert "self._central_layout.insertWidget(1, bar)" in mw  # nav under strip
    assert "self._central_layout.insertWidget(2, bar)" in mw  # bookmarks third
    # Classic fallback preserved if the reparent ever fails.
    assert "self.setCentralWidget(self.tabs)" in mw


def test_focus_mode_shortcut_is_window_level() -> None:
    mw = _src("browser_ui/main_window.py")
    assert '_qshortcut(QKeySequence("Ctrl+Shift+F"), self, activated=self.toggle_focus_mode)' in mw
    # …and no longer ONLY the View-menu action's shortcut (the one-way door).
    assert 'self._add(view_menu, "Focus Mode", self.toggle_focus_mode, "Ctrl+Shift+F")' not in mw
    assert 'self._add(view_menu, "Focus Mode", self.toggle_focus_mode)' in mw
    palette = _src("browser_ui/palette.py")
    assert '("Focus Mode", self._mw.toggle_focus_mode)' in palette


def test_no_startup_tip_popup_and_info_toasts_gated() -> None:
    mw = _src("browser_ui/main_window.py")
    assert "_show_welcome_hint" not in mw
    toasts = _src("browser_ui/toasts.py")
    assert 'if kind == "info":' in toasts


def test_shortcuts_dialog_uses_theme_palette() -> None:
    mw = _src("browser_ui/main_window.py")
    start = mw.index("def show_shortcuts")
    segment = mw[start : start + 1400]
    assert "palette(self.settings)" in segment
    assert "#1a2132" not in segment  # hardcoded Neon chip background
    assert "#5b9dff" not in segment  # hardcoded Neon heading color


# ── settings dialog: shrinkable + scrolling ───────────────────────────


def test_settings_dialog_shrinks_and_scrolls() -> None:
    dialogs = _src("browser_ui/dialogs.py")
    assert "self.setMinimumWidth(480)" not in dialogs
    assert "self.setMinimumSize(360, 300)" in dialogs
    assert "def _scrollable(page: QWidget) -> QScrollArea:" in dialogs
    assert dialogs.count("tabs.addTab(_scrollable(") == 4
    for tab in ("General", "Privacy", "Appearance", "Updates"):
        assert f'"{tab}"' in dialogs


# ── AI Assistant declutter ────────────────────────────────────────────


def test_sidebar_has_one_input_no_checkboxes_no_status_dot() -> None:
    sb = _src("browser_ui/ai_sidebar.py")
    for gone in (
        "self.context_box",
        "self.harness_box",
        "self.vision_box",
        "self.agent_input",
        "self.harness_status",
        "refresh_harness_status",
        "🔬 Research",
    ):
        assert gone not in sb, gone
    assert 'self.actions_btn.setText("Actions ▾")' in sb
    assert '"▶ Run as agent task"' in sb
    # Empty chat stays empty: the greeting seeds no blocks anymore.
    greet = sb[sb.index("def _greet") : sb.index("def _greet") + 700]
    assert "self._blocks = []" in greet
    # Full coding agent is the default path; vision is capability-based.
    assert "self._start_harness_agent(task)" in sb
    assert "def _vision_capable" in sb
    # Harness runs are told to end with findings.
    assert "findings summary" in sb


# ── dashboard declutter ───────────────────────────────────────────────


def _fake_settings(**values):
    return SimpleNamespace(get=lambda key, default=None: values.get(key, default))


def test_dashboard_has_no_pills_no_clock_no_mode_tabs() -> None:
    from browser_core.dashboard import dashboard_html

    page = dashboard_html(_fake_settings())
    for gone in (
        'id="pills"',
        "pill-harness",
        'id="clock"',
        'id="time"',
        "setPill(",
        "refreshStatus",
        "mode-tab",
        'id="ai-card"',
        "setSearchMode",
    ):
        assert gone not in page, gone
    # The greeting survives (with the default display name injected).
    assert "LuckyD Browser's ready" in page.replace("\\'", "'")


def test_dashboard_greeting_uses_custom_display_name() -> None:
    from browser_core.dashboard import dashboard_html

    page = dashboard_html(_fake_settings(browser_display_name="Dylan's Browser"))
    assert "Dylan's Browser's ready" in page.replace("\\'", "'")


def test_dashboard_hover_color_is_theme_derived() -> None:
    from browser_core.brand import css_vars

    arctic = css_vars(_fake_settings(theme="arctic"))
    assert "--ld-hover:rgba(26,34,51" in arctic  # dark-text tint on light bg
    page_src = _src("browser_core/dashboard.py")
    assert "background: var(--hover)" in page_src
    assert "rgba(255,255,255,.09)" not in page_src.split("_DASH_CSS")[1].split('"""')[0]


# ── Gmail sign-in identity ────────────────────────────────────────────


class _HeaderInfo:
    """QWebEngineUrlRequestInfo fake that records setHttpHeader calls."""

    def __init__(self, url: str):
        self._url = url
        self.blocked = False
        self.headers: dict[str, str] = {}

    def requestUrl(self):  # noqa: N802 - mirrors QWebEngine API
        url = self._url

        class _U:
            def host(self):
                return url.split("://", 1)[1].split("/", 1)[0].split(":")[0]

            def toString(self):  # noqa: N802
                return url

        return _U()

    def block(self, b: bool):
        self.blocked = b

    def setHttpHeader(self, name, value):  # noqa: N802 - mirrors QWebEngine API
        self.headers[bytes(name).decode()] = bytes(value).decode()


def test_signin_hosts_get_a_consistent_chrome_identity(tmp_path: Path) -> None:
    from browser_core.adblock import AdBlockInterceptor

    interceptor = AdBlockInterceptor(enabled=True, list_path=tmp_path / "none.txt")
    info = _HeaderInfo("https://accounts.google.com/v3/signin/identifier")
    interceptor.interceptRequest(info)
    assert not info.blocked  # sign-in is never blocked
    ua = info.headers.get("User-Agent", "")
    assert ua.startswith("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
    assert "QtWebEngine" not in ua
    hints = info.headers.get("sec-ch-ua", "")
    assert "Chromium" in hints and "Google Chrome" in hints
    assert info.headers.get("sec-ch-ua-platform") == '"Windows"'
    assert info.headers.get("sec-ch-ua-mobile") == "?0"


def test_non_signin_hosts_get_no_pinned_headers(tmp_path: Path) -> None:
    from browser_core.adblock import AdBlockInterceptor

    interceptor = AdBlockInterceptor(enabled=True, list_path=tmp_path / "none.txt")
    info = _HeaderInfo("https://example.com/login")
    interceptor.interceptRequest(info)
    assert info.headers == {}


def test_signin_ua_has_no_qtwebengine_marker() -> None:
    from browser_core.profile import client_hints, signin_ua

    ua = signin_ua()
    assert "QtWebEngine" not in ua
    assert "Chrome/" in ua
    hints = client_hints(ua)
    if hints:  # engine version query can be unavailable under stubbed Qt
        major = ua.split("Chrome/", 1)[1].split(".", 1)[0]
        assert f'v="{major}"' in hints["sec-ch-ua"]
