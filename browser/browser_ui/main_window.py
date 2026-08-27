"""Main browser window: toolbar, tabs, docks, menus, shortcuts."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from browser_core.profile import incognito_profile
from browser_core.session import tab_record, window_record
from browser_core.updater import ReleaseDownloader, UpdateChecker
from browser_core.zoom import clamp_zoom, origin_key, remember, zoom_for
from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QGuiApplication, QIcon, QImage, QKeySequence, QShortcut
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QToolBar,
    QVBoxLayout,
)

from .ai_sidebar import AiSidebar
from .dialogs import (
    BookmarksDialog,
    HistoryDialog,
    ScriptsDialog,
    SettingsDialog,
)
from .downloads import DownloadsDock
from .icons import letter_tile
from .omnibox import Omnibox
from .palette import CommandPalette
from .tab_widget import BrowserTabWidget
from .theme import apply_to_app
from .toasts import ToastManager
from .vertical_tabs import VerticalTabsDock

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
NEWTAB_PATH = ASSETS_DIR / "newtab.html"
APP_DISPLAY = "LuckyD Browser"


class MainWindow(QMainWindow):
    # Screenshot worker → GUI thread delivery (bytes payload + target path).
    _shot_saved = Signal(bytes, str)
    _shot_failed = Signal(str)
    # AI tab organizer worker → GUI thread delivery.
    _groups_done = Signal(list)
    _groups_failed = Signal(str)

    def __init__(self, app, incognito: bool = False):
        super().__init__()
        self._app = app
        self.settings = app.settings
        self.storage = app.storage
        self.incognito = incognito
        app_icon = ASSETS_DIR / "professional_icon.ico"
        if app_icon.exists():
            self.setWindowIcon(QIcon(str(app_icon)))

        if incognito:
            # Off-the-record profile: cookies/cache vanish when window closes.
            self.profile = incognito_profile(self)
            self.profile.setUrlRequestInterceptor(app.adblock)
            self.profile.downloadRequested.connect(self.downloads_handle)
        else:
            self.profile = app.profile

        self.resize(1280, 800)
        self._dev_window = None
        self._dev_view = None
        # Element fullscreen (a page's video player asked for the screen):
        # tracks the state plus the chrome we hid, so exit restores exactly.
        self._video_fs = False
        self._fs_hidden: list = []
        self._fs_was_maximized = False

        self.tabs = BrowserTabWidget(self)
        self.setCentralWidget(self.tabs)

        self._build_toolbar()
        self._build_bookmark_bar()
        self._build_statusbar()
        self._build_findbar()
        self._build_docks()
        self._build_menus()
        self._build_shortcuts()

        self._shot_saved.connect(self._write_screenshot)
        self._shot_failed.connect(lambda msg: self.toast(msg, "error"))
        self._groups_done.connect(self._apply_ai_groups)
        self._groups_failed.connect(lambda msg: self.toast(f"AI organizer: {msg}", "error"))

        self.new_tab()
        self._update_title()

        # ── futuristic UI features ─────────────────────────────────────
        self._apply_theme()
        self.toasts = ToastManager(self)
        # Background update checker / downloader (created lazily on first use).
        self._update_checker = None
        self._release_dl = None
        self._palette = CommandPalette(self)
        self._palette.closed.connect(lambda: self._palette.hide())

        self.tabs.tabCloseRequested.connect(self._on_tab_closed)

        # Friendly hint toast on startup (only once per version)
        QTimer.singleShot(1800, self._show_welcome_hint)

        # ── silent update check shortly after startup ─────────────────
        if self.settings.get("update_auto_check", True):
            QTimer.singleShot(8000, lambda: self.check_for_updates(silent=True))

    def _show_welcome_hint(self) -> None:
        """One friendly toast per session: shortcuts + free AI hint."""
        with contextlib.suppress(Exception):
            self.toast(
                "Tip: ? in address bar asks AI • Ctrl+K commands • Esc exits fullscreen", "info"
            )

    def _apply_theme(self) -> None:
        """Apply the current theme (called on startup and when theme changes)."""
        apply_to_app(self._app.qapp, self.settings)
        # Keep the AI sidebar's chat bubbles/code in step with the new palette.
        sidebar = getattr(self, "ai_sidebar", None)
        if sidebar is not None:
            sidebar._apply_theme()

    def toast(self, message: str, kind: str = "info") -> None:
        """Show a transient toast notification (if the toast manager is up)."""
        toasts = getattr(self, "toasts", None)
        if toasts is not None:
            with contextlib.suppress(Exception):
                toasts.show(message, kind)

    def _toggle_assistant(self) -> None:
        """Toggle the AI assistant sidebar."""
        if self.ai_sidebar.isVisible():
            self.ai_sidebar.hide()
        else:
            self.ai_sidebar.show()
            self.ai_sidebar.raise_()
            self.ai_sidebar.input.setFocus()

    def show_assistant(self) -> None:
        """Show the AI assistant sidebar and focus the input."""
        self.ai_sidebar.show()
        self.ai_sidebar.raise_()
        self.ai_sidebar.input.setFocus()
        self.ai_act.setChecked(True)

    def show_downloads(self) -> None:
        """Show the downloads dock."""
        self.downloads.show()
        self.downloads.raise_()

    def show_palette(self) -> None:
        """Show the command palette (Ctrl+K)."""
        self._palette.show_palette()

    def on_pins_changed(self) -> None:
        """Called when pinned tabs change — pinned state is part of the session."""
        self.on_tabs_changed()

    def _on_tab_closed(self, index: int) -> None:
        """Track recently closed tabs for Ctrl+Shift+T restore."""
        pass  # handled in BrowserTabWidget

    # ── UI builders ──────────────────────────────────────────────────

    def _build_toolbar(self) -> None:
        bar = QToolBar("Navigation", self)
        bar.setObjectName("nav_toolbar")
        bar.setMovable(False)
        self.addToolBar(bar)

        self.back_act = QAction("←", self)
        self.back_act.setToolTip("Back (Alt+Left) — mouse back button also works")
        self.back_act.triggered.connect(lambda: self._nav("back"))
        bar.addAction(self.back_act)

        self.forward_act = QAction("→", self)
        self.forward_act.setToolTip("Forward (Alt+Right) — mouse forward button also works")
        self.forward_act.triggered.connect(lambda: self._nav("forward"))
        bar.addAction(self.forward_act)

        self.reload_act = QAction("⟳", self)
        self.reload_act.setToolTip("Reload (F5 / Ctrl+R)")
        self.reload_act.triggered.connect(lambda: self._nav("reload"))
        bar.addAction(self.reload_act)

        home_act = QAction("🏠", self)
        home_act.setToolTip("Home (new tab dashboard)")
        home_act.triggered.connect(self.go_home)
        bar.addAction(home_act)

        self.omnibox = Omnibox(self.settings, self.storage, self)
        self.omnibox.ask.connect(self._omnibox_ask)
        self.omnibox.navigate.connect(self.load_in_current_tab)
        bar.addWidget(self.omnibox)

        self.star_act = QAction("☆", self)
        self.star_act.setToolTip("Bookmark this page (Ctrl+D) — ★ when saved")
        self.star_act.triggered.connect(self.toggle_bookmark)
        bar.addAction(self.star_act)

        # ── AI Assistant ───────────────────────────────────────────────
        bar.addSeparator()
        self.ai_act = QAction("🤖", self)
        self.ai_act.setToolTip("AI Assistant — chat, summarise, vision, agent (Ctrl+Shift+A)")
        self.ai_act.setCheckable(True)
        self.ai_act.triggered.connect(self._toggle_assistant)
        bar.addAction(self.ai_act)

        # ── Coding Agent ───────────────────────────────────────────────
        hq_act = QAction("⌘", self)
        hq_act.setToolTip("Coding Agent workspace — 70+ tools, auto-start (Ctrl+Shift+H)")
        hq_act.triggered.connect(self.open_hq)
        bar.addAction(hq_act)

        mesh_act = QAction("🕸", self)
        mesh_act.setToolTip("Agent Mesh — four parallel sessions (Ctrl+Alt+M)")
        mesh_act.triggered.connect(self.open_agent_mesh)
        bar.addAction(mesh_act)

        if self.incognito:
            incog_label = QLabel(" 🕶 Incognito", self)
            incog_label.setStyleSheet(
                "color: #34d399; font-weight: 600; font-size: 11px; "
                "padding: 0 8px; background: transparent;"
            )
            bar.addWidget(incog_label)

        self.nav_bar = bar

    def _build_bookmark_bar(self) -> None:
        """Toggleable bookmarks strip under the nav bar (Ctrl+Shift+B)."""
        # Force a second toolbar row so the strip always spans the full width.
        self.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea)
        bar = QToolBar("Bookmarks", self)
        bar.setObjectName("bookmark_bar")
        bar.setMovable(False)
        bar.setFloatable(False)
        bar.setIconSize(QSize(16, 16))
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)
        bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        bar.customContextMenuRequested.connect(self._bookmark_bar_menu)
        self.bookmark_bar = bar
        self.refresh_bookmark_bar()
        bar.setVisible(bool(self.settings.get("bookmark_bar_visible", True)))

    def refresh_bookmark_bar(self) -> None:
        """Rebuild the bookmark strip from storage (add/remove/import)."""
        bar = self.bookmark_bar
        bar.clear()
        # Read Later items stay in their menu — the bar is for real bookmarks.
        rows = [r for r in self.storage.bookmarks() if r[2] != "readlater"]
        if not rows:
            hint = QAction("☆ No bookmarks yet — press Ctrl+D to add one", bar)
            hint.setEnabled(False)
            bar.addAction(hint)
            return
        for url, title, _folder, _created in rows[:80]:
            label = (title or url).strip() or url
            act = QAction(label[:28], bar)
            act.setIcon(letter_tile(url))  # per-site identity tile, fully offline
            act.setData(url)
            act.setToolTip(f"{title or url}\n{url}")
            act.triggered.connect(lambda _checked=False, u=url: self.load_in_current_tab(QUrl(u)))
            bar.addAction(act)

    def _bookmark_bar_menu(self, pos) -> None:
        act = self.bookmark_bar.actionAt(pos)
        menu = QMenu(self)
        url = act.data() if act is not None else None
        if url:
            menu.addAction("Open in New Tab", lambda: self.open_in_new_tab(QUrl(url)))
            menu.addAction("Copy URL", lambda: QGuiApplication.clipboard().setText(url))
            menu.addSeparator()
            menu.addAction("Remove Bookmark", lambda: self._remove_bookmark(url))
        else:
            menu.addAction("Hide Bookmarks Bar", lambda: self.bm_bar_act.setChecked(False))
        menu.exec(self.bookmark_bar.mapToGlobal(pos))

    def _remove_bookmark(self, url: str) -> None:
        self.storage.remove_bookmark(url)
        self.refresh_bookmark_bar()
        self._update_star()
        self.toast("Bookmark removed")

    def toggle_bookmark_bar(self, visible: bool) -> None:
        self.bookmark_bar.setVisible(bool(visible))
        self.settings.set("bookmark_bar_visible", bool(visible))

    def toggle_vertical_tabs(self, visible: bool) -> None:
        self.settings.set("vertical_tabs", bool(visible))
        self.vtabs.setVisible(bool(visible))
        self.tabs.tabBar().setVisible(not visible)
        if visible:
            self.vtabs.refresh()

    def _omnibox_ask(self, question: str) -> None:
        """The omnibox "?…" prefix: straight into the AI sidebar chat."""
        if not question:
            return
        self.show_assistant()
        self.ai_sidebar.ask(question)

    def toggle_focus_mode(self) -> None:
        """Immersive mode: strip every chrome surface, keep only the page."""
        on = not getattr(self, "_focus", False)
        self._focus = on
        self.menuBar().setVisible(not on)
        self.nav_bar.setVisible(not on)
        if on:
            self._focus_bm_was = self.bookmark_bar.isVisible()
            self._focus_sb_was = self.ai_sidebar.isVisible()
            self._focus_dl_was = self.downloads.isVisible()
            self._focus_vt_was = self.vtabs.isVisible()
            self.bookmark_bar.hide()
            self.ai_sidebar.hide()
            self.downloads.hide()
            self.vtabs.hide()
            self.statusBar().hide()
            self.toast("Focus mode — Ctrl+Shift+F brings the chrome back")
        else:
            self.statusBar().show()
            if self._focus_bm_was:
                self.bookmark_bar.show()
            if self._focus_sb_was:
                self.ai_sidebar.show()
            if self._focus_dl_was:
                self.downloads.show()
            if self._focus_vt_was:
                self.vtabs.show()

    def _build_statusbar(self) -> None:
        self.progress = QProgressBar(self)
        self.progress.setMaximumWidth(160)
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        self.progress.hide()

        self.zoom_label = QPushButton("100%", self)
        self.zoom_label.setObjectName("zoom_pill")
        self.zoom_label.setFlat(True)
        self.zoom_label.setMinimumWidth(48)
        self.zoom_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.zoom_label.setToolTip("Page zoom — click to reset to 100%")
        self.zoom_label.clicked.connect(self.zoom_reset)
        self.zoom_label.hide()

        self.lock_label = QLabel("", self)
        self.lock_label.setMinimumWidth(20)
        self.lock_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lock_label.setStyleSheet("font-size: 14px; padding: 0 4px;")

        self.statusBar().addPermanentWidget(self.lock_label)
        self.statusBar().addPermanentWidget(self.zoom_label)
        self.statusBar().addPermanentWidget(self.progress)

    def _build_findbar(self) -> None:
        self.find_bar = QToolBar("Find", self)
        self.find_bar.setObjectName("find_bar")
        self.find_bar.setMovable(False)
        self.addToolBar(Qt.ToolBarArea.BottomToolBarArea, self.find_bar)

        self.find_bar.addWidget(QLabel(" Find: "))
        self.find_edit = QLineEdit(self)
        self.find_edit.setMaximumWidth(260)
        self.find_edit.textChanged.connect(self._find_text_changed)
        self.find_edit.returnPressed.connect(self.find_next)
        self.find_bar.addWidget(self.find_edit)

        self._add(self.find_bar, "Next", self.find_next)
        self._add(self.find_bar, "Prev", self.find_prev)
        self._add(self.find_bar, "✕", self.find_bar.hide)
        self.find_bar.hide()

    def _build_docks(self) -> None:
        self.downloads = DownloadsDock(self.settings, self)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.downloads)
        self.downloads.hide()

        self.vtabs = VerticalTabsDock(self)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.vtabs)
        self.vtabs.hide()
        if bool(self.settings.get("vertical_tabs", False)):
            self.vtabs.show()
            self.tabs.tabBar().hide()

        # Side pane: a second, docked web view for link previews/reference —
        # shares the profile (cookies, adblock) but owns no tabs.
        self._side_pane = None

        self.ai_sidebar = AiSidebar(self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.ai_sidebar)
        # "Remember my setup": reopen the assistant on launch so the AI is
        # ready out of the box; hide it for a clean browser when disabled.
        if bool(self.settings.get("assistant_visible_startup", True)):
            self.ai_sidebar.show()
        else:
            self.ai_sidebar.hide()
        # Sync the toolbar button state with the sidebar visibility.
        self.ai_sidebar.visibilityChanged.connect(lambda visible: self.ai_act.setChecked(visible))
        self.ai_act.setChecked(self.ai_sidebar.isVisible())

    @staticmethod
    def _add(menu, text, slot, shortcut=None):
        act = QAction(text, menu)
        if shortcut:
            act.setShortcut(QKeySequence(shortcut))
        act.triggered.connect(slot)
        menu.addAction(act)
        return act

    def _build_menus(self) -> None:
        mbar = self.menuBar()

        file_menu = mbar.addMenu("&File")
        self._add(file_menu, "New Tab", self.new_tab, "Ctrl+T")
        self._add(file_menu, "New Window", self.new_window, "Ctrl+N")
        self._add(
            file_menu,
            "New Incognito Window",
            self.new_incognito_window,
            "Ctrl+Shift+N",
        )
        file_menu.addSeparator()
        self._add(file_menu, "Open File…", self.open_file, "Ctrl+O")
        file_menu.addSeparator()
        self._add(file_menu, "Print…", self.print_page, "Ctrl+P")
        self._add(file_menu, "Save Page As…", self.save_page, "Ctrl+S")
        self._add(file_menu, "Save Screenshot…", self.save_screenshot, "Ctrl+Shift+S")
        self._add(file_menu, "Save Full-Page Screenshot…", self.save_full_screenshot)
        self._add(file_menu, "Reopen Previous Session", self.reopen_previous_session)
        file_menu.addSeparator()
        self._add(file_menu, "Close Tab", self.close_current_tab, "Ctrl+W")
        self._add(file_menu, "Quit", self.close, "Ctrl+Q")

        history_menu = mbar.addMenu("&History")
        self._add(history_menu, "Show All History", self.open_history, "Ctrl+H")
        self._add(history_menu, "Clear All History…", self.clear_history)

        self.bookmarks_menu = mbar.addMenu("&Bookmarks")
        self.bookmarks_menu.aboutToShow.connect(self._rebuild_bookmarks_menu)

        view_menu = mbar.addMenu("&View")
        self._add(view_menu, "Zoom In", self.zoom_in, "Ctrl+=")
        self._add(view_menu, "Zoom Out", self.zoom_out, "Ctrl+-")
        self._add(view_menu, "Actual Size", self.zoom_reset, "Ctrl+0")
        self._add(view_menu, "Reader Mode", self.toggle_reader_mode, "Ctrl+Alt+R")
        view_menu.addSeparator()
        self._add(view_menu, "Find in Page…", self.show_find_bar, "Ctrl+F")
        self._add(view_menu, "View Page Source", self.view_source, "Ctrl+U")
        self._add(view_menu, "Developer Tools", self.open_devtools, "F12")
        view_menu.addSeparator()
        self._add(view_menu, "Fullscreen", self.toggle_fullscreen, "F11")
        view_menu.addSeparator()
        self._add(view_menu, "Downloads", self.show_downloads, "Ctrl+J")
        self.bm_bar_act = QAction("Bookmarks Bar", self, checkable=True)
        self.bm_bar_act.setChecked(bool(self.settings.get("bookmark_bar_visible", True)))
        self.bm_bar_act.setShortcut(QKeySequence("Ctrl+Shift+B"))
        self.bm_bar_act.setStatusTip("Show/hide the bookmarks bar")
        self.bm_bar_act.toggled.connect(self.toggle_bookmark_bar)
        view_menu.addAction(self.bm_bar_act)
        self.vtabs_act = QAction("Vertical Tabs", self, checkable=True)
        self.vtabs_act.setChecked(bool(self.settings.get("vertical_tabs", False)))
        self.vtabs_act.setStatusTip("Show tabs as a sidebar strip instead of the top bar")
        self.vtabs_act.toggled.connect(self.toggle_vertical_tabs)
        view_menu.addAction(self.vtabs_act)
        self._add(view_menu, "Focus Mode", self.toggle_focus_mode, "Ctrl+Shift+F")
        ai_toggle = self.ai_sidebar.toggleViewAction()
        ai_toggle.setText("AI Assistant")
        ai_toggle.setShortcut(QKeySequence("Ctrl+Shift+A"))
        view_menu.addAction(ai_toggle)

        tools_menu = mbar.addMenu("&Tools")
        self._add(tools_menu, "Coding Agent", self.open_hq, "Ctrl+Shift+H")
        self._add(
            tools_menu, "Agent Mesh (4 parallel sessions)", self.open_agent_mesh, "Ctrl+Alt+M"
        )
        self._add(tools_menu, "Agent Terminal", lambda: self.open_terminal("agent"), "Ctrl+`")
        self._add(tools_menu, "Agent 2 Terminal", lambda: self.open_terminal("agent2"))
        self._add(tools_menu, "Antigravity Terminal", lambda: self.open_terminal("mesh-agy"))
        self._add(
            tools_menu,
            "PowerShell Terminal",
            lambda: self.open_terminal("powershell"),
            "Ctrl+Shift+`",
        )
        self._add(tools_menu, "Workflows…", self.open_workflows)
        self._add(tools_menu, "Network Monitor", self.open_network_monitor)
        self._add(tools_menu, "Organize Tabs with AI", self.organize_tabs_with_ai)
        tools_menu.addSeparator()
        self.adblock_act = QAction("Ad-Block Enabled", self, checkable=True)
        self.adblock_act.setChecked(bool(self.settings.get("adblock_enabled", True)))
        self.adblock_act.toggled.connect(self._app.apply_adblock)
        tools_menu.addAction(self.adblock_act)
        self._add(tools_menu, "Reload Ad-Block List", self._app.adblock.reload)
        tools_menu.addSeparator()
        self.browser_api_act = QAction("Browser Control API", self, checkable=True)
        self.browser_api_act.setChecked(bool(self.settings.get("browser_api_enabled", True)))
        self.browser_api_act.setToolTip(
            "Localhost API (127.0.0.1:9777) letting the coding agent "
            "and local scripts drive this browser's tabs"
        )
        self.browser_api_act.toggled.connect(self._app.set_browser_api_enabled)
        tools_menu.addAction(self.browser_api_act)
        tools_menu.addSeparator()
        self._add(tools_menu, "Extensions…", self.open_extensions, "Ctrl+Shift+E")
        tools_menu.addSeparator()
        self._add(tools_menu, "Settings…", self.open_settings, "Ctrl+,")

        help_menu = mbar.addMenu("&Help")
        self._add(help_menu, "Check for Updates…", self.check_for_updates)
        help_menu.addSeparator()
        self._add(help_menu, "Keyboard Shortcuts…", self.show_shortcuts, "Ctrl+/")
        help_menu.addSeparator()
        self._add(help_menu, "About", self.show_about)

    def _rebuild_bookmarks_menu(self) -> None:
        self.bookmarks_menu.clear()
        self._add(self.bookmarks_menu, "Bookmark This Page", self.toggle_bookmark, "Ctrl+D")
        self._add(self.bookmarks_menu, "Read This Page Later", self.save_read_later, "Ctrl+Alt+S")
        self._add(
            self.bookmarks_menu,
            "Show All Bookmarks",
            self.open_bookmarks,
            "Ctrl+Shift+O",
        )
        # Read Later lives in its own submenu — a reading queue, not bookmarks.
        later = [b for b in self.storage.bookmarks() if b[2] == "readlater"]
        if later:
            sub = self.bookmarks_menu.addMenu(f"📖 Read Later ({len(later)})")
            for url, title, _f, _c in later[:15]:
                sub.addAction(
                    (title or url)[:60],
                    lambda checked=False, u=url: self.open_in_new_tab(QUrl(u)),
                )
        self.bookmarks_menu.addSeparator()
        bookmarks = [b for b in self.storage.bookmarks() if b[2] != "readlater"][:25]
        if not bookmarks and not later:
            empty = QAction("(no bookmarks yet)", self)
            empty.setEnabled(False)
            self.bookmarks_menu.addAction(empty)
            return
        for url, title, _folder, _created in bookmarks:
            label = (title or url)[:60]
            self.bookmarks_menu.addAction(
                label,
                lambda checked=False, u=url: self.open_in_new_tab(QUrl(u)),
            )

    def save_read_later(self) -> None:
        """Ctrl+Alt+S: park the current page in the Read Later queue."""
        view = self.tabs.current_view()
        if view is None:
            return
        url = view.url().toString()
        if not url.startswith(("http://", "https://")):
            self.toast("Read Later needs a web page", "info")
            return
        if any(b[0] == url and b[2] == "readlater" for b in self.storage.bookmarks()):
            self.toast("Already in Read Later", "info")
            return
        self.storage.add_bookmark(url, view.title(), folder="readlater")
        self.toast("Added to Read Later 📖", "ok")

    def _build_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self.focus_omnibox)
        QShortcut(QKeySequence("Ctrl+Alt+S"), self, activated=self.save_read_later)
        QShortcut(QKeySequence("Alt+Left"), self, activated=lambda: self._nav("back"))
        QShortcut(QKeySequence("Alt+Right"), self, activated=lambda: self._nav("forward"))
        QShortcut(QKeySequence("F5"), self, activated=lambda: self._nav("reload"))
        esc = QShortcut(QKeySequence("Esc"), self, activated=self._escape)
        esc.setContext(Qt.ShortcutContext.WindowShortcut)
        QShortcut(QKeySequence("Ctrl+Tab"), self, activated=lambda: self._cycle_tab(1))
        QShortcut(
            QKeySequence("Ctrl+Shift+Tab"),
            self,
            activated=lambda: self._cycle_tab(-1),
        )
        QShortcut(
            QKeySequence("Ctrl+9"),
            self,
            activated=lambda: self.tabs.setCurrentIndex(self.tabs.count() - 1),
        )
        for i in range(1, 9):
            QShortcut(
                QKeySequence(f"Ctrl+{i}"),
                self,
                activated=lambda i=i: self._jump_tab(i),
            )
        # Command palette (Ctrl+K)
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self.show_palette)
        # Reopen closed tab (Ctrl+Shift+T)
        QShortcut(
            QKeySequence("Ctrl+Shift+T"),
            self,
            activated=lambda: self.tabs.reopen_last_closed(),
        )

    # ── tab / navigation API ─────────────────────────────────────────

    def new_tab(self, url=None):
        target = url if url is not None else self._newtab_url()
        view = self.tabs.new_tab(target)
        if url is None:
            self.focus_omnibox()
        return view

    def _newtab_url(self) -> QUrl:
        """New-tab target: the live dashboard when the Control API is up."""
        server = getattr(self._app, "control_server", None)
        if (
            server is not None
            and server.running
            and bool(self.settings.get("dashboard_newtab", True))
        ):
            return QUrl(server.base_url + "/dashboard")
        return QUrl.fromLocalFile(str(NEWTAB_PATH))

    def open_hq(self) -> None:
        """Open the Coding Agent HQ (the exe's full UI) in a tab.

        Goes through the Control API's /hq gateway: instant redirect when the
        backend is up, auto-start + splash when it isn't. Falls back to the
        raw harness URL when the Control API is disabled.
        """
        server = getattr(self._app, "control_server", None)
        if server is not None and server.running:
            self.open_in_new_tab(QUrl(server.base_url + "/hq"))
            return
        harness = getattr(self._app, "harness", None)
        if harness is not None:
            harness.ensure_started()
            self.open_in_new_tab(QUrl(harness.url))
        else:
            self.toasts.show("Coding agent backend unavailable", kind="error")

    def open_terminal(self, shell: str = "agent") -> None:
        """Open a NEW independent terminal tab (agent CLI, PowerShell, or CMD).

        Every call spawns its own ConPTY session — the "second terminal" is
        just another tab. Goes through the Control API's /terminal page
        (xterm.js), which talks to the WS→PTY bridge on 127.0.0.1:9881.
        If the bridge is down, kick it once and still open the tab — the
        page auto-retries.
        """
        term = getattr(self._app, "terminal_server", None)
        if term is None or not term.running:
            starter = getattr(self._app, "start_terminal_server", None)
            if callable(starter):
                starter()
        server = getattr(self._app, "control_server", None)
        if server is not None and server.running:
            url = server.base_url + "/terminal"
            if shell and shell != "agent":
                url += f"?shell={shell}"
            self.open_in_new_tab(QUrl(url))
        else:
            self.toasts.show(
                "Terminal needs the Browser Control API (Tools → Browser Control API)",
                kind="error",
            )

    def open_agent_mesh(self) -> None:
        """Open the first-class Agent Mesh workspace with four live sessions."""
        term = getattr(self._app, "terminal_server", None)
        if term is None or not term.running:
            starter = getattr(self._app, "start_terminal_server", None)
            if callable(starter):
                starter()
        server = getattr(self._app, "control_server", None)
        if server is not None and server.running:
            self.open_in_new_tab(QUrl(server.base_url + "/mesh"))
        else:
            self.toasts.show(
                "Agent Mesh needs the Browser Control API (Tools → Browser Control API)",
                kind="error",
            )

    def open_workflows(self) -> None:
        """Open the workflow manager (record/replay saved automations)."""
        server = getattr(self._app, "control_server", None)
        if server is not None and server.running:
            self.open_in_new_tab(QUrl(server.base_url + "/workflows"))
        else:
            self.toasts.show(
                "Workflows need the Browser Control API (Tools → Browser Control API)",
                kind="error",
            )

    def open_network_monitor(self) -> None:
        """Open the live network monitor tab (CDP Network domain)."""
        server = getattr(self._app, "control_server", None)
        if server is not None and server.running:
            self.open_in_new_tab(QUrl(server.base_url + "/network"))
        else:
            self.toasts.show(
                "Network Monitor needs the Browser Control API (Tools → Browser Control API)",
                kind="error",
            )

    # ── side pane (second docked web view) ────────────────────────────

    def open_in_side_pane(self, url: QUrl) -> None:
        """Open a link in the docked side pane instead of a tab."""
        from PySide6.QtWidgets import QDockWidget

        if self._side_pane is None:
            dock = QDockWidget("Side Pane", self)
            dock.setObjectName("side_pane")
            dock.setAllowedAreas(
                Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
            )
            view = QWebEngineView(self.profile, dock)
            view.setUrl(url)
            dock.setWidget(view)
            self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
            dock.show()  # docks default to visible on add — be explicit
            dock.visibilityChanged.connect(self._side_pane_visibility)
            self._side_pane = dock
            self._side_view = view
        else:
            self._side_view.setUrl(url)
            self._side_pane.show()
        # A sensible split: about a third of the window for the pane.
        self.resizeDocks(
            [self._side_pane], [max(360, self.width() // 3)], Qt.Orientation.Horizontal
        )

    def _side_pane_visibility(self, visible: bool) -> None:
        if not visible:
            with contextlib.suppress(Exception):
                self._side_view.stop()

    # ── AI tab organizer ─────────────────────────────────────────────

    def organize_tabs_with_ai(self) -> None:
        """Cluster the window's tabs into named groups via the AI provider."""
        entries = []
        for i in range(self.tabs.count()):
            if self.tabs.is_pinned(i):
                continue
            view = self.tabs.widget(i)
            if view is None:
                continue
            host = ""
            with contextlib.suppress(Exception):
                from urllib.parse import urlsplit

                host = urlsplit(view.url().toString()).hostname or ""
            entries.append({"i": i, "title": (view.title() or "")[:80], "host": host})
        if len(entries) < 3:
            self.toast("Open at least 3 unpinned tabs to organize", "info")
            return
        self.toast("AI is grouping your tabs…")

        def _work() -> None:
            try:
                from browser_core.ai_bridge import AIBridge
                from browser_core.extract import parse_json_loose

                bridge = AIBridge()
                system = (
                    "You organize browser tabs into topical groups. Reply with ONLY "
                    'JSON: {"groups": [{"name": "1-3 word label", "tabs": [<index>, ...]}]}. '
                    "At most 6 groups; every tab index exactly once; skip nothing."
                )
                user = "Tabs:\n" + "\n".join(
                    f'{t["i"]}: {t["title"]} ({t["host"]})' for t in entries
                )
                text, _used = asyncio.run(
                    bridge.chat(
                        [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ]
                    )
                )
                data = parse_json_loose(text)
                groups = data.get("groups") if isinstance(data, dict) else None
                if not groups:
                    raise RuntimeError("the model returned no groups")
                self._groups_done.emit(groups)
            except Exception as exc:
                self._groups_failed.emit(str(exc))

        threading.Thread(target=_work, name="tab-organizer", daemon=True).start()

    def _apply_ai_groups(self, groups: list) -> None:
        applied = 0
        for group in groups[:6]:
            name = str(group.get("name", "")).strip()[:24]
            indices = []
            for raw in group.get("tabs", []):
                with contextlib.suppress(TypeError, ValueError):
                    idx = int(raw)
                    if 0 <= idx < self.tabs.count():
                        indices.append(idx)
            if not name or not indices:
                continue
            gid = self.tabs.create_group(name)
            for idx in indices:
                self.tabs.set_tab_group(idx, gid)
                applied += 1
        if applied:
            self.toast(f"Sorted {applied} tabs into groups", "ok")
        else:
            self.toast("AI organizer produced nothing usable", "warn")

    def create_popup_view(self):
        """Called by WebView.createWindow for target=_blank / window.open."""
        return self.tabs.new_tab(make_current=True)

    def open_in_new_tab(self, url):
        return self.tabs.new_tab(url)

    def load_in_current_tab(self, url) -> None:
        view = self.tabs.current_view()
        if view is not None:
            view.setUrl(url)

    def search_for(self, text: str) -> None:
        self.tabs.new_tab(QUrl(self.settings.search_url_for(text)))

    def close_current_tab(self) -> None:
        index = self.tabs.currentIndex()
        if index >= 0:
            self.tabs.close_tab(index)

    def new_window(self) -> None:
        self._app.new_window()

    def new_incognito_window(self) -> None:
        self._app.new_window(incognito=True)

    def go_home(self) -> None:
        home = self.settings.get("homepage", "newtab")
        if home == "newtab":
            self.load_in_current_tab(self._newtab_url())
        else:
            self.load_in_current_tab(self.omnibox.to_url(home))

    def _nav(self, kind: str) -> None:
        view = self.tabs.current_view()
        if view is not None:
            getattr(view, kind)()

    def _cycle_tab(self, step: int) -> None:
        count = self.tabs.count()
        if count > 1:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + step) % count)

    def _jump_tab(self, n: int) -> None:
        if n <= self.tabs.count():
            self.tabs.setCurrentIndex(n - 1)

    def focus_omnibox(self) -> None:
        self.omnibox.setFocus()
        self.omnibox.selectAll()

    def _escape(self) -> None:
        # 1) YouTube / HTML5 element fullscreen (video player) — must exit via
        # the page's Fullscreen API so Qt emits toggleOff and restores chrome.
        if self._video_fs:
            view = self.tabs.current_view()
            if view is not None:
                try:
                    view.page().triggerAction(QWebEnginePage.WebAction.ExitFullScreen)
                except Exception:
                    self._exit_video_fullscreen()
            else:
                self._exit_video_fullscreen()
            return
        # 2) Window fullscreen (F11) — Esc is expected to leave it too.
        if self.isFullScreen():
            self.showNormal()
            return
        # 3) Normal Esc: stop loading + hide find bar
        view = self.tabs.current_view()
        if view is not None:
            view.stop()
        self.find_bar.hide()

    # ── tab signal handlers (called by BrowserTabWidget) ─────────────

    def on_load_started(self, view) -> None:
        if view is self.tabs.current_view():
            self.progress.setValue(0)
            self.progress.show()

    def on_load_progress(self, view, pct: int) -> None:
        if view is self.tabs.current_view():
            self.progress.setValue(pct)

    def on_load_finished(self, view, ok: bool) -> None:
        self._apply_zoom(view)
        if view is self.tabs.current_view():
            self.progress.hide()
            self._update_nav_state(view)
            self._update_zoom_label(view)
            self._update_security(view)
        url = view.url()
        if ok and url.scheme() in ("http", "https"):
            self.storage.add_visit(url.toString(), view.title())
            self.omnibox.refresh_completions()

    def on_url_changed(self, url) -> None:
        if not self.omnibox.hasFocus():
            self.omnibox.setText(url.toString())
        self._update_star()

    def on_title_changed(self, title: str) -> None:
        self._update_title(title)

    def on_tab_switched(self, view) -> None:
        self.on_url_changed(view.url())
        self._update_title(view.title())
        self._update_nav_state(view)
        self._update_zoom_label(view)
        self._update_security(view)
        if view.page().isLoading():
            self.progress.show()
        else:
            self.progress.hide()

    def on_link_hovered(self, text: str) -> None:
        self.statusBar().showMessage(text)

    def on_fullscreen_requested(self, request) -> None:
        request.accept()
        if request.toggleOn():
            self._enter_video_fullscreen()
        else:
            self._exit_video_fullscreen()

    def _fullscreen_chrome(self) -> list:
        """Every chrome surface that must vanish for element fullscreen."""
        widgets = [
            self.menuBar(),
            self.nav_bar,
            self.bookmark_bar,
            self.find_bar,
            self.statusBar(),
            self.tabs.tabBar(),
            self.vtabs,
            self.ai_sidebar,
            self.downloads,
        ]
        return [w for w in widgets if w is not None]

    def _enter_video_fullscreen(self) -> None:
        """Hide ALL chrome, then go fullscreen — the video gets the whole
        screen, not just the window area left over after the toolbars."""
        if self._video_fs:
            return
        self._video_fs = True
        self._fs_was_maximized = self.isMaximized()
        self._fs_hidden = [w for w in self._fullscreen_chrome() if w.isVisible()]
        for w in self._fs_hidden:
            w.hide()
        self.showFullScreen()

    def _exit_video_fullscreen(self) -> None:
        if not self._video_fs:
            self.showNormal()
            return
        self._video_fs = False
        if self._fs_was_maximized:
            self.showMaximized()
        else:
            self.showNormal()
        for w in self._fs_hidden:
            w.show()
        self._fs_hidden = []

    def _update_title(self, title: str = "") -> None:
        suffix = " — Incognito" if self.incognito else ""
        if title:
            self.setWindowTitle(f"{title} — {APP_DISPLAY}{suffix}")
        else:
            self.setWindowTitle(f"{APP_DISPLAY}{suffix}")

    def _update_nav_state(self, view) -> None:
        self.back_act.setEnabled(view.history().canGoBack())
        self.forward_act.setEnabled(view.history().canGoForward())

    # ── bookmarks ────────────────────────────────────────────────────

    def toggle_bookmark(self) -> None:
        view = self.tabs.current_view()
        if view is None:
            return
        url = view.url().toString()
        if not url.startswith(("http://", "https://")):
            return
        if self.storage.is_bookmarked(url):
            self.storage.remove_bookmark(url)
            self.toasts.show("Bookmark removed", kind="info")
        else:
            self.storage.add_bookmark(url, view.title())
            self.toasts.show("Bookmark added", kind="ok")
        self._update_star()
        self.refresh_bookmark_bar()

    def _update_star(self) -> None:
        view = self.tabs.current_view()
        url = view.url().toString() if view is not None else ""
        starred = url.startswith(("http://", "https://")) and self.storage.is_bookmarked(url)
        self.star_act.setText("★" if starred else "☆")

    def open_bookmarks(self) -> None:
        BookmarksDialog(self.storage, self.open_in_new_tab, self).exec()
        # The manager can delete/import bookmarks — keep the strip in sync.
        self.refresh_bookmark_bar()
        self._update_star()

    def open_extensions(self) -> None:
        ScriptsDialog(self._app.scripts, self).exec()

    # ── history ──────────────────────────────────────────────────────

    def open_history(self) -> None:
        HistoryDialog(self.storage, self.open_in_new_tab, self).exec()

    def clear_history(self) -> None:
        answer = QMessageBox.question(self, "Clear History", "Delete all browsing history?")
        if answer == QMessageBox.StandardButton.Yes:
            self.storage.clear_history()
            self.omnibox.refresh_completions()
            self.toasts.show("History cleared", kind="ok")

    # ── find in page ─────────────────────────────────────────────────

    def show_find_bar(self) -> None:
        self.find_bar.show()
        view = self.tabs.current_view()
        if view is not None and view.selectedText():
            self.find_edit.setText(view.selectedText())
        self.find_edit.setFocus()
        self.find_edit.selectAll()

    def _find_text_changed(self, text: str) -> None:
        view = self.tabs.current_view()
        if view is None:
            return
        view.findText("")  # clear previous highlight
        if text:
            view.findText(text)

    def find_next(self) -> None:
        view = self.tabs.current_view()
        if view is not None and self.find_edit.text():
            view.findText(self.find_edit.text())

    def find_prev(self) -> None:
        view = self.tabs.current_view()
        if view is not None and self.find_edit.text():
            view.findText(self.find_edit.text(), QWebEnginePage.FindFlag.FindBackward)

    # ── view ─────────────────────────────────────────────────────────

    def zoom_in(self) -> None:
        view = self.tabs.current_view()
        if view is not None:
            view.setZoomFactor(clamp_zoom(view.zoomFactor() + 0.1))
            self._update_zoom_label(view)
            self._remember_zoom(view)

    def zoom_out(self) -> None:
        view = self.tabs.current_view()
        if view is not None:
            view.setZoomFactor(clamp_zoom(view.zoomFactor() - 0.1))
            self._update_zoom_label(view)
            self._remember_zoom(view)

    def zoom_reset(self) -> None:
        view = self.tabs.current_view()
        if view is not None:
            view.setZoomFactor(1.0)
            self._update_zoom_label(view)
            self._remember_zoom(view)

    # ── reader mode ──────────────────────────────────────────────────

    def toggle_reader_mode(self) -> None:
        """Distill the page into a clean article view (Ctrl+Alt+R to exit)."""
        view = self.tabs.current_view()
        if view is None:
            return
        original = getattr(view, "_reader_original", "")
        if original:  # already in reader mode → back to the live page
            view._reader_original = ""
            view.setUrl(QUrl(original))
            return
        url = view.url().toString()
        if not url.startswith(("http://", "https://")):
            self.toast("Reader mode needs a web page", "info")
            return
        from browser_core.reader import EXTRACT_JS

        view._reader_candidate = url  # guard against mid-extract navigation
        view.page().runJavaScript(EXTRACT_JS, lambda raw: self._reader_apply(view, raw))

    def _reader_apply(self, view, raw) -> None:
        data = None
        with contextlib.suppress(Exception):
            data = json.loads(raw) if raw else None
        if not data or not data.get("html"):
            self.toast("Nothing readable on this page", "info")
            return
        if getattr(view, "_reader_candidate", "") != view.url().toString():
            return  # user navigated while we were extracting
        from browser_core.reader import reader_html

        from .theme import palette as theme_palette

        url = view.url().toString()
        view._reader_original = url
        view.setHtml(
            reader_html(data.get("title", ""), data["html"], url, theme_palette(self.settings)),
            QUrl(url),
        )
        self.toast("Reader mode on — Ctrl+Alt+R or F5 exits", "info")

    def _apply_zoom(self, view) -> None:
        """Per-site zoom memory first, global default as the fallback."""
        url = view.url().toString()
        if not origin_key(url):
            return  # internal pages keep the neutral 100%
        try:
            default = float(self.settings.get("zoom_factor", 1.0) or 1.0)
        except (TypeError, ValueError):
            default = 1.0
        if bool(self.settings.get("zoom_remember", True)):
            levels = self.settings.get("zoom_levels", {})
            factor = zoom_for(levels, url, default)
        else:
            factor = clamp_zoom(default)
        if abs(view.zoomFactor() - factor) > 0.001:
            view.setZoomFactor(factor)

    def _remember_zoom(self, view) -> None:
        """Persist the current zoom for this site (100% deletes the entry)."""
        if not bool(self.settings.get("zoom_remember", True)):
            return
        url = view.url().toString()
        if not origin_key(url):
            return
        levels = remember(self.settings.get("zoom_levels", {}), url, view.zoomFactor())
        self.settings.set("zoom_levels", levels)

    def _update_zoom_label(self, view) -> None:
        pct = int(view.zoomFactor() * 100)
        self.zoom_label.setText(f"{pct}%")
        # Dynamic property drives the accent state in the theme QSS.
        self.zoom_label.setProperty("zoomed", pct != 100)
        self.zoom_label.style().unpolish(self.zoom_label)
        self.zoom_label.style().polish(self.zoom_label)
        self.zoom_label.show()
        QTimer.singleShot(
            3000, lambda: self.zoom_label.hide() if self.zoom_label.text() == "100%" else None
        )

    def _update_security(self, view) -> None:
        """Update the lock icon based on the page's security."""
        try:
            cert_errors = view.page().certificateErrorCount()
            url = view.url().toString()
            if cert_errors and cert_errors > 0:
                self.lock_label.setText("⚠")
                self.lock_label.setStyleSheet("color: #ff5b6e; font-size: 14px; padding: 0 4px;")
                self.lock_label.setToolTip("Connection not secure — certificate error")
            elif url.startswith("https://"):
                self.lock_label.setText("🔒")
                self.lock_label.setStyleSheet("color: #34d399; font-size: 14px; padding: 0 4px;")
                self.lock_label.setToolTip("Connection secure (HTTPS)")
            else:
                self.lock_label.setText("")
                self.lock_label.setStyleSheet("font-size: 14px; padding: 0 4px;")
                self.lock_label.setToolTip("")
        except Exception:
            self.lock_label.setText("")

    def view_source(self) -> None:
        view = self.tabs.current_view()
        if view is not None:
            self.open_in_new_tab(QUrl("view-source:" + view.url().toString()))

    def open_devtools(self) -> None:
        view = self.tabs.current_view()
        if view is None:
            return
        if self._dev_window is None:
            dlg = QDialog(self)
            dlg.setWindowTitle("Developer Tools")
            dlg.resize(1000, 700)
            layout = QVBoxLayout(dlg)
            self._dev_view = QWebEngineView(dlg)
            layout.addWidget(self._dev_view)
            self._dev_window = dlg
        view.page().setDevToolsPage(self._dev_view.page())
        self._dev_window.show()
        self._dev_window.raise_()

    def toggle_fullscreen(self) -> None:
        # During element fullscreen, F11 must exit THROUGH the page so the
        # engine emits the toggle-off request that restores the chrome — a
        # bare showNormal() would strand the video in fullscreen mode.
        if self._video_fs:
            view = self.tabs.current_view()
            if view is not None:
                view.page().triggerAction(QWebEnginePage.WebAction.ExitFullScreen)
            return
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    # ── downloads / files ────────────────────────────────────────────

    def downloads_handle(self, download) -> None:
        self.downloads.handle_download(download)

    def open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open File")
        if path:
            self.load_in_current_tab(QUrl.fromLocalFile(path))

    def print_page(self) -> None:
        """Print the current page (Ctrl+P)."""
        view = self.tabs.current_view()
        if view is None:
            return
        from PySide6.QtPrintSupport import QPrintDialog

        printer = QPrintDialog(self)
        if printer.exec() == QPrintDialog.DialogCode.Accepted:
            view.page().print(printer.printer(), lambda ok: None)

    def save_page(self) -> None:
        """Save the current page as a single file (Ctrl+S)."""
        view = self.tabs.current_view()
        if view is None:
            return
        from PySide6.QtCore import QSaveFile

        suggested = (view.title() or "page").replace("/", "_").replace("\\", "_")[:80] + ".html"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Page As", suggested, "HTML Files (*.html *.htm);;All Files (*)"
        )
        if not path:
            return
        self.toasts.show("Saving page…", kind="info")

        def _saved(html: str) -> None:
            try:
                file = QSaveFile(path)
                if file.open(file.WriteOnly | file.Text):
                    file.write(html.encode("utf-8"))
                    file.commit()
                    safe_name = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
                    self.toasts.show(f"Page saved to {safe_name}", kind="ok")
                else:
                    self.toasts.show("Could not save page", kind="error")
            except Exception as exc:
                self.toasts.show(f"Save error: {exc}", kind="error")

        view.page().toHtml(_saved)

    def save_screenshot(self) -> None:
        """Capture the visible page to an image file (Ctrl+Shift+S).

        Uses the CDP page target (browser_core.screenshot) because Qt
        WebEngine composites on the GPU and QWidget.grab() comes back blank.
        The capture runs on a worker thread; the result hops back via signals.
        """
        view = self.tabs.current_view()
        if view is None:
            return
        url = view.url().toString()
        if not url.startswith(("http://", "https://")):
            self.toasts.show("Screenshots need a loaded web page", kind="info")
            return
        from browser_core.screenshot import capture_b64, suggested_name

        folder = str(self.settings.get("download_dir", "") or "") or str(Path.home() / "Downloads")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Screenshot",
            str(Path(folder) / suggested_name(url)),
            "JPEG Image (*.jpg);;PNG Image (*.png)",
        )
        if not path:
            return
        self.toasts.show("Capturing screenshot…", kind="info")

        def _work() -> None:
            try:
                payload = base64.b64decode(asyncio.run(capture_b64(url, jpeg_quality=85)))
            except Exception as exc:
                self._shot_failed.emit(f"Screenshot failed: {exc}")
                return
            self._shot_saved.emit(payload, path)

        threading.Thread(target=_work, name="screenshot", daemon=True).start()

    def _write_screenshot(self, payload: bytes, path: str) -> None:
        """GUI-thread delivery of the captured pixels (transcodes via QImage)."""
        try:
            image = QImage.fromData(payload)
            if image.isNull():
                raise RuntimeError("empty capture")
            if not image.save(path):
                raise RuntimeError("could not write file")
            name = Path(path).name
            self.toasts.show(f"Screenshot saved to {name}", kind="ok")
        except Exception as exc:
            self.toasts.show(f"Screenshot failed: {exc}", kind="error")

    def save_full_screenshot(self) -> None:
        """Capture the ENTIRE scrollable page (CDP captureBeyondViewport)."""
        view = self.tabs.current_view()
        if view is None:
            return
        url = view.url().toString()
        if not url.startswith(("http://", "https://")):
            self.toasts.show("Screenshots need a loaded web page", kind="info")
            return
        from browser_core.screenshot import capture_full_b64, suggested_name

        folder = str(self.settings.get("download_dir", "") or "") or str(Path.home() / "Downloads")
        suggested = suggested_name(url).replace(".jpg", "-full.jpg")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Full-Page Screenshot",
            str(Path(folder) / suggested),
            "JPEG Image (*.jpg);;PNG Image (*.png)",
        )
        if not path:
            return
        self.toasts.show("Capturing full page…", kind="info")

        def _work() -> None:
            try:
                payload = base64.b64decode(asyncio.run(capture_full_b64(url, jpeg_quality=85)))
            except Exception as exc:
                self._shot_failed.emit(f"Screenshot failed: {exc}")
                return
            self._shot_saved.emit(payload, path)

        threading.Thread(target=_work, name="screenshot-full", daemon=True).start()

    # ── session restore ("continue where you left off") ──────────────

    def on_tabs_changed(self) -> None:
        """Called by BrowserTabWidget on open/close/navigation/switch."""
        app = getattr(self, "_app", None)
        scheduler = getattr(app, "schedule_session_save", None)
        if callable(scheduler) and not self.incognito:
            scheduler()
        vtabs = getattr(self, "vtabs", None)
        if vtabs is not None and vtabs.isVisible():
            vtabs.refresh()

    def _session_snapshot(self) -> dict | None:
        """This window's restorable state, or None (incognito / nothing worth saving)."""
        if self.incognito:
            return None
        records: list[dict] = []
        current = 0
        current_index = self.tabs.currentIndex()
        for i in range(self.tabs.count()):
            view = self.tabs.widget(i)
            if view is None:
                continue
            rec = tab_record(
                view.url().toString(),
                view.title(),
                self.tabs.is_pinned(i),
                group=self.tabs.group_of(i) or "",
            )
            if rec is not None:
                if i == current_index:
                    current = len(records)
                records.append(rec)
        groups = {
            gid: {"name": info["name"], "color": info["color"], "collapsed": info["collapsed"]}
            for gid, info in self.tabs._groups.items()
        }
        return window_record(records, current, groups=groups or None)

    def restore_session(self, window_data: dict) -> None:
        """Reopen a saved window's tabs (pinned state + active index too)."""
        if self.incognito:
            return
        records = [r for r in (window_data.get("tabs") or []) if isinstance(r, dict)]
        urls = [r for r in records if r.get("url")]
        if not urls:
            return
        # Reuse the pristine initial tab for the first restored URL instead
        # of leaving a stray new-tab page in front of the restored ones.
        first = self.tabs.widget(0) if self.tabs.count() == 1 else None
        fresh_targets = {self._newtab_url().toString(), "about:blank", ""}
        start = 0
        if first is not None and first.url().toString() in fresh_targets:
            first.setUrl(QUrl(urls[0]["url"]))
            if urls[0].get("pinned"):
                self.tabs.toggle_pin(0)
            start = 1
        for rec in urls[start:]:
            view = self.tabs.new_tab(QUrl(rec["url"]), make_current=False)
            if rec.get("pinned"):
                self.tabs.toggle_pin(self.tabs.indexOf(view))
        # Recreate tab groups (ids are remapped — they only need uniqueness).
        gid_map: dict[str, str] = {}
        for old_gid, info in (window_data.get("groups") or {}).items():
            gid_map[old_gid] = self.tabs.create_group(info.get("name", ""), info.get("color"))
        for i, rec in enumerate(urls):
            old_gid = rec.get("group")
            if old_gid and old_gid in gid_map and i < self.tabs.count():
                self.tabs.set_tab_group(i, gid_map[old_gid])
        for old_gid, info in (window_data.get("groups") or {}).items():
            if info.get("collapsed") and old_gid in gid_map:
                self.tabs.toggle_group_collapsed(gid_map[old_gid])
        current = int(window_data.get("current", 0) or 0)
        if 0 <= current < self.tabs.count():
            self.tabs.setCurrentIndex(current)
        count = len(urls)
        self.toast(f"Session restored — {count} tab{'s' if count != 1 else ''} back", "ok")

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        # The app quits when the last window closes — this is the one moment
        # the session is guaranteed to be saved with all tabs still alive.
        app = getattr(self, "_app", None)
        saver = getattr(app, "save_session", None)
        if callable(saver) and not self.incognito:
            saver()
        super().closeEvent(event)

    def reopen_previous_session(self) -> None:
        """Restore the previous session generation (session.prev.json)."""
        app = getattr(self, "_app", None)
        store = getattr(app, "session_store", None)
        if store is None:
            return
        windows = store.load_previous().get("windows") or []
        if not windows:
            self.toast("No previous session saved yet", "info")
            return
        self.restore_session(windows[0])
        for extra in windows[1:]:
            app.new_window().restore_session(extra)

    # ── settings / about ─────────────────────────────────────────────

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self.profile, self)
        if dialog.exec():
            self.adblock_act.setChecked(bool(self.settings.get("adblock_enabled", True)))
            # Settings can also flip the bookmark bar / startup behaviour.
            self.bm_bar_act.setChecked(bool(self.settings.get("bookmark_bar_visible", True)))

    def open_internal_page(self, name: str) -> None:
        """Handle luckyd:// internal links (dashboard tiles, new-tab footer)."""
        actions = {
            "history": self.open_history,
            "bookmarks": self.open_bookmarks,
            "downloads": self.show_downloads,
            "settings": self.open_settings,
            "assistant": self.show_assistant,
            "extensions": self.open_extensions,
            "shortcuts": self.show_shortcuts,
            "hq": self.open_hq,
            "dashboard": lambda: self.open_in_new_tab(self._newtab_url()),
        }
        handler = actions.get(name.strip("/").lower())
        if handler:
            handler()

    def show_shortcuts(self) -> None:
        """Display a keyboard shortcuts reference dialog."""
        shortcuts = """
        <style>
          td, th { padding: 3px 16px 3px 4px; text-align: left; font-size: 12px; }
          th { color: #5b9dff; font-weight: 600; font-size: 13px; padding-top: 10px; }
          .kbd { background: #1a2132; border: 1px solid #232c42; border-radius: 4px;
                 padding: 1px 7px; font-family: monospace; font-size: 11px; }
        </style>
        <b>Keyboard Shortcuts</b><br><br>
        <table>
        <tr><th colspan='2'>Tabs &amp; Windows</th></tr>
        <tr><td><span class='kbd'>Ctrl+T</span></td><td>New tab</td></tr>
        <tr><td><span class='kbd'>Ctrl+W</span></td><td>Close tab</td></tr>
        <tr><td><span class='kbd'>Ctrl+Tab</span></td><td>Next tab</td></tr>
        <tr><td><span class='kbd'>Ctrl+Shift+Tab</span></td><td>Previous tab</td></tr>
        <tr><td><span class='kbd'>Ctrl+1-9</span></td><td>Jump to tab</td></tr>
        <tr><td><span class='kbd'>Ctrl+Shift+T</span></td><td>Reopen closed tab</td></tr>
        <tr><td><span class='kbd'>Ctrl+N</span></td><td>New window</td></tr>
        <tr><td><span class='kbd'>Ctrl+Shift+N</span></td><td>New incognito window</td></tr>
        <tr><th colspan='2'>Navigation</th></tr>
        <tr><td><span class='kbd'>Ctrl+L</span></td><td>Focus address bar</td></tr>
        <tr><td><span class='kbd'>Alt+Left</span></td><td>Back</td></tr>
        <tr><td><span class='kbd'>Alt+Right</span></td><td>Forward</td></tr>
        <tr><td><span class='kbd'>F5</span></td><td>Reload</td></tr>
        <tr><td><span class='kbd'>Esc</span></td><td>Exit fullscreen / Stop loading / Close find bar</td></tr>
        <tr><th colspan='2'>View</th></tr>
        <tr><td><span class='kbd'>Ctrl+=</span></td><td>Zoom in</td></tr>
        <tr><td><span class='kbd'>Ctrl+-</span></td><td>Zoom out</td></tr>
        <tr><td><span class='kbd'>Ctrl+0</span></td><td>Reset zoom</td></tr>
        <tr><td><span class='kbd'>Ctrl+Scroll</span></td><td>Zoom in / out (remembered per site)</td></tr>
        <tr><td><span class='kbd'>F11</span></td><td>Full screen</td></tr>
        <tr><td><span class='kbd'>F12</span></td><td>Developer tools</td></tr>
        <tr><td><span class='kbd'>Ctrl+U</span></td><td>View page source</td></tr>
        <tr><th colspan='2'>Features</th></tr>
        <tr><td><span class='kbd'>Ctrl+F</span></td><td>Find in page</td></tr>
        <tr><td><span class='kbd'>Ctrl+D</span></td><td>Bookmark / unbookmark page</td></tr>
        <tr><td><span class='kbd'>Ctrl+H</span></td><td>History</td></tr>
        <tr><td><span class='kbd'>Ctrl+J</span></td><td>Downloads</td></tr>
        <tr><td><span class='kbd'>Ctrl+Shift+O</span></td><td>Bookmarks manager</td></tr>
        <tr><td><span class='kbd'>Ctrl+Shift+B</span></td><td>Toggle bookmarks bar</td></tr>
        <tr><td><span class='kbd'>Ctrl+Shift+S</span></td><td>Save screenshot</td></tr>
        <tr><td><span class='kbd'>Ctrl+Alt+R</span></td><td>Reader mode</td></tr>
        <tr><td><span class='kbd'>Ctrl+Shift+F</span></td><td>Focus mode (hide all chrome)</td></tr>
        <tr><td><span class='kbd'>Ctrl+Shift+A</span></td><td>AI assistant</td></tr>
        <tr><td><span class='kbd'>Ctrl+Shift+H</span></td><td>Coding agent</td></tr>
        <tr><td><span class='kbd'>Ctrl+Alt+M</span></td><td>Agent Mesh (4 parallel sessions)</td></tr>
        <tr><td><span class='kbd'>Ctrl+`</span></td><td>Agent terminal</td></tr>
        <tr><td><span class='kbd'>Ctrl+Shift+`</span></td><td>PowerShell terminal</td></tr>
        <tr><td><span class='kbd'>Ctrl+K</span></td><td>Command palette</td></tr>
        <tr><td><span class='kbd'>?</span></td><td>Omnibox prefix — ask the AI</td></tr>
        <tr><td><span class='kbd'>Ctrl+,</span></td><td>Settings</td></tr>
        <tr><td><span class='kbd'>Ctrl+P</span></td><td>Print</td></tr>
        <tr><td><span class='kbd'>Ctrl+S</span></td><td>Save page as</td></tr>
        <tr><td><span class='kbd'>Ctrl+O</span></td><td>Open file</td></tr>
        <tr><td><span class='kbd'>Ctrl+Q</span></td><td>Quit</td></tr>
        <tr><td><span class='kbd'>Ctrl+/</span></td><td>Show this help</td></tr>
        </table>
        """
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.about(self, f"{APP_DISPLAY} — Shortcuts", shortcuts)

    def show_about(self) -> None:
        from browser_core.updater import current_version
        from PySide6.QtWidgets import (
            QDialog,
            QDialogButtonBox,
            QLabel,
            QPushButton,
            QVBoxLayout,
        )

        __version__ = current_version()

        channel = "Standalone build" if getattr(sys, "frozen", False) else "Source (dev)"
        last_checked = str(self.settings.get("update_last_checked", "") or "").strip()
        skipped = str(self.settings.get("update_skipped_version", "") or "").strip()
        # Trim ISO timestamp to a friendlier "YYYY-MM-DD HH:MM" form.
        last_checked = last_checked.replace("T", " ")[:16] if last_checked else "Never"

        dlg = QDialog(self)
        dlg.setWindowTitle(f"About {APP_DISPLAY}")
        dlg.setWindowIcon(self.windowIcon())
        lay = QVBoxLayout(dlg)
        text = QLabel(
            f"<h3>🍀 {APP_DISPLAY}</h3>"
            f"<p><b>Version:</b> {__version__}<br>"
            f"<b>Channel:</b> {channel}<br>"
            f"<b>Last checked for updates:</b> {last_checked}</p>"
            + (f"<p><i>Update to version {skipped} was skipped.</i></p>" if skipped else "")
            + "<p>A privacy-first, ad-free web browser built on Qt WebEngine.</p>"
            "<p>Free, open source, no telemetry.</p>"
            "<p>Tabs · Bookmarks · History · Downloads · Incognito · "
            "Find-in-page · Ad-block · DevTools</p>",
            dlg,
        )
        text.setTextFormat(Qt.TextFormat.RichText)
        text.setWordWrap(True)
        lay.addWidget(text)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dlg)
        check_btn = QPushButton("Check for Updates", dlg)
        buttons.addButton(check_btn, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(dlg.reject)

        def _check() -> None:
            dlg.accept()
            self.check_for_updates(silent=False)

        check_btn.clicked.connect(_check)
        lay.addWidget(buttons)
        dlg.resize(440, dlg.sizeHint().height())
        dlg.exec()

    # ── self-updater ────────────────────────────────────────────────────

    def _current_version(self) -> str:
        from browser_core.updater import current_version

        return current_version()

    def check_for_updates(self, silent: bool = False) -> None:
        """Check GitHub for a newer release; offer to download + install."""
        from PySide6.QtWidgets import QMessageBox

        if not getattr(sys, "frozen", False):
            if not silent:
                QMessageBox.information(
                    self,
                    "Updates",
                    "You're running from source.\n\n" "Update with:\n  git pull",
                )
            return

        if self._update_checker is not None and self._update_checker.isRunning():
            return  # a check is already in flight

        # Defensive: silent (startup) checks honor the settings toggle.
        if silent:
            try:
                if not bool(self.settings.get("update_auto_check", True)):
                    return
            except Exception:
                pass

        # Record the attempt so "last checked" is always fresh.
        with contextlib.suppress(Exception):
            self.settings.set(
                "update_last_checked",
                __import__("datetime").datetime.now().isoformat(timespec="seconds"),
            )

        checker = UpdateChecker(parent=self)
        self._update_checker = checker
        checker.update_available.connect(lambda info: self._on_update_available(info, silent))
        checker.up_to_date.connect(lambda: self._on_no_update(silent))
        checker.failed.connect(lambda msg: self._on_update_failed(msg, silent))
        checker.finished.connect(self._on_update_checker_finished)
        checker.start()

    def _on_update_checker_finished(self) -> None:
        """Release the checker reference once its thread has finished."""
        checker = self.sender()
        if self._update_checker is checker:
            self._update_checker = None
        with contextlib.suppress(Exception):
            checker.deleteLater()

    def _on_no_update(self, silent: bool) -> None:
        if not silent:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.information(
                self,
                "No Updates",
                f"You're running the latest version " f"(v{self._current_version()}).",
            )

    def _on_update_failed(self, message: str, silent: bool) -> None:
        if not silent:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(
                self,
                "Update Check Failed",
                f"Could not check for updates:\n{message}",
            )

    def _on_update_available(self, info: dict, silent: bool) -> None:
        from PySide6.QtWidgets import QMessageBox

        version = str(info.get("version") or "?")

        # If the user previously skipped this version, don't nag again.
        try:
            skipped = str(self.settings.get("update_skipped_version", "") or "")
        except Exception:
            skipped = ""
        if skipped and skipped == str(version):
            return

        size_mb = float(info.get("installer_size") or 0) / (1024 * 1024)
        size_line = f"Download size: {size_mb:.1f} MB<br><br>" if size_mb else ""
        # Show a taste of the release notes so the update feels real.
        notes = str(info.get("notes") or "").strip()
        notes_html = ""
        if notes:
            import html as _html

            snippet = _html.escape(notes[:600]).replace("\n", "<br>")
            if len(notes) > 600:
                snippet += "…"
            notes_html = (
                f"<div style='color:#8b93a7; font-size:11px; max-height:140px;'>"
                f"{snippet}</div><br>"
            )

        box = QMessageBox(self)
        box.setWindowTitle("Update Available")
        box.setIcon(QMessageBox.Icon.Question)
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(
            f"<b>Version {version}</b> is available "
            f"(you have v{self._current_version()}).<br><br>"
            f"{size_line}"
            f"{notes_html}"
            "Download and install now? The browser will restart to "
            "apply the update."
        )
        btn_update = box.addButton("Update Now", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Remind Me Later", QMessageBox.ButtonRole.RejectRole)
        btn_skip = box.addButton("Skip This Version", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(btn_update)
        box.exec()

        clicked = box.clickedButton()
        if clicked is btn_update:
            self._start_update_download(info)
        elif clicked is btn_skip:
            try:
                self.settings.set("update_skipped_version", str(version))
                self.toast(f"Will skip version {version}", "info")
            except Exception:
                pass

    def _start_update_download(self, info: dict) -> None:
        url = str(info.get("installer_url") or "")
        if not url:
            # No direct installer asset attached to the release (e.g. the
            # build/upload step was skipped). Fall back to opening the
            # human-readable release page so the user can grab it manually,
            # instead of dead-ending on an error toast.
            release_page = str(info.get("url") or "")
            if release_page:
                self.toast(
                    "No installer attached — opening the release page instead",
                    "info",
                )
                self.open_in_new_tab(QUrl(release_page))
            else:
                self.toast("Update asset missing download URL", "error")
            return

        from PySide6.QtWidgets import QProgressDialog

        progress = QProgressDialog("Downloading update…", "Cancel", 0, 100, self)
        progress.setWindowTitle(f"Updating {APP_DISPLAY}")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        version = str(info.get("version") or "latest")
        dest = Path(tempfile.gettempdir()) / f"{APP_DISPLAY}-update-{version}.exe"
        dl = ReleaseDownloader(
            url,
            dest,
            int(info.get("installer_size") or 0),
            str(info.get("installer_sha256") or ""),
            parent=self,
        )
        self._release_dl = dl

        def _on_progress(received: int, total: int) -> None:
            if total:
                progress.setValue(int(received * 100 / total))

        def _on_done(path: str) -> None:
            progress.close()
            self._apply_update(path, version)

        def _on_error(msg: str) -> None:
            progress.close()
            self.toast(f"Update download failed: {msg}", "error")

        dl.progress.connect(_on_progress)
        dl.finished_ok.connect(_on_done)
        dl.failed.connect(_on_error)
        progress.canceled.connect(dl.cancel)
        dl.cancelled.connect(progress.close)
        dl.start()

    def _apply_update(self, installer_path: str, version: str) -> None:
        """Run the downloaded Inno installer silently, then relaunch.

        The download is LuckyDBrowserSetup-x.y.z.exe (Inno Setup, per-user) —
        NOT a bare exe to swap over the running one (an earlier version of
        this function did exactly that, which would have replaced the app
        with the installer binary). A tiny .bat waits for this process to
        exit, runs the installer silently, relaunches the app, and deletes
        itself.
        """
        from PySide6.QtWidgets import QMessageBox

        current_exe = Path(sys.executable).resolve()
        installer = Path(installer_path).resolve()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".bat", delete=False, encoding="utf-8"
        ) as script:
            script.write(
                "@echo off\r\n"
                "rem Wait for the browser process to fully exit.\r\n"
                "timeout /t 2 /nobreak >nul\r\n"
                f'"{installer}" /VERYSILENT /NORESTART\r\n'
                f'start "" "{current_exe}"\r\n'
                'del "%~f0"\r\n'
            )

        box = QMessageBox(self)
        box.setWindowTitle("Ready to Update")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(
            f"Version {version} has been downloaded.\n\n"
            "Restart now to install it? The browser closes, installs,\n"
            "and reopens itself — your tabs come back with session restore."
        )
        btn_restart = box.addButton("Restart && Update", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_restart)
        box.exec()
        if box.clickedButton() is not btn_restart:
            return  # "Later" — the installer stays in %TEMP% for a manual run
        subprocess.Popen(
            ["cmd", "/c", script.name],
            creationflags=subprocess.CREATE_NO_WINDOW,
            close_fds=True,
        )
        self.close()
