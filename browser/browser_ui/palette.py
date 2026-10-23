"""Command palette: Ctrl+K for fuzzy search over tabs, bookmarks, history, actions."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QIcon, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from .icons import letter_tile

# Row model roles: every row says what it IS (kind), where it goes (detail)
# and, for actions, which shortcut runs it (hint) — no mystery-meat list.
ROLE_ACTION = Qt.ItemDataRole.UserRole
ROLE_KIND = Qt.ItemDataRole.UserRole + 1
ROLE_DETAIL = Qt.ItemDataRole.UserRole + 2
ROLE_HINT = Qt.ItemDataRole.UserRole + 3

ROW_H = 36
MAX_VISIBLE_ROWS = 9
MAX_ROWS = 50  # rendered-row cap; typing narrows further
EMPTY_CAPS = {"Bookmark": 10, "History": 10}  # per-kind caps for the empty query

# Real bindings (menus / _build_shortcuts in main_window.py) for the hint
# column. Actions without a binding simply show their kind instead.
_ACTION_SHORTCUTS = {
    "New Tab": "Ctrl+T",
    "AI Assistant": "Ctrl+Shift+A",
    "Summarize This Page": "Ctrl+Shift+U",
    "Read Aloud": "Ctrl+Shift+L",
    "Save Screenshot": "Ctrl+Shift+S",
    "Focus Mode": "Ctrl+Shift+F",
    "Toggle Bookmarks Bar": "Ctrl+Shift+B",
    "Bookmarks": "Ctrl+Shift+O",
    "History": "Ctrl+H",
    "Settings": "Ctrl+,",
    "Coding Agent": "Ctrl+Shift+H",
    "Agent Terminal": "Ctrl+`",
    "PowerShell Terminal": "Ctrl+Shift+`",
}


def _score(q: str, t: str) -> int:
    if not q or not t:
        return 0
    q, t = q.lower(), t.lower()
    s, idx = 0, 0
    for c in t:
        if idx < len(q) and c == q[idx]:
            s += 15 + (len(q) - idx) * 3
            idx += 1
        else:
            s -= 2
    return max(0, s + 20 if idx == len(q) else s)


def _is_subsequence(q: str, t: str) -> bool:
    it = iter(t.lower())
    return all(c in it for c in q.lower())


def _pretty_url(u: str) -> str:
    """Readable row detail: scheme stripped; internal pages show nothing."""
    if not u or "newtab" in u or u.endswith("dashboard.html"):
        return ""
    for scheme in ("https://", "http://", "file://"):
        if u.startswith(scheme):
            return u[len(scheme) :]
    return u


def _contrast(a: str, b: str) -> float:
    def lum(hexcolor: str) -> float:
        h = hexcolor.lstrip("#")
        r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))

        def f(c: float) -> float:
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)

    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _on_accent(p: dict) -> str:
    """Most readable theme-token foreground on top of the accent color."""
    return max((p["text"], p["window"]), key=lambda c: _contrast(c, p["accent"]))


class _RowDelegate(QStyledItemDelegate):
    """Paints one palette row: icon, title (+muted detail), and a muted
    right-aligned tag — the action's shortcut, else the row's kind."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._p: dict = {}
        self._on_accent = ""

    def set_theme(self, p: dict) -> None:
        self._p = p
        self._on_accent = _on_accent(p) if p else ""

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # noqa: N802
        return QSize(0, ROW_H)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        if not self._p:
            super().paint(painter, option, index)
            return
        p = self._p
        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        kind = index.data(ROLE_KIND) or ""
        title = index.data(Qt.ItemDataRole.DisplayRole) or ""
        detail = index.data(ROLE_DETAIL) or ""
        hint = index.data(ROLE_HINT) or ""

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg_rect = rect.adjusted(2, 1, -2, -1)
        if selected:
            painter.setBrush(QColor(p["accent"]))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(bg_rect, 8, 8)
        elif hovered and kind != "Info":
            painter.setBrush(QColor(p["panel2"]))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(bg_rect, 8, 8)

        fg = QColor(self._on_accent) if selected else QColor(p["text"])
        muted = QColor(self._on_accent) if selected else QColor(p["muted"])
        if selected:
            muted.setAlpha(190)
        if kind == "Info":
            fg = muted

        icon = index.data(Qt.ItemDataRole.DecorationRole)
        x = rect.left() + 12
        if isinstance(icon, QIcon) and not icon.isNull():
            icon.paint(painter, QRect(rect.left() + 10, rect.top() + 9, 18, 18))
            x = rect.left() + 38

        fm = QFontMetrics(option.font)
        tag = hint or ("" if kind == "Info" else kind)
        tag_w = fm.horizontalAdvance(tag) + 12 if tag else 0
        text_rect = QRect(
            x, rect.top(), rect.width() - (x - rect.left()) - tag_w - 12, rect.height()
        )
        painter.setFont(option.font)
        if detail:
            full = f"{title} — {detail}"
            if fm.horizontalAdvance(full) <= text_rect.width():
                painter.setPen(fg)
                painter.drawText(
                    text_rect,
                    Qt.AlignmentFlag.AlignVCenter,
                    fm.elidedText(title, Qt.TextElideMode.ElideRight, text_rect.width()),
                )
                tw = fm.horizontalAdvance(title)
                painter.setPen(muted)
                painter.drawText(
                    QRect(x + tw, rect.top(), text_rect.width() - tw, rect.height()),
                    Qt.AlignmentFlag.AlignVCenter,
                    f" — {detail}",
                )
            else:
                painter.setPen(fg)
                painter.drawText(
                    text_rect,
                    Qt.AlignmentFlag.AlignVCenter,
                    fm.elidedText(full, Qt.TextElideMode.ElideRight, text_rect.width()),
                )
        else:
            painter.setPen(fg)
            painter.drawText(
                text_rect,
                Qt.AlignmentFlag.AlignVCenter,
                fm.elidedText(title, Qt.TextElideMode.ElideRight, text_rect.width()),
            )
        if tag:
            painter.setPen(muted)
            painter.drawText(
                QRect(rect.left(), rect.top(), rect.width() - 12, rect.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                tag,
            )
        painter.restore()


class CommandPalette(QWidget):
    closed = Signal()

    def __init__(self, mw: QWidget):
        super().__init__(mw, Qt.WindowType.FramelessWindowHint)
        self.setObjectName("palette_card")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # Without this, a plain QWidget silently ignores its stylesheet
        # background — the card's panel fill + border never paint at all.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        # NOTE: no WA_ShowWithoutActivating — the palette must steal keyboard
        # focus on open, otherwise typing keeps going to the web page.
        self._mw = mw
        self.setWindowOpacity(0.97)
        self._card_bg = ""
        self._card_border = ""

        # The palette IS the card: layout lives on the window itself, so the
        # window's size is the card's size (the pre-fix build sized only an
        # inner child, leaving a 100x30 window that clipped the card).
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        self._e = QLineEdit(self)
        self._e.setPlaceholderText(
            "Search tabs, bookmarks, history, actions…  (↑↓ navigate · Enter open · Esc close)"
        )
        self._e.returnPressed.connect(self._activate)
        self._e.textChanged.connect(self._filter)
        # Tab / Shift+Tab and arrows cycle the list WITHOUT moving keyboard
        # focus out of the input — otherwise the first Tab traps focus in
        # the list and typing goes nowhere (the classic palette focus trap).
        # Escape is claimed here too: the main window's window-level Esc
        # shortcut starves the palette's keyPressEvent via ShortcutOverride,
        # so Esc is intercepted in the filter BEFORE the shortcut sees it.
        self._e.installEventFilter(self)
        self._e.setFocus()

        self._lst = QListWidget(self)
        self._delegate = _RowDelegate(self._lst)
        self._lst.setItemDelegate(self._delegate)
        self._lst.setUniformItemSizes(True)
        self._lst.setMouseTracking(True)
        self._lst.viewport().setMouseTracking(True)
        self._lst.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._lst.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # itemActivated covers Enter-in-list AND double-click; connecting
        # itemDoubleClicked as well would fire the action twice.
        self._lst.itemActivated.connect(lambda _it: self._activate())
        self._lst.installEventFilter(self)
        lay.addWidget(self._e)
        lay.addWidget(self._lst)

        self._items: list[tuple[str, str, str, str, QIcon | None, Callable]] = []
        self._n_actionable = 0
        self._width = 640
        self._load_items()
        self._apply_style()
        # Click-away dismissal (with changeEvent's WindowDeactivate below):
        # keyboard focus landing anywhere outside the palette closes it.
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._on_focus_changed)
        self.hide()

    def _on_focus_changed(self, _old: QWidget | None, new: QWidget | None) -> None:
        if self.isVisible() and new is not None and new is not self and not self.isAncestorOf(new):
            self.close_palette()

    def _apply_style(self) -> None:
        """Theme-synced card styling (re-applied on every open)."""
        from .theme import palette as theme_palette

        p = theme_palette(self._mw.settings)
        self._delegate.set_theme(p)
        self._card_bg = p["panel"]
        self._card_border = p["border"]
        self.setStyleSheet(
            f"""
            QWidget#palette_card {{
                background: {p["panel"]};
                border: 1px solid {p["border"]};
                border-radius: 14px;
            }}
            QLineEdit {{
                background: {p["card"]}; color: {p["text"]};
                border: 1px solid {p["border"]}; border-radius: 10px;
                padding: 9px 13px; selection-background-color: {p["accent"]};
                selection-color: {p["text"]};
            }}
            QLineEdit:focus {{ border-color: {p["accent"]}; }}
            QListWidget {{
                background: transparent; border: none; outline: none;
                color: {p["text"]};
            }}
            QListWidget::item {{ padding: 0; border: none; background: transparent; }}
            """
        )

    def _load_items(self) -> None:
        # Group order for the empty query: commands first, then open tabs,
        # workspaces, and capped bookmarks/history.
        self._items = []
        actions = [
            ("New Tab", self._mw.new_tab),
            ("AI Assistant", self._mw.show_assistant),
            ("Summarize This Page", self._mw.summarize_page),
            ("Read Aloud", self._mw.read_aloud_page),
            ("Site Permissions…", self._mw.open_site_permissions),
            ("Save Current as Workspace…", self._mw.save_workspace),
            ("Deep Researcher Swarm…", self._mw.open_deep_research),
            ("Coding Agent", self._mw.open_hq),
            ("Agent Terminal", self._mw.open_terminal),
            ("PowerShell Terminal", lambda: self._mw.open_terminal("powershell")),
            ("Workflows", self._mw.open_workflows),
            ("Save Screenshot", self._mw.save_screenshot),
            ("Focus Mode", self._mw.toggle_focus_mode),
            ("Toggle Bookmarks Bar", self._mw.bm_bar_act.toggle),
            ("Bookmarks", self._mw.open_bookmarks),
            ("History", self._mw.open_history),
            ("Settings", self._mw.open_settings),
        ]
        # Retired tools (HQ, Workflows, Deep Researcher) stay out of the
        # palette unless show_retired_tools is flipped back on.
        try:
            show_retired = bool(self._mw.settings.get("show_retired_tools", False))
        except Exception:
            show_retired = False
        if not show_retired:
            retired = {"Deep Researcher Swarm…", "Coding Agent", "Workflows"}
            actions = [(label, act) for label, act in actions if label not in retired]
        for label, action in actions:
            self._items.append(
                ("Action", label, "", _ACTION_SHORTCUTS.get(label, ""), None, action)
            )
        t = self._mw.tabs
        for i in range(t.count()):
            v = t.widget(i)
            if v:
                u, tt = v.url().toString(), v.title() or "New Tab"
                self._items.append(
                    (
                        "Tab",
                        tt,
                        _pretty_url(u),
                        "",
                        v.icon(),
                        lambda vv=v: self._mw.tabs.setCurrentWidget(vv),  # switch, don't dupe
                    )
                )
        store = getattr(getattr(self._mw, "_app", None), "workspaces", None)
        if store is not None:
            for ws in store.list():
                self._items.append(
                    (
                        "Workspace",
                        ws["name"],
                        "",
                        "",
                        None,
                        lambda i=ws["id"]: self._mw.switch_workspace(i),
                    )
                )
        for u, t, *_ in self._mw.storage.bookmarks()[:30]:
            self._items.append(
                (
                    "Bookmark",
                    t or u,
                    _pretty_url(u),
                    "",
                    letter_tile(u),
                    lambda uu=u: self._mw.open_in_new_tab(uu),
                )
            )
        for u, t, *_ in self._mw.storage.recent(100):
            self._items.append(
                (
                    "History",
                    t or u,
                    _pretty_url(u),
                    "",
                    letter_tile(u),
                    lambda uu=u: self._mw.open_in_new_tab(uu),
                )
            )

    def paintEvent(self, e) -> None:  # noqa: N802  # Qt virtual override
        # Paint the card ourselves: a plain top-level QWidget's stylesheet
        # background is not reliably painted (it silently never appeared —
        # part of why the palette looked broken), so the rounded panel +
        # border are drawn deterministically from the theme tokens.
        if self._card_bg:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(QColor(self._card_bg))
            painter.setPen(QColor(self._card_border))
            painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 14, 14)
            painter.end()
        super().paintEvent(e)

    def show_palette(self) -> None:
        self._apply_style()  # theme may have changed since last open
        self._e.clear()
        self._load_items()  # reload fresh data
        self._width = max(300, min(640, self._mw.width() - 32))
        self._filter("")
        self._position()
        self.show()
        self.raise_()
        self.activateWindow()  # Windows needs the explicit activation nudge
        self._e.setFocus(Qt.FocusReason.PopupFocusReason)

    def _position(self) -> None:
        mw, mh = self._mw.width(), self._mw.height()
        x = self._mw.x() + max((mw - self.width()) // 2, 8)
        y = self._mw.y() + max(int(mh * 0.14), 12)
        if y + self.height() > self._mw.y() + mh - 8:
            y = max(self._mw.y() + mh - 8 - self.height(), self._mw.y() + 8)
        self.move(x, y)

    def _resize_to_content(self) -> None:
        rows = min(max(self._lst.count(), 1), MAX_VISIBLE_ROWS)
        list_h = rows * ROW_H + 4
        total = 20 + self._e.sizeHint().height() + 8 + list_h
        cap = max(int(self._mw.height() * 0.8), 120)
        if total > cap:  # small window: shrink the list, never clip the card
            list_h = max(cap - 28 - self._e.sizeHint().height(), ROW_H + 4)
            total = cap
        self._lst.setFixedHeight(list_h)
        self.resize(self._width, total)

    def _filter(self, text: str) -> None:
        q = text.strip().lower()
        self._lst.clear()
        rows: list[tuple[str, str, str, str, QIcon | None, Callable]] = []
        overflow = 0
        if not q:
            counts: dict[str, int] = {}
            for it in self._items:
                cap = EMPTY_CAPS.get(it[0])
                if cap is not None:
                    counts[it[0]] = counts.get(it[0], 0) + 1
                    if counts[it[0]] > cap:
                        continue
                rows.append(it)
        else:
            scored = []
            for it in self._items:
                kind, title, detail, _hint, _ico, _act = it
                hay = f"{title} {detail} {kind}"
                if q in hay.lower():
                    scored.append((1, _score(q, hay), it))
                elif _is_subsequence(q, hay):
                    scored.append((0, _score(q, hay), it))
            scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
            rows = [it for _f, _s, it in scored]
            overflow = max(len(rows) - MAX_ROWS, 0)
            rows = rows[:MAX_ROWS]
        self._n_actionable = len(rows)
        for kind, title, detail, hint, ico, act in rows:
            item = QListWidgetItem(title)
            item.setData(ROLE_ACTION, act)
            item.setData(ROLE_KIND, kind)
            item.setData(ROLE_DETAIL, detail)
            item.setData(ROLE_HINT, hint)
            if ico:
                item.setIcon(ico)
            self._lst.addItem(item)
        if q and not rows:
            self._add_info_row(f'No matches for "{text.strip()}" — Esc to close')
        elif q and overflow:
            self._add_info_row(f"… {overflow} more matches — keep typing to narrow")
        self._resize_to_content()
        if self._n_actionable:
            # Keep a live selection so Enter always activates the top match.
            self._lst.setCurrentRow(0)

    def _add_info_row(self, text: str) -> None:
        item = QListWidgetItem(text)
        item.setData(ROLE_KIND, "Info")
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled & ~Qt.ItemFlag.ItemIsSelectable)
        self._lst.addItem(item)

    def _move(self, key: int) -> None:
        if not self._n_actionable:
            return
        cur = self._lst.currentRow()
        if key == Qt.Key.Key_Down:
            nxt = cur + 1
        elif key == Qt.Key.Key_Up:
            nxt = cur - 1
        elif key == Qt.Key.Key_PageDown:
            nxt = cur + MAX_VISIBLE_ROWS
        elif key == Qt.Key.Key_PageUp:
            nxt = cur - MAX_VISIBLE_ROWS
        elif key == Qt.Key.Key_Home:
            nxt = 0
        elif key == Qt.Key.Key_End:
            nxt = self._n_actionable - 1
        else:
            return
        self._lst.setCurrentRow(max(0, min(nxt, self._n_actionable - 1)))

    def _activate(self) -> None:
        it = self._lst.currentItem()
        if it:
            act = it.data(ROLE_ACTION)
            if callable(act):
                act()
                self.close_palette()

    def close_palette(self) -> None:
        self.closed.emit()
        self.hide()

    def changeEvent(self, e) -> None:  # noqa: N802  # Qt virtual override
        # Click-away dismissal: clicking the page (or another window) drops
        # our activation — a floating palette that never goes away reads as
        # broken, so close like every other command palette does.
        if e.type() == QEvent.Type.WindowDeactivate and self.isVisible():
            self.close_palette()
        super().changeEvent(e)

    def eventFilter(self, obj, e) -> bool:  # noqa: N802  # Qt virtual override
        if obj in (self._e, self._lst):
            if e.type() == QEvent.Type.ShortcutOverride and e.key() == Qt.Key.Key_Escape:
                # Starve the main window's Esc shortcut; we close on KeyPress.
                e.accept()
                return True
            if e.type() == QEvent.Type.KeyPress:
                if e.key() == Qt.Key.Key_Escape:
                    self.close_palette()
                    return True
                if obj is self._e:
                    if e.key() == Qt.Key.Key_Tab:
                        self._move(Qt.Key.Key_Down)
                        return True
                    if e.key() == Qt.Key.Key_Backtab:
                        self._move(Qt.Key.Key_Up)
                        return True
                    if e.key() in (
                        Qt.Key.Key_Down,
                        Qt.Key.Key_Up,
                        Qt.Key.Key_PageDown,
                        Qt.Key.Key_PageUp,
                        Qt.Key.Key_Home,
                        Qt.Key.Key_End,
                    ):
                        self._move(e.key())
                        return True
        return super().eventFilter(obj, e)

    def keyPressEvent(self, e) -> None:  # noqa: N802  # Qt virtual override
        if e.key() == Qt.Key.Key_Escape:
            self.close_palette()
            return
        if e.key() in (
            Qt.Key.Key_Down,
            Qt.Key.Key_Up,
            Qt.Key.Key_PageDown,
            Qt.Key.Key_PageUp,
            Qt.Key.Key_Home,
            Qt.Key.Key_End,
        ):
            self._move(e.key())
            return
        super().keyPressEvent(e)
