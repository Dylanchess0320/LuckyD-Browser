"""Glass toast notifications: slide in bottom-right, auto-dismiss, stacked.

Usage:  mw.toasts.show("Bookmark added")  — or the MainWindow.toast() helper.
Kinds: info (accent edge), ok (green), warn (amber), error (red). A toast is
a frameless translucent card parented to the main window so it floats above
the web content without stealing focus.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    Qt,
    QTimer,
)
from PySide6.QtWidgets import QGraphicsOpacityEffect, QHBoxLayout, QLabel, QWidget

from .theme import palette as _theme_palette

_KIND_ICON = {
    "info": "◆",
    "ok": "✔",
    "warn": "⚠",
    "error": "✕",
}

# Semantic warn tint — readable on all five themes.
_WARN = "#fbbf24"


def _edge_color(kind: str, p: dict) -> str:
    return {"info": p["accent"], "ok": p["ok"], "error": p["danger"]}.get(kind, _WARN)


class _Toast(QWidget):
    WIDTH = 320
    HEIGHT = 52

    def __init__(self, parent_window, message: str, kind: str = "info"):
        super().__init__(
            parent_window,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._card = QWidget(self)
        self._apply_style(kind)
        row = QHBoxLayout(self._card)
        row.setContentsMargins(12, 8, 14, 8)
        dot = QLabel(_KIND_ICON.get(kind, _KIND_ICON["info"]), self._card)
        dot.setObjectName("icon")
        row.addWidget(dot)
        text = QLabel(message, self._card)
        text.setWordWrap(True)
        row.addWidget(text, 1)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._card)
        self.setFixedWidth(self.WIDTH)
        self.adjustSize()
        self.setFixedHeight(max(self.HEIGHT, self.sizeHint().height()))

        self._opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)
        self._opacity.setOpacity(0.0)

    def _apply_style(self, kind: str) -> None:
        """Theme-synced card styling, rebuilt from the active palette."""
        p = _theme_palette(getattr(self.parent(), "settings", None))
        edge = _edge_color(kind, p)
        self._card.setStyleSheet(
            f"""
            QWidget {{
                background: {p["panel"]};
                border: 1px solid {p["border"]};
                border-left: 3px solid {edge};
                border-radius: 12px;
            }}
            QLabel {{ background: transparent; border: none; color: {p["text"]}; }}
            QLabel#icon {{ color: {edge}; font-size: 15px; }}
            """
        )


class ToastManager:
    """Owns a window's toast stack: positioning + lifecycle."""

    MARGIN = 16
    GAP = 8
    SLIDE_MS = 220
    HOLD_MS = 3200

    def __init__(self, window):
        self._win = window
        self._items: list[_Toast] = []

    def set_accent(self, accent: str) -> None:
        """Deprecated no-op: the info edge now follows the active theme."""
        del accent

    def show(self, message: str, kind: str = "info") -> None:
        toast = _Toast(self._win, message, kind)
        self._items.append(toast)
        self._layout()
        toast.show()
        self._animate_in(toast)
        QTimer.singleShot(self.HOLD_MS, lambda t=toast: self._animate_out(t))

    # ── internals ────────────────────────────────────────────────────
    def _layout(self) -> None:
        x = self._win.width() - _Toast.WIDTH - self.MARGIN
        y = self._win.height() - self.MARGIN
        for toast in reversed(self._items):
            y -= toast.height()
            toast.move(QPoint(x, y))
            y -= self.GAP

    def _animate_in(self, toast: _Toast) -> None:
        fade = QPropertyAnimation(toast._opacity, b"opacity", toast)
        fade.setDuration(self.SLIDE_MS)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.start()
        toast._fade = fade  # keep alive

    def _animate_out(self, toast: _Toast) -> None:
        if toast not in self._items:
            return
        fade = QPropertyAnimation(toast._opacity, b"opacity", toast)
        fade.setDuration(self.SLIDE_MS)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.Type.InCubic)
        fade.finished.connect(lambda: self._drop(toast))
        fade.start()
        toast._fade = fade

    def _drop(self, toast: _Toast) -> None:
        if toast in self._items:
            self._items.remove(toast)
        toast.hide()
        toast.deleteLater()
        self._layout()

    def relayout(self) -> None:
        """Re-anchor on window resize."""
        self._layout()
