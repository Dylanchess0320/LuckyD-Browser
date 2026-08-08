"""Vertical tab strip — an optional left-side dock with big readable rows.

Mirrors the horizontal tab bar: click to switch, middle-click to close,
right-click for the full tab menu, drag rows to reorder, New Tab button up
top. Grouped tabs get their group's color as a left edge; the active row
glows with the theme accent. Refreshed on every tab change by MainWindow.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDockWidget,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .icons import letter_tile


class VerticalTabsDock(QDockWidget):
    def __init__(self, main_window):
        super().__init__("Tabs", main_window)
        self._mw = main_window
        self.setObjectName("vertical_tabs_dock")
        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )

        body = QWidget(self)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        new_btn = QToolButton(body)
        new_btn.setText("＋ New Tab")
        new_btn.setAutoRaise(True)
        new_btn.setToolTip("New Tab (Ctrl+T)")
        new_btn.clicked.connect(lambda: self._mw.new_tab())
        row = QHBoxLayout()
        row.addWidget(new_btn)
        row.addStretch(1)
        layout.addLayout(row)

        self.list = QListWidget(body)
        self.list.setObjectName("vtabs_list")
        self.list.setIconSize(QSize(18, 18))
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.currentRowChanged.connect(self._row_activated)
        self.list.model().rowsMoved.connect(self._rows_moved)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        layout.addWidget(self.list)

        self.setWidget(body)
        self._syncing = False

    def refresh(self) -> None:
        """Rebuild the rows from the tab widget (cheap; called on changes)."""
        tabs = self._mw.tabs
        self._syncing = True
        try:
            if self.list.count() != tabs.count():
                self.list.clear()
                for _ in range(tabs.count()):
                    self.list.addItem(QListWidgetItem())
            for i in range(tabs.count()):
                view = tabs.widget(i)
                item = self.list.item(i)
                if view is None:
                    continue
                title = tabs.tabText(i) or view.title() or "New Tab"
                item.setText(title)
                icon = view.icon()
                item.setIcon(
                    letter_tile(view.url().toString(), 18) if icon.isNull() else QIcon(icon)
                )
                gid = tabs.group_of(i)
                info = tabs.group_info(gid) if gid else None
                if info is not None:
                    item.setForeground(QColor(info["color"]))
                    item.setToolTip(f"[{info['name']}] {view.url().toString()}")
                else:
                    item.setForeground(QColor())  # theme default
                    item.setToolTip(view.url().toString())
            self.list.setCurrentRow(tabs.currentIndex())
        finally:
            self._syncing = False

    # ── interactions ──────────────────────────────────────────────────

    def _row_activated(self, row: int) -> None:
        if not self._syncing and 0 <= row < self._mw.tabs.count():
            self._mw.tabs.setCurrentIndex(row)

    def _rows_moved(self, _parent, start, _end, _dest, row) -> None:
        if self._syncing:
            return
        tabs = self._mw.tabs
        if 0 <= start < tabs.count():
            tabs.tabBar().moveTab(start, row)
        self._syncing = True
        try:
            self.refresh()
        finally:
            self._syncing = False

    def _context_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is not None:
            self._mw.tabs.show_tab_menu(self.list.row(item), self.list.viewport().mapToGlobal(pos))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.MiddleButton:
            item = self.list.itemAt(self.list.mapFromGlobal(event.globalPosition().toPoint()))
            if item is not None:
                self._mw.tabs.close_tab(self.list.row(item))
                event.accept()
                return
        super().mouseReleaseEvent(event)
