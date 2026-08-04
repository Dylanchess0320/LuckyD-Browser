"""Tab strip: futuristic custom tab bar + tab lifecycle.

Custom tab bar features:
  * animated neon spinner in place of the favicon while a page loads
  * pinned tabs (icon-only, left-aligned, no close button, persisted by URL)
  * hover preview card (title + URL, glass style)
  * middle-click to close, double-click empty strip for a new tab,
    mouse wheel to cycle tabs
  * context menu: duplicate / pin / mute / reload / copy URL /
    close others / close to the right / reopen closed
  * a per-window stack of recently closed tabs (Ctrl+Shift+T restores)
"""

from __future__ import annotations

import contextlib

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QLabel,
    QMenu,
    QTabBar,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .web_view import WebView

_SPIN_STEP_MS = 70
_SPIN_DEG_PER_STEP = 30
_PREVIEW_DELAY_MS = 450


def _spinner_icon(angle: int, accent: str = "#5b9dff") -> QIcon:
    """One frame of the tab loading spinner: a 100-degree arc."""
    pix = QPixmap(16, 16)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(accent))
    pen.setWidth(2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.drawArc(QRect(2, 2, 12, 12), angle * 16, 100 * 16)
    painter.end()
    return QIcon(pix)


class _TabPreview(QWidget):
    """Glass hover card shown above a tab after a short delay."""

    def __init__(self, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.ToolTip
            | Qt.WindowType.WindowTransparentForInput,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        card = QWidget(self)
        card.setObjectName("preview_card")
        card.setStyleSheet(
            """
            QWidget#preview_card {
                background: rgba(16, 21, 31, 235);
                border: 1px solid rgba(255, 255, 255, 30);
                border-radius: 12px;
            }
            QLabel { background: transparent; border: none; }
            QLabel#p_title { color: #e8ecf5; font-weight: 600; font-size: 13px; }
            QLabel#p_url { color: #8b93a7; font-size: 11px; }
            """
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        self._title = QLabel(card)
        self._title.setObjectName("p_title")
        self._url = QLabel(card)
        self._url.setObjectName("p_url")
        layout.addWidget(self._title)
        layout.addWidget(self._url)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        self.setMaximumWidth(340)

    def show_for(self, title: str, url: str, pos: QPoint) -> None:
        self._title.setText(title or "New Tab")
        self._url.setText(url[:90])
        self.adjustSize()
        self.move(pos)
        self.show()


class BrowserTabBar(QTabBar):
    """QTabBar with middle-click close, wheel cycling, hover preview."""

    def __init__(self, tabs: BrowserTabWidget):
        super().__init__(tabs)
        self._tabs = tabs
        self._preview = _TabPreview(self)
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.timeout.connect(self._show_preview)
        self._hover_index = -1
        self.setMouseTracking(True)
        self.tabHovered.connect(self._on_hovered)

    # ── hover preview ────────────────────────────────────────────────
    def _on_hovered(self, index: int) -> None:
        self._hover_index = index
        self._preview.hide()
        if index >= 0:
            self._hover_timer.start(_PREVIEW_DELAY_MS)

    def _show_preview(self) -> None:
        index = self._hover_index
        if index < 0 or index >= self.count() or not self.underMouse():
            return
        view = self._tabs.widget(index)
        if view is None:
            return
        rect = self.tabRect(index)
        pos = self.mapToGlobal(QPoint(rect.left(), rect.bottom() + 6))
        self._preview.show_for(view.title(), view.url().toString(), pos)

    def leaveEvent(self, event):  # noqa: N802 (Qt API)
        self._hover_timer.stop()
        self._preview.hide()
        super().leaveEvent(event)

    # ── mouse / wheel ────────────────────────────────────────────────
    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.MiddleButton:
            index = self.tabAt(event.position().toPoint())
            if index >= 0:
                self._tabs.close_tab(index)
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.tabAt(event.position().toPoint()) < 0
        ):
            self._tabs._mw.new_tab()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event):  # noqa: N802
        step = -1 if event.angleDelta().y() > 0 else 1
        self._tabs._mw._cycle_tab(step)
        event.accept()

    def contextMenuEvent(self, event):  # noqa: N802
        index = self.tabAt(event.pos())
        if index >= 0:
            self._tabs.show_tab_menu(index, self.mapToGlobal(event.pos()))


class BrowserTabWidget(QTabWidget):
    def __init__(self, main_window):
        super().__init__(main_window)
        self._mw = main_window
        self.setTabsClosable(True)
        self.setMovable(True)
        self.setDocumentMode(True)
        self.setElideMode(Qt.TextElideMode.ElideRight)

        self._closed_stack: list[str] = []
        self._pinned: set[int] = set()  # id() of pinned WebViews
        self._loading: set[int] = set()  # indexes currently loading
        self._muted: set[int] = set()
        self._spin_angle = 0

        # Fall back to the stock tab bar on failure (loses preview/wheel extras).
        with contextlib.suppress(Exception):
            # protected in C++, exposed as public by PySide6
            self.setTabBar(BrowserTabBar(self))

        plus = QToolButton(self)
        plus.setObjectName("tab_plus")
        plus.setText("＋")
        plus.setAutoRaise(True)
        plus.setToolTip("New Tab (Ctrl+T)")
        plus.clicked.connect(lambda: self._mw.new_tab())
        self.setCornerWidget(plus, Qt.Corner.TopRightCorner)

        self._spinner = QTimer(self)
        self._spinner.setInterval(_SPIN_STEP_MS)
        self._spinner.timeout.connect(self._tick_spinner)

        self.tabCloseRequested.connect(self.close_tab)
        self.currentChanged.connect(self._on_current_changed)

    # ── lifecycle ────────────────────────────────────────────────────

    def new_tab(self, url=None, make_current=True) -> WebView:
        view = WebView(self._mw, self._mw.profile)
        index = self.addTab(view, "New Tab")
        self._wire(view)
        if make_current:
            self.setCurrentIndex(index)
        if url is not None:
            view.setUrl(url)
        return view

    def close_tab(self, index: int) -> None:
        view = self.widget(index)
        if view is not None:
            url = view.url().toString()
            if url.startswith(("http://", "https://", "file://")):
                self._closed_stack.append(url)
                self._closed_stack = self._closed_stack[-25:]
            self._pinned.discard(id(view))
            self._muted.discard(id(view))
        self._loading.discard(index)
        self.removeTab(index)
        self._loading = {i for i in self._loading if i < self.count()}
        if view is not None:
            view.deleteLater()
        if self.count() == 0:
            self._mw.close()

    def reopen_last_closed(self) -> bool:
        """Ctrl+Shift+T: restore the most recently closed tab."""
        from PySide6.QtCore import QUrl

        while self._closed_stack:
            url = self._closed_stack.pop()
            if url:
                self._mw.open_in_new_tab(QUrl(url))
                return True
        return False

    def current_view(self) -> WebView | None:
        return self.currentWidget()

    def all_views(self) -> list[WebView]:
        return [self.widget(i) for i in range(self.count())]

    # ── pinned tabs ──────────────────────────────────────────────────
    def is_pinned(self, index: int) -> bool:
        view = self.widget(index)
        return view is not None and id(view) in self._pinned

    def toggle_pin(self, index: int) -> None:
        view = self.widget(index)
        if view is None:
            return
        if id(view) in self._pinned:
            self._pinned.discard(id(view))
            self._retitle(view, view.title())
            self._apply_close_button(index, True)
            self.tabBar().moveTab(index, len(self._pinned))
        else:
            self._pinned.add(id(view))
            self.setTabText(index, "")
            self._apply_close_button(index, False)
            self.tabBar().moveTab(index, len(self._pinned) - 1)
        self._mw.on_pins_changed()

    def _apply_close_button(self, index: int, closable: bool) -> None:
        if closable:
            btn = QToolButton(self)
            btn.setText("✕")
            btn.setAutoRaise(True)
            btn.clicked.connect(lambda _=False, b=btn: self._close_for_button(b))
            self.setTabButton(index, QTabBar.ButtonPosition.RightSide, btn)
        else:
            self.setTabButton(index, QTabBar.ButtonPosition.RightSide, None)

    def _close_for_button(self, button) -> None:
        for i in range(self.count()):
            if self.tabButton(i, QTabBar.ButtonPosition.RightSide) is button:
                self.close_tab(i)
                return

    def pinned_urls(self) -> list[str]:
        urls = []
        for i in range(self.count()):
            view = self.widget(i)
            if view is not None and id(view) in self._pinned:
                url = view.url().toString()
                if url.startswith(("http://", "https://")):
                    urls.append(url)
        return urls

    # ── tab context menu ─────────────────────────────────────────────
    def show_tab_menu(self, index: int, global_pos: QPoint) -> None:
        view = self.widget(index)
        if view is None:
            return
        mw = self._mw
        menu = QMenu(self)
        menu.addAction("Duplicate Tab", lambda: mw.open_in_new_tab(view.url()))
        pinned = self.is_pinned(index)
        menu.addAction("Unpin Tab" if pinned else "Pin Tab", lambda: self.toggle_pin(index))
        muted = id(view) in self._muted
        menu.addAction("Unmute Tab" if muted else "Mute Tab", lambda: self.toggle_mute(index))
        menu.addSeparator()
        menu.addAction("Reload Tab", view.reload)
        menu.addAction(
            "Copy Page URL",
            lambda: QGuiApplication.clipboard().setText(view.url().toString()),
        )
        menu.addSeparator()
        menu.addAction("Close Other Tabs", lambda: self._close_others(index))
        menu.addAction("Close Tabs to the Right", lambda: self._close_right(index))
        reopen = menu.addAction("Reopen Closed Tab", self.reopen_last_closed)
        reopen.setEnabled(bool(self._closed_stack))
        menu.exec(global_pos)

    def _close_others(self, keep: int) -> None:
        for i in range(self.count() - 1, -1, -1):
            if i != keep and not self.is_pinned(i):
                self.close_tab(i)

    def _close_right(self, index: int) -> None:
        for i in range(self.count() - 1, index, -1):
            if not self.is_pinned(i):
                self.close_tab(i)

    # ── audio / mute ─────────────────────────────────────────────────
    def toggle_mute(self, index: int) -> None:
        view = self.widget(index)
        if view is None:
            return
        if id(view) in self._muted:
            self._muted.discard(id(view))
            view.page().setAudioMuted(False)
        else:
            self._muted.add(id(view))
            view.page().setAudioMuted(True)
        self._retitle(view, view.title())

    # ── loading spinner ──────────────────────────────────────────────
    def _tick_spinner(self) -> None:
        self._spin_angle = (self._spin_angle + _SPIN_DEG_PER_STEP) % 360
        for index in list(self._loading):
            if 0 <= index < self.count():
                self.setTabIcon(index, _spinner_icon(self._spin_angle))

    def _start_loading(self, view: WebView) -> None:
        index = self.indexOf(view)
        if index < 0:
            return
        self._loading.add(index)
        if not self._spinner.isActive():
            self._spinner.start()

    def _stop_loading(self, view: WebView) -> None:
        index = self.indexOf(view)
        if index < 0:
            return
        self._loading.discard(index)
        self.setTabIcon(index, view.icon())
        if not self._loading and self._spinner.isActive():
            self._spinner.stop()

    # ── signal wiring ────────────────────────────────────────────────

    def _wire(self, view: WebView) -> None:
        view.titleChanged.connect(lambda title, v=view: self._retitle(v, title))
        view.iconChanged.connect(lambda icon, v=view: self._icon(v, icon))
        view.urlChanged.connect(lambda url, v=view: self._url(v, url))
        view.loadStarted.connect(lambda v=view: self._on_load_started(v))
        view.loadFinished.connect(lambda ok, v=view: self._on_load_finished(v, ok))
        view.loadProgress.connect(lambda p, v=view: self._mw.on_load_progress(v, p))
        page = view.page()
        page.linkHovered.connect(self._mw.on_link_hovered)
        page.fullScreenRequested.connect(self._mw.on_fullscreen_requested)
        # older Qt versions lack the media-audible signal
        with contextlib.suppress(AttributeError):
            page.recentlyAudibleChanged.connect(lambda audible, v=view: self._retitle(v, v.title()))

    def _on_load_started(self, view: WebView) -> None:
        self._start_loading(view)
        self._mw.on_load_started(view)

    def _on_load_finished(self, view: WebView, ok: bool) -> None:
        self._stop_loading(view)
        self._mw.on_load_finished(view, ok)

    def _retitle(self, view: WebView, title: str) -> None:
        index = self.indexOf(view)
        if index >= 0:
            if id(view) in self._pinned:
                self.setTabText(index, "")
            else:
                short = (title or "").strip() or "New Tab"
                prefix = ""
                try:
                    if view.page().recentlyAudible():
                        prefix = "🔇 " if id(view) in self._muted else "🔊 "
                except AttributeError:
                    pass
                self.setTabText(index, prefix + short[:40])
            self.setTabToolTip(index, title or "")
        if view is self.currentWidget():
            self._mw.on_title_changed(title or "")

    def _icon(self, view: WebView, icon) -> None:
        index = self.indexOf(view)
        if index >= 0 and index not in self._loading:
            self.setTabIcon(index, icon)

    def _url(self, view: WebView, url) -> None:
        if view is self.currentWidget():
            self._mw.on_url_changed(url)

    def _on_current_changed(self, _index: int) -> None:
        view = self.currentWidget()
        if view is not None:
            self._mw.on_tab_switched(view)
