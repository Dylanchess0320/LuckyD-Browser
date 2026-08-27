"""Address bar: URL detection, search-engine fallback, history completions."""

from __future__ import annotations

import re

from PySide6.QtCore import QStringListModel, Qt, QUrl, Signal
from PySide6.QtWidgets import QCompleter, QLineEdit

_DOMAIN_RE = re.compile(
    r"^(localhost|127\.0\.0\.1|0\.0\.0\.0|[\w-]+(\.[\w-]+)+)(:\d+)?(/.*)?(\?.*)?$",
    re.IGNORECASE,
)


class Omnibox(QLineEdit):
    """Single input for both URLs and web searches (like Chrome's omnibox)."""

    navigate = Signal(QUrl)
    ask = Signal(str)  # "? question" prefix → AI assistant

    def __init__(self, settings, storage, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._storage = storage
        self.setPlaceholderText("Search Google or type a URL  •  ?ask AI about this page  •  Ctrl+K commands")
        self.setClearButtonEnabled(True)

        self._model = QStringListModel(self)
        completer = QCompleter(self._model, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.setCompleter(completer)

        self.returnPressed.connect(self._submit)

    def _submit(self) -> None:
        text = self.text().strip()
        if text.startswith("?") and len(text) > 1:
            self.ask.emit(text[1:].strip())
            return
        url = self.to_url(text)
        if url is not None and url.isValid():
            self.navigate.emit(url)

    def to_url(self, text: str) -> QUrl | None:
        """Decide whether input is a URL to load or a query to search."""
        text = text.strip()
        if not text:
            return None
        if " " not in text:
            lowered = text.lower()
            if lowered.startswith(("http://", "https://", "file://", "about:", "view-source:")):
                return QUrl(text)
            if _DOMAIN_RE.match(text):
                return QUrl("https://" + text)
        return QUrl(self._settings.search_url_for(text))

    def refresh_completions(self) -> None:
        """Rebuild completion candidates from recent history."""
        seen: set[str] = set()
        items: list[str] = []
        for url, title, _ts in self._storage.recent(300):
            for candidate in (url, title):
                if candidate and candidate not in seen:
                    seen.add(candidate)
                    items.append(candidate)
        self._model.setStringList(items)
