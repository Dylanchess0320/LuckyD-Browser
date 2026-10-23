"""The bottom status strip: text-only, rebuilt slim (2026-10-22).

Dylan's call, twice refined: the old QStatusBar carried three widgets he
didn't want (lock/site-permissions button, zoom pill, chunky load-progress
bar) — gone. What he actually uses the bar for is seeing the URL of the
link he's hovering, so the bar stays as a polished text strip:

- Hover URLs render rich: scheme dimmed, DOMAIN in the theme accent,
  the rest in the normal text color (see format_hover_html).
- Plain status text (anything that would have called
  QStatusBar.showMessage) lands on the same label, escaped, so every
  existing writer keeps working unchanged.
- Load progress is a thin 2px line along the bar's TOP edge, painted in
  the theme accent, visible only while a page is loading.

All colors come from the active theme palette — nothing hardcoded.
"""

from __future__ import annotations

import html

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QLabel, QStatusBar

from .theme import palette as theme_palette

PROGRESS_LINE_PX = 2


def format_hover_html(text: str, pal: dict[str, str]) -> str:
    """Rich-text form of a hovered URL: dim scheme, accent domain.

    Non-URL text (and empty text) is returned HTML-escaped, unchanged in
    meaning — the label is a rich-text label, so raw text must never be
    interpreted as markup.
    """
    t = (text or "").strip()
    if "://" not in t:
        return html.escape(t)
    scheme, rest = t.split("://", 1)
    host_end = len(rest)
    for sep in ("/", "?", "#"):
        idx = rest.find(sep)
        if idx != -1:
            host_end = min(host_end, idx)
    host, tail = rest[:host_end], rest[host_end:]
    if not host:
        return html.escape(t)
    return (
        f'<span style="color:{pal["muted"]}">{html.escape(scheme)}://</span>'
        f'<span style="color:{pal["accent"]};font-weight:600">{html.escape(host)}</span>'
        f"{html.escape(tail)}"
    )


class StatusBar(QStatusBar):
    """A QStatusBar whose only child is a text label + a painted
    top-edge progress line. No buttons, no pills, no progress widget."""

    def __init__(self, settings, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._label = QLabel("", self)
        self._label.setObjectName("status_text")
        self._label.setTextFormat(Qt.TextFormat.RichText)
        self._label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.addWidget(self._label, 1)
        self._progress: float | None = None
        self._clear_timer = QTimer(self)
        self._clear_timer.setSingleShot(True)
        self._clear_timer.timeout.connect(self._label.clear)

    # ── text display (the QStatusBar message API, rerouted) ────────

    def showMessage(self, text: str, timeout: int = 0) -> None:  # noqa: N802 — Qt slot name
        """Plain status text on the strip (escaped; never markup)."""
        self._label.setText(html.escape(text or ""))
        self._arm_clear_timer(timeout)

    def clearMessage(self) -> None:  # noqa: N802 — Qt slot name
        self._clear_timer.stop()
        self._label.clear()

    def show_hover_url(self, text: str) -> None:
        """A hovered link URL with the domain emphasized."""
        self._clear_timer.stop()
        self._label.setText(format_hover_html(text, theme_palette(self._settings)))

    def current_text(self) -> str:
        return self._label.text()

    def _arm_clear_timer(self, timeout: int) -> None:
        self._clear_timer.stop()
        if timeout and timeout > 0:
            self._clear_timer.start(timeout)

    # ── load progress: the thin top-edge line ───────────────────────

    def set_load_progress(self, frac: float | None) -> None:
        """frac in [0, 1) shows the line; None (or >= 1) hides it."""
        new = None if frac is None or frac >= 1 else max(0.0, float(frac))
        if new != self._progress:
            self._progress = new
            self.update()

    def load_progress(self) -> float | None:
        return self._progress

    def paintEvent(self, event) -> None:  # noqa: N802 — Qt override
        super().paintEvent(event)
        if self._progress is None:
            return
        pal = theme_palette(self._settings)
        painter = QPainter(self)
        painter.fillRect(
            0,
            0,
            int(self.width() * self._progress),
            PROGRESS_LINE_PX,
            QColor(pal["accent"]),
        )
        painter.end()
