"""Main browser window: toolbar, tabs, docks, menus, shortcuts."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import html as _html
import json
import sys
import threading
from pathlib import Path
from typing import Any

from browser_core import tts as _tts
from browser_core.permissions import ALLOW, ASK, DENY, feature_key, feature_label, origin_of
from browser_core.profile import incognito_profile
from browser_core.session import plan_restore, tab_record, window_record
from browser_core.settings import display_name as _settings_display_name

# Kept importable: other modules (About dialog, selftest) import from
# browser_core.updater, so the module stays — these two names are just no
# longer used by this file.
from browser_core.updater import (  # noqa: F401
    ReleaseDownloader,
    UpdateChecker,
)

#: Updates now live on the public GitHub releases page. The in-app
#: download-and-install flow was unreliable (API rate limits, proxies/VPNs,
#: timing), so every update path below just opens this page in a tab.
RELEASES_PAGE_URL = "https://github.com/Dylanchess0320/LuckyD-Browser/releases"
from browser_core.zoom import clamp_zoom, origin_key, remember, zoom_for
from PySide6.QtCore import QEvent, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QGuiApplication, QIcon, QImage, QKeySequence, QShortcut
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .ai_sidebar import AiSidebar
from .dialogs import (
    BookmarksDialog,
    HistoryDialog,
    PermissionsDialog,
    ScriptsDialog,
    SettingsDialog,
)
from .downloads import DownloadsDock
from .icons import letter_tile
from .omnibox import Omnibox, display_url_text
from .palette import CommandPalette
from .tab_widget import BrowserTabWidget
from .theme import apply_to_app, palette
from .titlebar import apply_titlebar_theme
from .toasts import ToastManager
from .vertical_tabs import VerticalTabsDock

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
NEWTAB_PATH = ASSETS_DIR / "newtab.html"
APP_DISPLAY = "LuckyD"  # fallback brand; live windows use settings.display_name()


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

        # Loopback session cookies for this profile's tabs (4.0) — incognito
        # windows get their own profile, so they need their own cookies.
        with contextlib.suppress(Exception):
            app.install_local_auth_cookies(self.profile)

        self.resize(1280, 800)
        self._dev_window: QDialog | None = None
        self._dev_view: QWebEngineView | None = None
        # Element fullscreen (a page's video player asked for the screen):
        # tracks the state plus the chrome we hid, so exit restores exactly.
        self._video_fs = False
        self._fs_hidden: list = []
        self._fs_was_maximized = False

        self.tabs = BrowserTabWidget(self)
        # The user's display name for the browser (Settings → Appearance;
        # default "LuckyD Browser"). In-app surfaces only — window title,
        # homepage greeting, About — never the exe/installer identity.
        self._app_display = _settings_display_name(self.settings)
        self._build_central()

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
        self._groups_failed.connect(
            lambda msg: self.toast(f"Couldn't group your tabs: {msg} — try again", "error")
        )

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

        # Auto-update checks are permanently disabled: they were unreliable
        # (GitHub API rate limits, proxies/VPNs, timing). Updates live on the
        # GitHub releases page — Help > Check for Updates opens it in a tab.
        self._release_page_url = RELEASES_PAGE_URL

    def _apply_theme(self) -> None:
        """Apply the current theme (called on startup and when theme changes)."""
        apply_to_app(self._app.qapp, self.settings)
        # The Windows caption lives outside Qt's client area; paint it with the
        # same palette so the header reads as part of the browser.
        with contextlib.suppress(Exception):
            apply_titlebar_theme(self, self.settings)
        # Keep the AI sidebar's chat bubbles/code in step with the new palette.
        sidebar = getattr(self, "ai_sidebar", None)
        if sidebar is not None:
            sidebar._apply_theme()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt API name)
        """Re-assert native title-bar colours once the HWND is on screen."""
        super().showEvent(event)
        with contextlib.suppress(Exception):
            apply_titlebar_theme(self, self.settings)

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

    def _retry_download_url(self, url: str) -> None:
        """Re-issue an interrupted download through the current tab's page.

        Qt offers no restart on a dead QWebEngineDownloadRequest — asking
        the live page to download the same URL raises a fresh request,
        which lands back in the dock as a new entry.
        """
        try:
            view = self.tabs.current_view()
            if view is not None and url:
                view.page().download(QUrl(url))
                self.show_downloads()
        except Exception:
            pass

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

    def _build_tab_strip(self, parent: QWidget) -> QWidget:
        """The tab-strip row: [tab bar][+ button][flexible space].

        The bar is added WITHOUT stretch and the flexible space goes
        after the + button, so the button hugs the last open tab and
        travels with the tab row (Dylan 2026-10-22 — it used to sit on
        the far side of the window). When tabs overflow, the bar takes
        the available width and scrolls internally; the + stays pinned
        right after it.
        """
        strip = QWidget(parent)
        strip_layout = QHBoxLayout(strip)
        strip_layout.setContentsMargins(0, 0, 0, 0)
        strip_layout.setSpacing(0)
        tab_bar = self.tabs.tabBar()
        tab_bar.setParent(strip)
        strip_layout.addWidget(tab_bar)
        self._tab_bar = tab_bar
        with contextlib.suppress(Exception):
            plus = self.tabs.cornerWidget(Qt.Corner.TopRightCorner)
            if plus is not None:
                plus.setParent(strip)
                strip_layout.addWidget(plus)
        strip_layout.addStretch(1)
        return strip

    def _build_central(self) -> None:
        """Central layout with the tab strip ABOVE the address bar.

        QMainWindow always docks toolbars above the central widget, so the
        classic tab-strip-on-top order needs the bars inside one container:
        [tab strip][nav bar][bookmark bar][tab pages]. The QTabBar is
        reparented into the strip row (the tab widget keeps managing the
        same bar object; BrowserTabBar references its widget explicitly,
        not via parent()).

        The PAGES live in the tab widget's own QStackedWidget, lifted
        into the container as a direct row. Keeping the whole tab widget
        in the layout left a blank strip between the nav bar and the
        page: QTabWidget reserves its (reparented-away) tab bar's height
        inside its own frame — measured 29px of dead space (Dylan
        2026-10-22). With the stack as a container row the content sits
        flush under the bars, and the emptied tab-widget shell is hidden
        (it remains the manager object every call site uses).

        Any failure falls back to the classic layout — tabs as the
        central widget with docked toolbars — rather than breaking
        window construction.
        """
        self._tab_bar = None
        self._central_layout: QVBoxLayout | None = None
        self._pages_stack: QStackedWidget | None = None
        try:
            stack = self.tabs.findChild(QStackedWidget)
            if stack is None:
                raise RuntimeError("tab pages stack not found")
            central = QWidget(self)
            layout = QVBoxLayout(central)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
            strip = self._build_tab_strip(central)
            stack.setParent(central)
            layout.addWidget(strip)
            layout.addWidget(stack, 1)
            self._pages_stack = stack
            self.tabs.hide()
            self._central_layout = layout
            self.setCentralWidget(central)
        except Exception:
            self._tab_bar = None
            self._central_layout = None
            self._pages_stack = None
            self.setCentralWidget(self.tabs)
            self._fallback_plus_beside_tabs()

    @staticmethod
    def _plus_x(bar_left: int, last_tab_right: int, tabs_width: int, plus_width: int) -> int:
        """X for the fallback + button: right after the last tab, capped
        so it never leaves the tab widget's right edge."""
        return max(0, min(last_tab_right + 2, tabs_width - plus_width - 2))

    def _fallback_plus_beside_tabs(self) -> None:
        """Classic-layout fallback only: float the + button beside the
        last tab instead of leaving it in the far top-right corner."""
        with contextlib.suppress(Exception):
            plus = self.tabs.cornerWidget(Qt.Corner.TopRightCorner)
            if plus is None:
                return
            self.tabs.setCornerWidget(None, Qt.Corner.TopRightCorner)
            plus.setParent(self.tabs)
            plus.show()
            self._fallback_plus = plus
            bar = self.tabs.tabBar()
            bar.installEventFilter(self)
            self.tabs.installEventFilter(self)
            self.tabs.currentChanged.connect(lambda _i: self._place_fallback_plus())
            self._place_fallback_plus()

    def _place_fallback_plus(self) -> None:
        plus = getattr(self, "_fallback_plus", None)
        if plus is None:
            return
        bar = self.tabs.tabBar()
        geo = bar.geometry()
        if bar.count() > 0:
            last_right = geo.left() + bar.tabRect(bar.count() - 1).right()
        else:
            last_right = geo.left()
        plus.move(
            self._plus_x(geo.left(), last_right, self.tabs.width(), plus.width()),
            geo.top(),
        )
        plus.raise_()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802  # Qt virtual override
        # Classic fallback only: keep the floated + button glued to the
        # last tab when the tab widget resizes or the bar relayouts.
        if getattr(self, "_fallback_plus", None) is not None and event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.LayoutRequest,
        ):
            self._place_fallback_plus()
        return super().eventFilter(obj, event)

    def _build_toolbar(self) -> None:
        bar = QToolBar("Navigation", self)
        bar.setObjectName("nav_toolbar")
        bar.setMovable(False)
        if self._central_layout is not None:
            # Inside the central container, directly under the tab strip.
            self._central_layout.insertWidget(1, bar)
        else:
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
        home_act.setToolTip("Home — opens your homepage")
        home_act.triggered.connect(self.go_home)
        bar.addAction(home_act)

        self.omnibox = Omnibox(self.settings, self.storage, self)
        self.omnibox.ask.connect(self._omnibox_ask)
        self.omnibox.navigate.connect(self.load_in_current_tab)
        bar.addWidget(self.omnibox)

        # (The old ⬆ update badge that sat here is gone — Dylan 2026-10-22:
        # updates already live under Menu → Help → Check for Updates. The
        # freed top-right slot now holds the menu button, added below.)

        # Legacy toolbar buttons (star/AI/coding-agent/mesh/summarize/read)
        # were removed in 7.0: the new-tab dashboard's Apps grid covers them
        # with one click and the shortcuts still work (Ctrl+D, Ctrl+Shift+A,
        # Ctrl+Shift+H, Ctrl+Alt+M, Ctrl+Shift+U, Ctrl+Shift+L). The actions
        # still exist SOLELY because other code references them (e.g.
        # _update_star → star_act, docks → ai_act); they are just never added
        # to the navigation bar.
        self.star_act = QAction("☆", self)
        self.star_act.setToolTip("Bookmark this page (Ctrl+D) — ★ when saved")
        self.star_act.triggered.connect(self.toggle_bookmark)

        self.ai_act = QAction("🤖", self)
        self.ai_act.setToolTip("AI Assistant — chat, summarise, vision, agent (Ctrl+Shift+A)")
        self.ai_act.setCheckable(True)
        self.ai_act.triggered.connect(self._toggle_assistant)

        hq_act = QAction("⌘", self)
        hq_act.setToolTip("Coding Agent workspace — 70+ tools, auto-start (Ctrl+Shift+H)")
        hq_act.triggered.connect(self.open_hq)

        mesh_act = QAction("🕸", self)
        mesh_act.setToolTip("Multi Agent Terminal — four parallel sessions (Ctrl+Alt+M)")
        mesh_act.triggered.connect(self.open_agent_mesh)

        summarize_act = QAction("✨", self)
        summarize_act.setToolTip("Summarize this page (Ctrl+Shift+U)")
        summarize_act.triggered.connect(self.summarize_page)

        self.read_act = QAction("🔊", self)
        self.read_act.setToolTip("Read this page aloud (Ctrl+Shift+L) — click again to stop")
        self.read_act.triggered.connect(self.read_aloud_page)

        if self.incognito:
            incog_label = QLabel(" 🕶 Incognito", self)
            incog_label.setStyleSheet(
                "color: #34d399; font-weight: 600; font-size: 11px; "
                "padding: 0 8px; background: transparent;"
            )
            bar.addWidget(incog_label)

        # The whole menu bar (File/History/Bookmarks/View/Tools/Help) lives
        # behind this one dropdown button at the top-right — where the old
        # update arrow sat. _build_menus() fills its menu.
        self.menu_btn = QToolButton(self)
        self.menu_btn.setText("☰")
        self.menu_btn.setToolTip("Menu — File, History, Bookmarks, View, Tools, Help")
        self.menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        bar.addWidget(self.menu_btn)

        self.nav_bar = bar

    def _build_bookmark_bar(self) -> None:
        """Toggleable bookmarks strip under the nav bar (Ctrl+Shift+B)."""
        bar = QToolBar("Bookmarks", self)
        bar.setObjectName("bookmark_bar")
        bar.setMovable(False)
        bar.setFloatable(False)
        bar.setIconSize(QSize(16, 16))
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        if self._central_layout is not None:
            # Third row of the central container: strip, nav, bookmarks.
            self._central_layout.insertWidget(2, bar)
        else:
            # Classic fallback: force a second toolbar row so the strip
            # always spans the full width.
            self.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea)
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
            self.toast("Focus mode — press Ctrl+Shift+F to bring the toolbars back")
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

        self.lock_label = QPushButton("", self)
        self.lock_label.setFlat(True)
        self.lock_label.setMinimumWidth(28)
        self.lock_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lock_label.setStyleSheet("font-size: 14px; padding: 0 4px;")
        self.lock_label.clicked.connect(self.open_site_permissions)

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
        self.downloads = DownloadsDock(
            self.settings,
            self,
            storage=self._app.storage,
            retry_handler=self._retry_download_url,
        )
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
        self._side_pane: Any = None
        self._side_view: Any = None

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
        # No native menu-bar row (2026-10-22): the six menus are standalone
        # QMenu objects, grouped as submenus of ONE root menu behind the
        # top-right menu button (see _build_toolbar). The native titlebar —
        # window controls and dragging — is untouched. The empty menu bar
        # widget itself stays hidden for good.
        self.menuBar().setVisible(False)

        file_menu = QMenu("&File", self)
        self._add(file_menu, "New Tab", self.new_tab, "Ctrl+T")
        self._add(file_menu, "New Window", self.new_window, "Ctrl+N")
        self._add(
            file_menu,
            "New Incognito Window",
            self.new_incognito_window,
            "Ctrl+Shift+N",
        )
        self.workspaces_menu = file_menu.addMenu("Workspaces")
        self.workspaces_menu.aboutToShow.connect(self._rebuild_workspaces_menu)
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

        history_menu = QMenu("&History", self)
        self._add(history_menu, "Show All History", self.open_history, "Ctrl+H")
        self._add(history_menu, "Clear All History…", self.clear_history)

        self.bookmarks_menu = QMenu("&Bookmarks", self)
        self.bookmarks_menu.aboutToShow.connect(self._rebuild_bookmarks_menu)

        view_menu = QMenu("&View", self)
        self._add(view_menu, "Zoom In", self.zoom_in, "Ctrl+=")
        self._add(view_menu, "Zoom Out", self.zoom_out, "Ctrl+-")
        self._add(view_menu, "Actual Size", self.zoom_reset, "Ctrl+0")
        self._add(view_menu, "Reader Mode", self.toggle_reader_mode, "Ctrl+Alt+R")
        self._add(view_menu, "Read Aloud", self.read_aloud_page, "Ctrl+Shift+L")
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
        self._add(view_menu, "Focus Mode", self.toggle_focus_mode)
        ai_toggle = self.ai_sidebar.toggleViewAction()
        ai_toggle.setText("AI Assistant")
        ai_toggle.setShortcut(QKeySequence("Ctrl+Shift+A"))
        view_menu.addAction(ai_toggle)

        tools_menu = QMenu("&Tools", self)
        if self._show_retired_tools():
            self._add(tools_menu, "Coding Agent", self.open_hq, "Ctrl+Shift+H")
        self._add(
            tools_menu,
            "Multi Agent Terminal (4 parallel sessions)",
            self.open_agent_mesh,
            "Ctrl+Alt+M",
        )
        if self._show_retired_tools():
            self._add(tools_menu, "LuckyD Chat", self.open_luckyd_chat)
        self._add(tools_menu, "Agent 1 Terminal", lambda: self.open_terminal("agent"), "Ctrl+`")
        self._add(tools_menu, "Agent 2 Terminal", lambda: self.open_terminal("agent2"))
        self._add(
            tools_menu,
            "PowerShell Terminal",
            lambda: self.open_terminal("powershell"),
            "Ctrl+Shift+`",
        )
        if self._show_retired_tools():
            self._add(tools_menu, "Workflows…", self.open_workflows)
        self._add(tools_menu, "Network Monitor", self.open_network_monitor)
        self._add(tools_menu, "Organize Tabs with AI", self.organize_tabs_with_ai)
        if self._show_retired_tools():
            self._add(tools_menu, "Deep Researcher Swarm…", self.open_deep_research, "Ctrl+Shift+R")
        self._add(tools_menu, "Summarize This Page", self.summarize_page, "Ctrl+Shift+U")
        self._add(tools_menu, "Site Permissions…", self.open_site_permissions)
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

        help_menu = QMenu("&Help", self)
        self._add(help_menu, "Check for Updates…", self.check_for_updates)
        help_menu.addSeparator()
        self._add(help_menu, "Keyboard Shortcuts…", self.show_shortcuts, "Ctrl+/")
        help_menu.addSeparator()
        self._add(help_menu, "About", self.show_about)

        # One root menu for the top-right button: the same six menus, in
        # the same order, as submenus. Every command (and the dynamic
        # Bookmarks/Workspaces repopulation) works exactly as before —
        # the menu objects are the same live objects as ever.
        root_menu = QMenu(self)
        for menu in (
            file_menu,
            history_menu,
            self.bookmarks_menu,
            view_menu,
            tools_menu,
            help_menu,
        ):
            root_menu.addMenu(menu)
        self._root_menu = root_menu
        self.menu_btn.setMenu(root_menu)

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
        _qshortcut: Any = QShortcut
        _qshortcut(QKeySequence("Ctrl+L"), self, activated=self.focus_omnibox)
        _qshortcut(QKeySequence("Ctrl+Alt+S"), self, activated=self.save_read_later)
        _qshortcut(QKeySequence("Alt+Left"), self, activated=lambda: self._nav("back"))
        _qshortcut(QKeySequence("Alt+Right"), self, activated=lambda: self._nav("forward"))
        _qshortcut(QKeySequence("F5"), self, activated=lambda: self._nav("reload"))
        esc = _qshortcut(QKeySequence("Esc"), self, activated=self._escape)
        esc.setContext(Qt.ShortcutContext.WindowShortcut)
        _qshortcut(QKeySequence("Ctrl+Tab"), self, activated=lambda: self._cycle_tab(1))
        _qshortcut(
            QKeySequence("Ctrl+Shift+Tab"),
            self,
            activated=lambda: self._cycle_tab(-1),
        )
        _qshortcut(
            QKeySequence("Ctrl+9"),
            self,
            activated=lambda: self.tabs.setCurrentIndex(self.tabs.count() - 1),
        )
        for i in range(1, 9):
            _qshortcut(
                QKeySequence(f"Ctrl+{i}"),
                self,
                activated=lambda i=i: self._jump_tab(i),
            )
        # Focus Mode (Ctrl+Shift+F) — window-level, NOT a menu shortcut:
        # focus mode hides the menu chrome, and a menu action's shortcut
        # stops dispatching while its menu is hidden, which made focus
        # mode a one-way door. This binding survives hidden chrome, so
        # the same command toggles it off AND on.
        _qshortcut(QKeySequence("Ctrl+Shift+F"), self, activated=self.toggle_focus_mode)
        # Command palette (Ctrl+K)
        _qshortcut(QKeySequence("Ctrl+K"), self, activated=self.show_palette)
        # Reopen closed tab (Ctrl+Shift+T)
        _qshortcut(
            QKeySequence("Ctrl+Shift+T"),
            self,
            activated=lambda: self.tabs.reopen_last_closed(),
        )
        # Undo last reversible tab action (Ctrl+Alt+Z)
        _qshortcut(
            QKeySequence("Ctrl+Alt+Z"),
            self,
            activated=self.undo_tab_action,
        )
        _qshortcut(QKeySequence("Ctrl+Shift+L"), self, activated=self.read_aloud_page)
        _qshortcut(QKeySequence("Ctrl+Shift+U"), self, activated=self.summarize_page)

    def undo_tab_action(self) -> dict:
        """Ctrl+Alt+Z: undo the most recent reversible tab-bar operation."""
        res = self.tabs.undo_last_tab_action()
        if res.get("undone"):
            self.toast(f"↩ {res.get('detail', 'Undid tab action')}", "ok")
        else:
            self.toast("Nothing to undo", "info")
        return res

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
            self.toasts.show(
                "Coding agent backend unavailable — turn on 'Start the coding-agent "
                "backend on launch' in Settings > General, then restart the browser",
                kind="error",
            )

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
        """Open the first-class Multi Agent Terminal workspace with four live sessions."""
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
                "Multi Agent Terminal needs the Browser Control API (Tools → Browser Control API)",
                kind="error",
            )

    def open_luckyd_chat(self) -> None:
        """Open LuckyD Chat — Hub chat + Claw tasks in one tab."""
        server = getattr(self._app, "control_server", None)
        if server is not None and server.running:
            self.open_in_new_tab(QUrl(server.base_url + "/chat"))
        else:
            self.toasts.show(
                "LuckyD Chat needs the Browser Control API (Tools → Browser Control API)",
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

    def open_deep_research(self, query: str = "") -> None:
        """Open the dedicated Deep Researcher Swarm tool workspace in a tab.

        Pre-populates current tab URL/title as context and query if provided.
        Runs the multi-agent swarm: planner -> parallel research workers ->
        citation synthesizer -> critic & verifier -> final report.
        """
        server = getattr(self._app, "control_server", None)
        if server is None or not server.running:
            starter = getattr(self._app, "set_browser_api_enabled", None)
            if callable(starter):
                starter(True)
            server = getattr(self._app, "control_server", None)

        from urllib.parse import urlencode

        params = {}
        if query:
            params["q"] = query

        try:
            view = self.tabs.current_view()
            if view is not None and view.url().scheme() in ("http", "https"):
                params["context_url"] = view.url().toString()
                params["context_title"] = view.title() or ""
        except Exception:
            pass

        if server is not None and server.running:
            qs = ("?" + urlencode(params)) if params else ""
            self.open_in_new_tab(QUrl(server.base_url + "/research" + qs))
        else:
            self.toasts.show(
                "Deep Research Swarm needs the Browser Control API (Tools → Browser Control API)",
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
                from browser_core.ai_bridge import get_shared_bridge
                from browser_core.extract import parse_json_loose

                bridge = get_shared_bridge()
                system = (
                    "You organize browser tabs into topical groups. Reply with ONLY "
                    'JSON: {"groups": [{"name": "1-3 word label", "tabs": [<index>, ...]}]}. '
                    "At most 6 groups; every tab index exactly once; skip nothing."
                )
                user = "Tabs:\n" + "\n".join(
                    f"{t['i']}: {t['title']} ({t['host']})" for t in entries
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
            port = int(self.settings.get("browser_api_port", 9777) or 9777)
            self.omnibox.setText(display_url_text(url.toString(), port))
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
            self.setWindowTitle(f"{title} — {self._app_display}{suffix}")
        else:
            self.setWindowTitle(f"{self._app_display}{suffix}")

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
                self.lock_label.setToolTip("Connection secure (HTTPS) — click for site permissions")
            elif url.startswith("http://"):
                self.lock_label.setText("🔓")
                self.lock_label.setStyleSheet("color: #fbbf24; font-size: 14px; padding: 0 4px;")
                self.lock_label.setToolTip("Not secure (HTTP) — click for site permissions")
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
        if self._dev_view is not None:
            view.page().setDevToolsPage(self._dev_view.page())
        if self._dev_window is not None:
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
                    self.toasts.show(
                        "Couldn't save the page — check that the folder is writable and try again",
                        kind="error",
                    )
            except Exception as exc:
                self.toasts.show(
                    f"Couldn't save the page: {exc} — check that the folder is "
                    "writable and try again",
                    kind="error",
                )

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
                self._shot_failed.emit(
                    f"Screenshot failed: {exc} — try reloading the page and capturing again"
                )
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
            self.toasts.show(
                f"Screenshot failed: {exc} — try reloading the page and capturing again",
                kind="error",
            )

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
                self._shot_failed.emit(
                    f"Screenshot failed: {exc} — try reloading the page and capturing again"
                )
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
                self.tabs.view_url(view),
                view.title() or (getattr(view, "_ld_pending_title", "") or ""),
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

    def restore_session(self, window_data: dict, announce: bool = True) -> None:
        """Reopen a saved window's tabs (pinned state + active index too).

        Lazy since 10.7: only the active tab loads its page during restore.
        Every other tab is created deferred (URL + title, no renderer) and
        loads on first activation, so a big restored session starts fast.
        """
        if self.incognito:
            return
        raw_current = window_data.get("current", 0)
        plan, current = plan_restore(
            window_data.get("tabs") or [],
            raw_current if isinstance(raw_current, int) else 0,
        )
        if not plan:
            return
        # Reuse the pristine initial tab for the first restored URL instead
        # of leaving a stray new-tab page in front of the restored ones.
        first = self.tabs.widget(0) if self.tabs.count() == 1 else None
        fresh_targets = {self._newtab_url().toString(), "about:blank", ""}
        start = 0
        # The view created/reused for each plan entry. Plan positions are NOT
        # tab indexes: existing tabs may precede them and pinning can reorder.
        views: list = []
        if first is not None and first.url().toString() in fresh_targets:
            rec = plan[0]
            if rec["load_now"]:
                # Clear any pending URL left by an earlier lazy restore
                # (workspace switch), or view_url()/wake would resurrect it.
                first._ld_pending_url = ""
                first._ld_pending_title = ""
                first.setUrl(QUrl(rec["url"]))
            else:
                first._ld_pending_url = rec["url"]
                first._ld_pending_title = (rec.get("title") or "").strip()
                self.tabs._retitle(first, first.title())
            if rec.get("pinned"):
                self.tabs.toggle_pin(0)
            views.append(first)
            start = 1
        for rec in plan[start:]:
            view = self.tabs.new_tab(
                QUrl(rec["url"]),
                make_current=False,
                defer_load=not rec["load_now"],
                title=rec.get("title") or "",
            )
            if rec.get("pinned"):
                self.tabs.toggle_pin(self.tabs.indexOf(view))
            views.append(view)
        # Recreate tab groups (ids are remapped — they only need uniqueness).
        gid_map: dict[str, str] = {}
        for old_gid, info in (window_data.get("groups") or {}).items():
            gid_map[old_gid] = self.tabs.create_group(info.get("name", ""), info.get("color"))
        for view, rec in zip(views, plan, strict=False):
            old_gid = rec.get("group")
            index = self.tabs.indexOf(view)
            if old_gid and old_gid in gid_map and index >= 0:
                self.tabs.set_tab_group(index, gid_map[old_gid])
        for old_gid, info in (window_data.get("groups") or {}).items():
            if info.get("collapsed") and old_gid in gid_map:
                self.tabs.toggle_group_collapsed(gid_map[old_gid])
        current_index = self.tabs.indexOf(views[current]) if current < len(views) else -1
        if current_index >= 0:
            self.tabs.setCurrentIndex(current_index)
        count = len(plan)
        if announce:
            self.toast(f"Session restored — {count} tab{'s' if count != 1 else ''} back", "ok")

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        # The app quits when the last window closes — this is the one moment
        # the session is guaranteed to be saved with all tabs still alive.
        app = getattr(self, "_app", None)
        saver = getattr(app, "save_session", None)
        if callable(saver) and not self.incognito:
            saver()
        with contextlib.suppress(Exception):
            _tts.stop()
        super().closeEvent(event)

    def reopen_previous_session(self) -> None:
        """Restore the previous session generation (session.prev.json)."""
        app = getattr(self, "_app", None)
        store = getattr(app, "session_store", None)
        if store is None or app is None:
            return
        windows = store.load_previous().get("windows") or []
        if not windows:
            self.toast("No previous session saved yet", "info")
            return
        self.restore_session(windows[0])
        for extra in windows[1:]:
            app.new_window().restore_session(extra)

    # ── site permissions / HTTPS-Only / workspaces / read-aloud ──────

    def handle_feature_permission(self, page, origin, feature) -> None:
        """Prompt once per origin+feature; remember Allow/Block (not incognito)."""
        url = origin.toString() if hasattr(origin, "toString") else str(origin)
        key = feature_key(feature)
        host = origin_of(url)
        store = getattr(self._app, "permissions", None)
        persist = not self.incognito
        decision = store.get(host, key) if store is not None and host else ASK
        grant, deny_pol = self._permission_policies()
        if decision == ALLOW:
            page.setFeaturePermission(origin, feature, grant)
            return
        if decision == DENY:
            page.setFeaturePermission(origin, feature, deny_pol)
            return
        label = feature_label(key)
        box = QMessageBox(self)
        box.setWindowTitle("Site permission")
        box.setText(f"{host or url} wants to use your {label.lower()}.")
        box.setInformativeText("LuckyD will remember this choice for the site.")
        allow_btn = box.addButton("Allow", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Block", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        ok = box.clickedButton() is allow_btn
        page.setFeaturePermission(origin, feature, grant if ok else deny_pol)
        if store is not None and host:
            store.set(host, key, ALLOW if ok else DENY, persist=persist)

    @staticmethod
    def _permission_policies():
        grant = getattr(
            getattr(QWebEnginePage, "PermissionPolicy", QWebEnginePage),
            "PermissionGrantedByUser",
            1,
        )
        deny = getattr(
            getattr(QWebEnginePage, "PermissionPolicy", QWebEnginePage),
            "PermissionDeniedByUser",
            2,
        )
        return grant, deny

    def open_site_permissions(self) -> None:
        store = getattr(self._app, "permissions", None)
        if store is None:
            return
        view = self.tabs.current_view()
        origin = origin_of(view.url().toString()) if view is not None else ""
        PermissionsDialog(store, origin=origin, parent=self).exec()

    def summarize_page(self) -> None:
        self.show_assistant()
        with contextlib.suppress(Exception):
            self.ai_sidebar._summarize()

    def read_aloud(self, text: str = "") -> None:
        if _tts.is_speaking():
            _tts.stop()
            self.toast("Stopped reading", "info")
            return
        if not (text or "").strip():
            self.toast("Nothing to read", "info")
            return
        if _tts.speak(text):
            self.toast("Reading aloud…", "ok")
        else:
            self.toast("Read Aloud isn't available on this system", "warn")

    def read_aloud_page(self) -> None:
        if _tts.is_speaking():
            self.read_aloud("")
            return
        view = self.tabs.current_view()
        if view is None:
            return
        selected = ""
        with contextlib.suppress(Exception):
            selected = (view.page().selectedText() or "").strip()
        if selected:
            self.read_aloud(selected)
            return
        view.page().toPlainText(lambda t: self.read_aloud(t or ""))

    def _workspace_tabs(self) -> tuple[list[dict], int]:
        snap = self._session_snapshot() or {}
        tabs = [t for t in (snap.get("tabs") or []) if isinstance(t, dict)]
        return tabs, int(snap.get("current") or 0)

    def _rebuild_workspaces_menu(self) -> None:
        menu = self.workspaces_menu
        menu.clear()
        self._add(menu, "Save Current as Workspace…", self.save_workspace)
        store = getattr(self._app, "workspaces", None)
        items = store.list() if store is not None else []
        if items:
            menu.addSeparator()
            for item in items:
                mark = "● " if store is not None and item["id"] == store.active_id else ""
                act = QAction(f"{mark}{item['name']} ({len(item.get('tabs') or [])} tabs)", self)
                act.triggered.connect(lambda _=False, i=item["id"]: self.switch_workspace(i))
                menu.addAction(act)
            menu.addSeparator()
            self._add(menu, "Rename Active Workspace…", self.rename_active_workspace)
            self._add(menu, "Delete Active Workspace", self.delete_active_workspace)

    def save_workspace(self) -> None:
        store = getattr(self._app, "workspaces", None)
        if store is None or self.incognito:
            self.toast("Workspaces are unavailable in incognito", "info")
            return
        name, ok = QInputDialog.getText(self, "Save Workspace", "Name:")
        if not ok or not str(name).strip():
            return
        tabs, current = self._workspace_tabs()
        store.create(str(name).strip(), tabs, current)
        self.toast(f"Saved workspace “{str(name).strip()}”", "ok")

    def switch_workspace(self, ws_id: str) -> None:
        store = getattr(self._app, "workspaces", None)
        if store is None or self.incognito:
            return
        if store.active_id:
            tabs, current = self._workspace_tabs()
            store.update_tabs(store.active_id, tabs, current)
        item = store.get(ws_id)
        if item is None:
            return
        store.set_active(ws_id)
        self._load_workspace_tabs(item)
        self.toast(f"Workspace: {item['name']}", "ok")

    def _load_workspace_tabs(self, item: dict) -> None:
        while self.tabs.count() > 1:
            self.tabs.close_tab(self.tabs.count() - 1)
        first = self.tabs.widget(0)
        if first is not None:
            if self.tabs.is_pinned(0):
                self.tabs.toggle_pin(0)
            first.setUrl(QUrl("about:blank"))
        self.restore_session(
            {"tabs": item.get("tabs") or [], "current": item.get("current") or 0},
            announce=False,
        )

    def rename_active_workspace(self) -> None:
        store = getattr(self._app, "workspaces", None)
        if store is None or not store.active_id:
            return
        item = store.get(store.active_id)
        if item is None:
            return
        name, ok = QInputDialog.getText(self, "Rename Workspace", "Name:", text=item["name"])
        if ok and str(name).strip():
            store.rename(item["id"], str(name).strip())

    def delete_active_workspace(self) -> None:
        store = getattr(self._app, "workspaces", None)
        if store is None or not store.active_id:
            return
        item = store.get(store.active_id)
        if item is None:
            return
        if (
            QMessageBox.question(self, "Delete workspace", f"Delete “{item['name']}”?")
            == QMessageBox.StandardButton.Yes
        ):
            store.delete(item["id"])
            self.toast("Workspace deleted", "ok")

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self.profile, self)
        if dialog.exec():
            # A renamed browser shows its new name immediately.
            self._app_display = _settings_display_name(self.settings)
            self._update_title()
            self.adblock_act.setChecked(bool(self.settings.get("adblock_enabled", True)))
            # Settings can also flip the bookmark bar / startup behaviour.
            self.bm_bar_act.setChecked(bool(self.settings.get("bookmark_bar_visible", True)))
            self._app.apply_adblock(bool(self.settings.get("adblock_enabled", True)))

    def _show_retired_tools(self) -> bool:
        """Retired tools (LuckyD HQ, Workflows, Deep Researcher, Deck Studio,
        LuckyD Chat) stay hidden from every UI surface unless the user flips
        the show_retired_tools setting back on. The tools themselves — pages,
        routes, modules — remain fully built in."""
        return bool(self.settings.get("show_retired_tools", False))

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
            "permissions": self.open_site_permissions,
            "dashboard": lambda: self.open_in_new_tab(self._newtab_url()),
        }
        if self._show_retired_tools():
            actions["hq"] = self.open_hq
        handler = actions.get(name.strip("/").lower())
        if handler:
            handler()

    # (section, [(keys, description), ...]) — the shortcuts reference is
    # data, so the dialog builder below stays layout-only.
    _SHORTCUT_SECTIONS = (
        (
            "Tabs & Windows",
            (
                ("Ctrl+T", "New tab"),
                ("Ctrl+W", "Close tab"),
                ("Ctrl+Tab", "Next tab"),
                ("Ctrl+Shift+Tab", "Previous tab"),
                ("Ctrl+1-9", "Jump to tab"),
                ("Ctrl+Shift+T", "Reopen closed tab"),
                ("Ctrl+N", "New window"),
                ("Ctrl+Shift+N", "New incognito window"),
            ),
        ),
        (
            "Navigation",
            (
                ("Ctrl+L", "Focus address bar"),
                ("Alt+Left", "Back"),
                ("Alt+Right", "Forward"),
                ("F5", "Reload"),
                ("Esc", "Exit fullscreen / Stop loading / Close find bar"),
            ),
        ),
        (
            "View",
            (
                ("Ctrl+=", "Zoom in"),
                ("Ctrl+-", "Zoom out"),
                ("Ctrl+0", "Reset zoom"),
                ("Ctrl+Scroll", "Zoom in / out (remembered per site)"),
                ("F11", "Full screen"),
                ("F12", "Developer tools"),
                ("Ctrl+U", "View page source"),
            ),
        ),
        (
            "Features",
            (
                ("Ctrl+F", "Find in page"),
                ("Ctrl+D", "Bookmark / unbookmark page"),
                ("Ctrl+H", "History"),
                ("Ctrl+J", "Downloads"),
                ("Ctrl+Shift+O", "Bookmarks manager"),
                ("Ctrl+Shift+B", "Toggle bookmarks bar"),
                ("Ctrl+Shift+S", "Save screenshot"),
                ("Ctrl+Alt+R", "Reader mode"),
                ("Ctrl+Shift+L", "Read page aloud"),
                ("Ctrl+Shift+U", "Summarize this page"),
                ("Ctrl+Shift+F", "Focus mode (hide all chrome)"),
                ("Ctrl+Shift+A", "AI assistant"),
                ("Ctrl+Shift+H", "Coding agent"),
                ("Ctrl+Alt+M", "Multi Agent Terminal (4 parallel sessions)"),
                ("Ctrl+`", "Agent 1 terminal"),
                ("Ctrl+Shift+`", "PowerShell terminal"),
                ("Ctrl+K", "Command palette"),
                ("?", "Omnibox prefix — ask the AI"),
                ("Ctrl+,", "Settings"),
                ("Ctrl+P", "Print"),
                ("Ctrl+S", "Save page as"),
                ("Ctrl+O", "Open file"),
                ("Ctrl+Q", "Quit"),
                ("Ctrl+/", "Show this help"),
            ),
        ),
    )

    def show_shortcuts(self) -> None:
        """Keyboard shortcuts reference: a sized, scrolling dialog.

        The old version poured ~50 rows into QMessageBox.about — an
        uncapped, unscrollable single column that overflowed the screen
        (Dylan 2026-10-22). Now: a real dialog capped at 90% of the
        available screen, two balanced columns, and a scrolling text
        view. Every color comes from the ACTIVE theme palette — and the
        dialog surface itself is painted from the palette, so light
        themes (Arctic Light) get a light dialog, not the OS default.
        """
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTextBrowser, QVBoxLayout

        pal = palette(self.settings)
        show_retired = self._show_retired_tools()

        def section_html(title: str, rows: tuple) -> str:
            parts = [f"<tr><th colspan='2'>{_html.escape(title)}</th></tr>"]
            for keys, desc in rows:
                if keys == "Ctrl+Shift+H" and not show_retired:
                    continue  # retired tool: row hidden with the tool
                parts.append(
                    f"<tr><td><span class='kbd'>{_html.escape(keys)}</span></td>"
                    f"<td>{_html.escape(desc)}</td></tr>"
                )
            return "".join(parts)

        sections = [section_html(t, r) for t, r in self._SHORTCUT_SECTIONS]
        # Balance the columns by section (Tabs+Navigation+View | Features).
        left = "".join(sections[:3])
        right = "".join(sections[3:])
        body = f"""
        <style>
          table {{ border-collapse: collapse; }}
          td, th {{ padding: 3px 14px 3px 4px; text-align: left; font-size: 12px;
                    color: {pal["text"]}; vertical-align: top; }}
          th {{ color: {pal["accent"]}; font-weight: 600; font-size: 13px; padding-top: 10px; }}
          .kbd {{ background: {pal["card"]}; color: {pal["text"]};
                  border: 1px solid {pal["border"]}; border-radius: 4px;
                  padding: 1px 7px; font-family: monospace; font-size: 11px;
                  white-space: nowrap; }}
        </style>
        <table><tr>
          <td style="padding:0 10px 0 0"><table>{left}</table></td>
          <td style="padding:0 0 0 10px"><table>{right}</table></td>
        </tr></table>
        """

        dlg = QDialog(self)
        dlg.setWindowTitle(f"{self._app_display} — Shortcuts")
        dlg.setStyleSheet(f"QDialog {{ background: {pal['window']}; color: {pal['text']}; }}")
        lay = QVBoxLayout(dlg)
        view = QTextBrowser(dlg)
        view.setOpenExternalLinks(False)
        view.setHtml(body)
        view.setStyleSheet(
            f"QTextBrowser {{ background: {pal['window']}; color: {pal['text']}; border: none; }}"
        )
        lay.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dlg)
        buttons.rejected.connect(dlg.reject)
        buttons.accepted.connect(dlg.accept)
        lay.addWidget(buttons)
        # Cap the dialog at 90% of the available screen in both axes.
        width, height = 940, 660
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            width = min(width, int(avail.width() * 0.9))
            height = min(height, int(avail.height() * 0.9))
        dlg.resize(width, height)
        dlg.exec()

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

        dlg = QDialog(self)
        dlg.setWindowTitle(f"About {self._app_display}")
        dlg.setWindowIcon(self.windowIcon())
        lay = QVBoxLayout(dlg)
        text = QLabel(
            f"<h3>🍀 {self._app_display}</h3>"
            f"<p><b>Version:</b> {__version__}<br>"
            f"<b>Channel:</b> {channel}<br>"
            f"<b>Updates:</b> github.com/Dylanchess0320/LuckyD-Browser/releases</p>"
            + "<p>LuckyD — a trust-first AI browser. Chat-first AI sidebar with "
            "contextual skills, a deep research swarm, and local-first Ollama for $0 AI.</p>"
            "<p>Free, open source, no telemetry.</p>",
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

    # ── updates: GitHub releases page ─────────────────────────────────
    # The in-app download-and-install updater was unreliable (API rate limits,
    # proxies/VPNs, timing), so every update path here just opens the public
    # releases page in a tab. Thin shims keep the old call sites working.

    def _release_page(self) -> str:
        return getattr(self, "_release_page_url", None) or RELEASES_PAGE_URL

    def _open_releases_page(self) -> None:
        self.toast("Opening the releases page — grab the latest setup exe", "info")
        self.open_in_new_tab(QUrl(self._release_page()))

    def _current_version(self) -> str:
        from browser_core.updater import current_version

        return current_version()

    def check_for_updates(self, silent: bool = False) -> None:
        """Open the GitHub releases page in a tab (the auto-updater is retired)."""
        del silent  # the badge + menu item share this entry point
        self._open_releases_page()

    # NOTE: the old in-app updater chain that used to follow here (checker
    # thread, download dialog, silent install) was removed: Help > Check for
    # Updates and the toolbar arrow both land on the public GitHub releases
    # page via _open_releases_page() above.
