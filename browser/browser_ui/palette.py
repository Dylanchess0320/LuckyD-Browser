"""Command palette: Ctrl+K for fuzzy search over tabs, bookmarks, history, actions."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

_SCORE = 60


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


class CommandPalette(QWidget):
    closed = Signal()

    def __init__(self, mw: QWidget):
        super().__init__(mw, Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._mw = mw
        self.setWindowOpacity(0.95)

        cont = QWidget(self)
        cont.setStyleSheet(
            """QWidget{background:rgba(16,21,31,235);border:1px solid rgba(255,255,255,30);border-radius:14px;padding:16px;}
            QLineEdit{background:#1a2132;border:1px solid #232c42;border-radius:10px;padding:8px 12px;}
            QListWidget{background:transparent;border:none;}
            QListWidget::item{padding:8px 14px;border-radius:8px;}
            QListWidget::item:selected{background:rgba(91,157,255,0.35);}"""
        )
        lay = QVBoxLayout(cont)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self._e = QLineEdit(cont)
        self._e.setPlaceholderText("Type… (Esc close)")
        self._e.returnPressed.connect(self._activate)
        self._e.textChanged.connect(self._filter)
        self._e.setFocus()

        self._lst = QListWidget(cont)
        self._lst.itemDoubleClicked.connect(self._activate)
        lay.addWidget(self._e)
        lay.addWidget(self._lst)

        self._items: list[tuple[str, str, QIcon | None, Callable]] = []
        self._load_items()
        self.hide()

    def _load_items(self) -> None:
        t = self._mw.tabs
        for i in range(t.count()):
            v = t.widget(i)
            if v:
                u, tt = v.url().toString(), v.title() or "New Tab"
                self._items.append(
                    (
                        f"tab:{i+1}",
                        f"{tt} — {u[:50]}",
                        v.icon(),
                        lambda vv=v: self._mw.open_in_new_tab(vv.url()),
                    )
                )
        for u, t, *_ in self._mw.storage.bookmarks()[:30]:
            self._items.append(
                ("bookmark", t or u, None, lambda uu=u: self._mw.open_in_new_tab(uu))
            )
        for u, t, *_ in self._mw.storage.recent(100):
            self._items.append(("history", t or u, None, lambda uu=u: self._mw.open_in_new_tab(uu)))
        for L, A in [
            ("New Tab", self._mw.new_tab),
            ("AI Assistant", self._mw.show_assistant),
            ("Coding Agent", self._mw.open_hq),
            ("Bookmarks", self._mw.open_bookmarks),
            ("History", self._mw.open_history),
            ("Settings", self._mw.open_settings),
        ]:
            self._items.append((L.lower(), L, None, A))

    def show_palette(self) -> None:
        self._e.clear()
        self._items = []
        self._load_items()  # reload fresh data
        self._filter("")
        # Don't clear + re-add items — _filter does that
        self._position()
        self.show()
        self.raise_()
        self._e.setFocus()
        for kw, lbl, ico, act in self._items:
            it = QListWidgetItem(lbl)
            it.setData(Qt.ItemDataRole.UserRole, act)
            if ico:
                it.setIcon(ico)
            self._lst.addItem(it)

    def _position(self) -> None:
        pw, ph = self.width(), self.height()
        mw, mh = self._mw.width(), self._mw.height()
        self.move(
            max(self._mw.x() + (mw - pw) // 2, self._mw.x() + 20),
            max(self._mw.y() + (mh - ph) // 2, self._mw.y() + 20),
        )

    def _filter(self, text: str) -> None:
        q = text.lower()
        self._lst.clear()
        for kw, lbl, ico, act in self._items:
            if q in lbl.lower():
                it = QListWidgetItem(lbl)
                it.setData(Qt.ItemDataRole.UserRole, act)
                if ico:
                    it.setIcon(ico)
                self._lst.addItem(it)

    def _activate(self) -> None:
        it = self._lst.currentItem()
        if it:
            act = it.data(Qt.ItemDataRole.UserRole)
            if callable(act):
                act()
        self.close_palette()

    def close_palette(self) -> None:
        self.closed.emit()
        self.hide()

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.close_palette()
            return
        if e.key() == Qt.Key.Key_Down:
            self._lst.setCurrentRow(min(self._lst.currentRow() + 1, self._lst.count() - 1))
            return
        if e.key() == Qt.Key.Key_Up:
            self._lst.setCurrentRow(max(self._lst.currentRow() - 1, 0))
            return
        super().keyPressEvent(e)
