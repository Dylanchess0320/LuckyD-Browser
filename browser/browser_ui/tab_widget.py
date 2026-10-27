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
import time

from browser_core.lifecycle import (
    ACTIVE,
    DISCARDED,
    FROZEN,
    next_state,
    should_protect,
)
from browser_core.session import effective_url
from PySide6.QtCore import QPoint, QRect, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QTabBar,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .icons import letter_tile
from .theme import palette as _theme_palette
from .web_view import WebView

_SPIN_STEP_MS = 70
_SPIN_DEG_PER_STEP = 30
_PREVIEW_DELAY_MS = 450


def _rgba(hex_color: str, alpha: int) -> str:
    """A theme hex color as a translucent rgba() string (glass cards)."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return hex_color
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


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

    def __init__(self, parent=None, tabs=None):
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.ToolTip
            | Qt.WindowType.WindowTransparentForInput,
        )
        self._tabs = tabs
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        card = QWidget(self)
        card.setObjectName("preview_card")
        self._card = card
        self._apply_style()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(9)
        self._tile = QLabel(card)
        self._tile.setFixedSize(22, 22)
        self._tile.setScaledContents(True)
        top.addWidget(self._tile, 0, Qt.AlignmentFlag.AlignTop)
        self._title = QLabel(card)
        self._title.setObjectName("p_title")
        top.addWidget(self._title, 1)
        layout.addLayout(top)
        self._url = QLabel(card)
        self._url.setObjectName("p_url")
        layout.addWidget(self._url)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        self.setMaximumWidth(340)

    def _apply_style(self) -> None:
        """Theme-synced glass card (re-applied on every show)."""
        settings = getattr(getattr(self._tabs, "_mw", None), "settings", None)
        p = _theme_palette(settings)
        self._card.setStyleSheet(
            f"""
            QWidget#preview_card {{
                background: {_rgba(p["panel"], 235)};
                border: 1px solid {p["border"]};
                border-radius: 12px;
            }}
            QLabel {{ background: transparent; border: none; }}
            QLabel#p_title {{ color: {p["text"]}; font-weight: 600; font-size: 13px; }}
            QLabel#p_url {{ color: {p["muted"]}; font-size: 11px; }}
            """
        )

    def show_for(self, title: str, url: str, pos: QPoint) -> None:
        self._apply_style()  # theme may have changed since last hover
        self._tile.setPixmap(letter_tile(url, 22).pixmap(22, 22))
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
        self._preview = _TabPreview(self, self._tabs)
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
        self._preview.show_for(view.title(), self._tabs.view_url(view), pos)

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

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        tabs = self._tabs
        if not tabs._groups:
            return
        # A 3px color strip along the bottom marks group membership.
        painter = QPainter(self)
        for i in range(self.count()):
            gid = tabs.group_of(i)
            info = tabs.group_info(gid) if gid else None
            if info is None:
                continue
            rect = self.tabRect(i)
            if rect.isNull():
                continue
            painter.fillRect(
                rect.left() + 6, rect.bottom() - 2, rect.width() - 12, 3, QColor(info["color"])
            )
        painter.end()


class BrowserTabWidget(QTabWidget):
    # Tab-group accent colors, assigned round-robin to new groups.
    GROUP_COLORS = ("#5b9dff", "#34d399", "#fbbf24", "#f87171", "#b46bff", "#22d3ee")

    def __init__(self, main_window):
        super().__init__(main_window)
        self._mw = main_window
        self.setTabsClosable(True)
        self.setMovable(True)
        self.setDocumentMode(True)
        self.setElideMode(Qt.TextElideMode.ElideRight)

        self._closed_stack: list[str] = []
        self._tab_undo_stack: list[dict] = []
        self._undoing_tab_action = False
        self._pinned: set[int] = set()  # id() of pinned WebViews
        self._loading: set[int] = set()  # id() of views currently loading
        self._muted: set[int] = set()
        self._spin_angle = 0
        # Tab groups: gid -> {"name", "color", "collapsed"}; view id -> gid.
        self._groups: dict[str, dict] = {}
        self._tab_group: dict[int, str] = {}
        self._group_seq = 0

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

        # Memory saver: idle clock per view + 15s policy tick.
        self._idle_since: dict[int, float] = {}
        self._sleep = QTimer(self)
        self._sleep.setInterval(15_000)
        self._sleep.timeout.connect(self._tick_memory_saver)
        self._sleep.start()

    def _push_tab_undo(self, entry: dict) -> None:
        if self._undoing_tab_action:
            return
        self._tab_undo_stack.append(entry)
        self._tab_undo_stack = self._tab_undo_stack[-25:]

    def tab_undo_stack(self) -> list[dict]:
        """Return a copy of the reversible tab-bar action stack."""
        return list(self._tab_undo_stack)

    # ── lifecycle ────────────────────────────────────────────────────

    def new_tab(self, url=None, make_current=True, defer_load=False, title="") -> WebView:
        view = WebView(self._mw, self._mw.profile)
        view._ld_pending_url = ""
        view._ld_pending_title = ""
        index = self.addTab(view, "New Tab")
        self._apply_close_button(index, True)
        self._wire(view)
        if url is not None and defer_load and not make_current:
            # Lazy restore: keep the URL/title without starting a renderer.
            # _wake_view() performs the real load on first activation.
            view._ld_pending_url = url.toString()
            view._ld_pending_title = (title or "").strip()
            self._retitle(view, view.title())
        elif url is not None:
            view.setUrl(url)
        if make_current:
            self.setCurrentIndex(index)
        return view

    def view_url(self, view) -> str:
        """The tab's real URL — the pending URL while a deferred tab waits."""
        if view is None:
            return ""
        return effective_url(getattr(view, "_ld_pending_url", ""), view.url().toString())

    def close_tab(self, index: int) -> None:
        view = self.widget(index)
        if view is not None:
            url = self.view_url(view)
            was_pinned = id(view) in self._pinned
            if url.startswith(("http://", "https://", "file://")):
                self._closed_stack.append(url)
                self._closed_stack = self._closed_stack[-25:]
                self._push_tab_undo(
                    {
                        "kind": "close",
                        "url": url,
                        "index": index,
                        "pinned": was_pinned,
                        "summary": f"close tab {url[:40]}",
                    }
                )
            self._pinned.discard(id(view))
            self._muted.discard(id(view))
            self._loading.discard(id(view))
            self._tab_group.pop(id(view), None)
            self._idle_since.pop(id(view), None)
            with contextlib.suppress(Exception):
                view._ld_sleep = None
        self.removeTab(index)
        # Groups with no members left dissolve.
        for dead in [g for g in self._groups if g not in self._tab_group.values()]:
            del self._groups[dead]
        if view is not None:
            view.deleteLater()
        self._mw.on_tabs_changed()
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
        views: list[WebView] = []
        for i in range(self.count()):
            v = self.widget(i)
            if v is not None:
                views.append(v)
        return views

    # ── pinned tabs ──────────────────────────────────────────────────
    def is_pinned(self, index: int) -> bool:
        view = self.widget(index)
        return view is not None and id(view) in self._pinned

    def toggle_pin(self, index: int) -> None:
        view = self.widget(index)
        if view is None:
            return
        was_pinned = id(view) in self._pinned
        self._push_tab_undo(
            {
                "kind": "pin",
                "view_id": id(view),
                "was_pinned": was_pinned,
                "prev_index": index,
                "summary": f"{'unpin' if was_pinned else 'pin'} tab #{index}",
            }
        )
        if was_pinned:
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
        # Note: setTabButton/tabButton live on QTabBar in Qt6 (the QTabWidget
        # forwarding API is gone in PySide6) — always go through tabBar().
        if closable:
            btn = QToolButton(self.tabBar())
            btn.setObjectName("tab_close")
            btn.setText("\u2715")
            btn.setAutoRaise(True)
            btn.setFixedSize(18, 18)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setToolTip("Close tab (Ctrl+W)")
            btn.setAccessibleName("Close tab")
            btn.clicked.connect(lambda _=False, b=btn: self._close_for_button(b))
            self.tabBar().setTabButton(index, QTabBar.ButtonPosition.RightSide, btn)
        else:
            self.tabBar().setTabButton(index, QTabBar.ButtonPosition.RightSide, None)

    def _close_for_button(self, button) -> None:
        for i in range(self.count()):
            if self.tabBar().tabButton(i, QTabBar.ButtonPosition.RightSide) is button:
                self.close_tab(i)
                return

    def pinned_urls(self) -> list[str]:
        urls = []
        for i in range(self.count()):
            view = self.widget(i)
            if view is not None and id(view) in self._pinned:
                url = self.view_url(view)
                if url.startswith(("http://", "https://")):
                    urls.append(url)
        return urls

    # ── tab groups ────────────────────────────────────────────────────

    def create_group(self, name: str = "", color: str | None = None) -> str:
        """Register a group; returns its id."""
        self._group_seq += 1
        gid = f"g{self._group_seq}"
        if color is None:
            color = self.GROUP_COLORS[(self._group_seq - 1) % len(self.GROUP_COLORS)]
        self._groups[gid] = {
            "name": (name or "").strip() or f"Group {self._group_seq}",
            "color": color,
            "collapsed": False,
        }
        return gid

    def group_of(self, index: int) -> str | None:
        view = self.widget(index)
        return self._tab_group.get(id(view)) if view is not None else None

    def group_info(self, gid: str) -> dict | None:
        return self._groups.get(gid)

    def group_indexes(self, gid: str) -> list[int]:
        return [i for i in range(self.count()) if self.group_of(i) == gid]

    def set_tab_group(self, index: int, gid: str | None) -> None:
        """Assign (or clear, gid=None) the tab's group; refreshes chrome."""
        view = self.widget(index)
        if view is None:
            return
        vid = id(view)
        if gid and gid in self._groups:
            self._tab_group[vid] = gid
        else:
            self._tab_group.pop(vid, None)
        # Drop groups that no tab uses anymore.
        for dead in [g for g in self._groups if g not in self._tab_group.values()]:
            del self._groups[dead]
        self._retitle(view, view.title())
        self.tabBar().update()  # repaint the color strips
        self._mw.on_tabs_changed()

    def toggle_group_collapsed(self, gid: str) -> None:
        """Collapse = hide every member tab except the first (the chip tab)."""
        group = self._groups.get(gid)
        if group is None:
            return
        group["collapsed"] = not group["collapsed"]
        members = self.group_indexes(gid)
        for pos, index in enumerate(members):
            self.tabBar().setTabVisible(index, not group["collapsed"] or pos == 0)
        if group["collapsed"] and members:
            first_view = self.widget(members[0])
            if first_view is not None:
                self._retitle(first_view, first_view.title())
        self.tabBar().update()
        self._mw.on_tabs_changed()

    # ── tab context menu ─────────────────────────────────────────────
    def show_tab_menu(self, index: int, global_pos: QPoint) -> None:
        view = self.widget(index)
        if view is None:
            return
        menu = QMenu(self)
        menu.addAction("Duplicate Tab", lambda: self.duplicate_tab(index))
        pinned = self.is_pinned(index)
        menu.addAction("Unpin Tab" if pinned else "Pin Tab", lambda: self.toggle_pin(index))
        muted = id(view) in self._muted
        menu.addAction("Unmute Tab" if muted else "Mute Tab", lambda: self.toggle_mute(index))

        # ── tab groups ──
        gid = self.group_of(index)
        group_menu = menu.addMenu("Tab Group")
        group_menu.addAction("New Group from Tab…", lambda: self._group_new_from(index))
        for g, info in self._groups.items():
            if g != gid:
                group_menu.addAction(
                    f"Add to “{info['name']}”", lambda g=g: self.set_tab_group(index, g)
                )
        if gid:
            info = self._groups[gid]
            group_menu.addSeparator()
            group_menu.addAction("Remove from Group", lambda: self.set_tab_group(index, None))
            label = "Expand Group" if info.get("collapsed") else "Collapse Group"
            group_menu.addAction(label, lambda g=gid: self.toggle_group_collapsed(g))
        menu.addSeparator()
        menu.addAction("Reload Tab", view.reload)
        menu.addAction(
            "Copy Page URL",
            lambda: QGuiApplication.clipboard().setText(self.view_url(view)),
        )
        menu.addSeparator()
        menu.addAction("Close Other Tabs", lambda: self._close_others(index))
        menu.addAction("Close Tabs to the Right", lambda: self._close_right(index))
        menu.addAction("Close Duplicate Tabs", self.close_duplicates)
        reopen = menu.addAction("Reopen Closed Tab", self.reopen_last_closed)
        reopen.setEnabled(bool(self._closed_stack))
        undo_act = menu.addAction("Undo Tab Action", self.undo_last_tab_action)
        undo_act.setEnabled(bool(self._tab_undo_stack))
        menu.exec(global_pos)

    def duplicate_tab(self, index: int) -> int:
        """Duplicate the tab at ``index`` and record a reversible undo entry."""
        view = self.widget(index)
        if view is None:
            return -1
        url = self.view_url(view)
        new_view = self._mw.open_in_new_tab(QUrl(url))
        new_idx = self.indexOf(new_view) if new_view is not None else self.currentIndex()
        if new_view is not None:
            self._push_tab_undo(
                {
                    "kind": "duplicate",
                    "new_view_id": id(new_view),
                    "url": url,
                    "summary": f"duplicate tab #{index}",
                }
            )
        return new_idx

    def move_tab(self, from_index: int, to_index: int) -> int:
        """Move a tab from ``from_index`` to ``to_index`` and record undo."""
        count = self.count()
        if not 0 <= from_index < count:
            raise IndexError(f"tab index {from_index} out of range (0..{count - 1})")
        target = max(0, min(int(to_index), count - 1))
        if from_index != target:
            self._push_tab_undo(
                {
                    "kind": "move",
                    "from_index": from_index,
                    "to_index": target,
                    "summary": f"move tab #{from_index} → #{target}",
                }
            )
            self.tabBar().moveTab(from_index, target)
            self._mw.on_tabs_changed()
        return target

    def close_duplicates(self) -> int:
        """Close tabs whose URL already has an open sibling (keeps the first)."""
        seen: set[str] = set()
        closed = 0
        for i in range(self.count() - 1, -1, -1):
            if self.is_pinned(i):
                continue
            view = self.widget(i)
            url = self.view_url(view) if view else ""
            if url and url in seen:
                self.close_tab(i)
                closed += 1
            elif url:
                seen.add(url)
        if hasattr(self._mw, "toast"):
            self._mw.toast(f"Closed {closed} duplicate{'s' if closed != 1 else ''}")
        return closed

    def _group_new_from(self, index: int) -> None:
        name, ok = QInputDialog.getText(self, "New Tab Group", "Group name:")
        if not ok:
            return
        self.set_tab_group(index, self.create_group(name))

    def _close_others(self, keep: int) -> None:
        for i in range(self.count() - 1, -1, -1):
            if i != keep and not self.is_pinned(i):
                self.close_tab(i)

    def _close_right(self, index: int) -> None:
        for i in range(self.count() - 1, index, -1):
            if not self.is_pinned(i):
                self.close_tab(i)

    # ── audio / mute ─────────────────────────────────────────────────
    def is_muted(self, index: int) -> bool:
        view = self.widget(index)
        return view is not None and id(view) in self._muted

    def toggle_mute(self, index: int) -> None:
        view = self.widget(index)
        if view is None:
            return
        was_muted = id(view) in self._muted
        self._push_tab_undo(
            {
                "kind": "mute",
                "view_id": id(view),
                "was_muted": was_muted,
                "summary": f"{'unmute' if was_muted else 'mute'} tab #{index}",
            }
        )
        if was_muted:
            self._muted.discard(id(view))
            view.page().setAudioMuted(False)
        else:
            self._muted.add(id(view))
            view.page().setAudioMuted(True)
        self._retitle(view, view.title())

    def undo_last_tab_action(self) -> dict:
        """Ctrl+Alt+Z / POST /tabs/undo: revert the most recent tab-bar operation."""
        while self._tab_undo_stack:
            entry = self._tab_undo_stack.pop()
            kind = entry.get("kind")
            self._undoing_tab_action = True
            try:
                if kind == "close":
                    url = str(entry.get("url", "") or "")
                    if not url:
                        continue
                    if self._closed_stack and self._closed_stack[-1] == url:
                        self._closed_stack.pop()
                    new_view = self._mw.open_in_new_tab(QUrl(url))
                    new_idx = (
                        self.indexOf(new_view) if new_view is not None else self.currentIndex()
                    )
                    target_idx = int(entry.get("index", new_idx))
                    if 0 <= target_idx < self.count() and new_idx != target_idx:
                        self.tabBar().moveTab(new_idx, target_idx)
                        new_idx = target_idx
                    if entry.get("pinned") and 0 <= new_idx < self.count():
                        self.toggle_pin(new_idx)
                    return {
                        "undone": True,
                        "detail": f"reopened {url}",
                        "entry": entry,
                    }
                if kind == "pin":
                    vid = entry.get("view_id")
                    idx = next((i for i in range(self.count()) if id(self.widget(i)) == vid), -1)
                    if idx < 0:
                        continue
                    self.toggle_pin(idx)
                    prev_idx = int(entry.get("prev_index", idx))
                    cur_idx = next(
                        (i for i in range(self.count()) if id(self.widget(i)) == vid), idx
                    )
                    if 0 <= prev_idx < self.count() and cur_idx != prev_idx:
                        self.tabBar().moveTab(cur_idx, prev_idx)
                    return {
                        "undone": True,
                        "detail": f"reverted {entry.get('summary', 'pin')}",
                        "entry": entry,
                    }
                if kind == "mute":
                    vid = entry.get("view_id")
                    idx = next((i for i in range(self.count()) if id(self.widget(i)) == vid), -1)
                    if idx < 0:
                        continue
                    self.toggle_mute(idx)
                    return {
                        "undone": True,
                        "detail": f"reverted {entry.get('summary', 'mute')}",
                        "entry": entry,
                    }
                if kind == "move":
                    from_idx = int(entry.get("from_index", 0))
                    to_idx = int(entry.get("to_index", 0))
                    if 0 <= to_idx < self.count() and 0 <= from_idx < self.count():
                        self.tabBar().moveTab(to_idx, from_idx)
                        self._mw.on_tabs_changed()
                        return {
                            "undone": True,
                            "detail": f"moved tab #{to_idx} back to #{from_idx}",
                            "entry": entry,
                        }
                    continue
                if kind == "duplicate":
                    vid = entry.get("new_view_id")
                    idx = next((i for i in range(self.count()) if id(self.widget(i)) == vid), -1)
                    if idx < 0 or self.count() <= 1:
                        continue
                    self.close_tab(idx)
                    return {
                        "undone": True,
                        "detail": f"closed duplicated tab #{idx}",
                        "entry": entry,
                    }
            finally:
                self._undoing_tab_action = False
        return {"undone": False, "detail": "nothing to undo"}

    # ── loading spinner ──────────────────────────────────────────────
    def _tick_spinner(self) -> None:
        self._spin_angle = (self._spin_angle + _SPIN_DEG_PER_STEP) % 360
        # _loading holds view ids: tab indexes shift on close/reorder, so
        # resolve each spinner's tab fresh (and drop views that are gone).
        for vid in list(self._loading):
            index = next((i for i in range(self.count()) if id(self.widget(i)) == vid), -1)
            if index >= 0:
                self.setTabIcon(index, _spinner_icon(self._spin_angle))
            else:
                self._loading.discard(vid)

    def _start_loading(self, view: WebView) -> None:
        if self.indexOf(view) < 0:
            return
        self._loading.add(id(view))
        if not self._spinner.isActive():
            self._spinner.start()

    def _stop_loading(self, view: WebView) -> None:
        index = self.indexOf(view)
        if index < 0:
            return
        self._loading.discard(id(view))
        self.setTabIcon(index, view.icon())
        if not self._loading and self._spinner.isActive():
            self._spinner.stop()

    # ── signal wiring ────────────────────────────────────────────────

    def _wire(self, view: WebView) -> None:
        view.titleChanged.connect(lambda title, v=view: self._retitle(v, title))
        view.iconChanged.connect(lambda icon, v=view: self._icon(v, icon))
        view.urlChanged.connect(lambda url, v=view: self._url(v, url))
        # Session autosave: background tabs navigate too, and _url() only
        # fires for the current tab — the session snapshot needs every URL.
        view.urlChanged.connect(lambda _url, v=view: self._mw.on_tabs_changed())
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
            gid = self._tab_group.get(id(view))
            group = self._groups.get(gid) if gid else None
            members = self.group_indexes(gid) if (gid and group) else []
            if group and group.get("collapsed") and members and members[0] == index:
                # The visible chip for a collapsed group: "▸ name · n tabs".
                self.setTabText(index, f"▸ {group['name']} · {len(members)}")
            elif id(view) in self._pinned:
                self.setTabText(index, "")
            else:
                short = (
                    (title or "").strip()
                    or (getattr(view, "_ld_pending_title", "") or "").strip()
                    or "New Tab"
                )
                prefix = ""
                try:
                    if view.page().recentlyAudible():
                        prefix = "🔇 " if id(view) in self._muted else "🔊 "
                except AttributeError:
                    pass
                sleep = getattr(view, "_ld_sleep", None)
                if sleep in (FROZEN, DISCARDED) and not prefix:
                    prefix = "💤 "
                self.setTabText(index, prefix + short[:40])
            tip = title or (getattr(view, "_ld_pending_title", "") or "")
            if group:
                tip = f"[{group['name']}] {tip}"
            self.setTabToolTip(index, tip)
        if view is self.currentWidget():
            self._mw.on_title_changed(title or "")

    def _icon(self, view: WebView, icon) -> None:
        index = self.indexOf(view)
        if index >= 0 and id(view) not in self._loading:
            self.setTabIcon(index, icon)

    def _url(self, view: WebView, url) -> None:
        if view is self.currentWidget():
            self._mw.on_url_changed(url)

    def _on_current_changed(self, _index: int) -> None:
        view = self.currentWidget()
        if view is not None:
            self._wake_view(view)
            self._mw.on_tab_switched(view)
        self._mark_idle_clocks()
        self._mw.on_tabs_changed()  # active index is part of the session

    def _set_page_visible(self, view: WebView, visible: bool) -> None:
        """Set a page's visibility only when it actually changes.

        Tab switches used to call setVisible on EVERY page even when the
        state was unchanged — redundant renderer/compositor toggling on
        every switch is what made the window flicker while the agent kept
        pages animating (scroll/highlight JS)."""
        if getattr(view, "_ld_page_visible", True) == visible:
            return
        view._ld_page_visible = visible
        with contextlib.suppress(Exception):
            view.page().setVisible(visible)

    def _mark_idle_clocks(self) -> None:
        now = time.monotonic()
        current = self.currentWidget()
        for i in range(self.count()):
            view = self.widget(i)
            if view is None:
                continue
            vid = id(view)
            if view is current:
                self._idle_since.pop(vid, None)
            else:
                self._idle_since.setdefault(vid, now)
            self._set_page_visible(view, view is current)

    def _wake_view(self, view: WebView) -> None:
        """Bring a frozen/discarded (or lazily restored) tab back to Active."""
        self._idle_since.pop(id(view), None)
        pending = getattr(view, "_ld_pending_url", "")
        if pending:
            # First activation of a deferred restore tab: load it now.
            view._ld_pending_url = ""
            view._ld_sleep = None
            with contextlib.suppress(Exception):
                view.setUrl(QUrl(pending))
            return
        view._ld_sleep = None
        page = view.page()
        self._set_page_visible(view, True)
        with contextlib.suppress(Exception):
            state = getattr(page, "LifecycleState", None)
            if state is not None:
                page.setLifecycleState(state.Active)
        self._retitle(view, view.title())

    def _tick_memory_saver(self) -> None:
        settings = getattr(self._mw, "settings", None)
        if settings is None or not bool(settings.get("memory_saver", True)):
            return
        freeze_after = int(settings.get("memory_saver_freeze_sec", 300) or 300)
        discard_after = int(settings.get("memory_saver_discard_sec", 900) or 900)
        now = time.monotonic()
        current = self.currentWidget()
        for i in range(self.count()):
            view = self.widget(i)
            if view is None:
                continue
            if getattr(view, "_ld_pending_url", ""):
                continue  # never loaded — nothing to freeze or discard
            audible = False
            with contextlib.suppress(Exception):
                audible = bool(view.page().recentlyAudible())
            if should_protect(
                url=self.view_url(view),
                is_current=view is current,
                pinned=self.is_pinned(i),
                audible=audible,
                agent_busy=bool(getattr(view, "_ld_agent_busy", False)),
            ):
                continue
            idle = now - self._idle_since.setdefault(id(view), now)
            wanted = next_state(idle, freeze_after, discard_after)
            self._apply_sleep(view, wanted)

    def _apply_sleep(self, view: WebView, wanted: str) -> None:
        if getattr(view, "_ld_agent_busy", False):
            return  # an agent run owns this tab — never freeze/discard it mid-run
        current = getattr(view, "_ld_sleep", None) or ACTIVE
        if wanted in (current, ACTIVE):
            return
        page = view.page()
        state = getattr(page, "LifecycleState", None)
        if state is None:
            return
        try:
            self._set_page_visible(view, False)
            if wanted == DISCARDED:
                page.setLifecycleState(state.Discarded)
            elif wanted == FROZEN:
                page.setLifecycleState(state.Frozen)
            else:
                return
        except Exception:
            return
        view._ld_sleep = wanted
        self._retitle(view, view.title())
