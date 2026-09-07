"""Address bar: URL detection, search-engine fallback, history completions."""

from __future__ import annotations

import re

from PySide6.QtCore import QStringListModel, Qt, QTimer, QUrl, Signal
from PySide6.QtWidgets import QCompleter, QLineEdit

_DOMAIN_RE = re.compile(
    r"^(localhost|127\.0\.0\.1|0\.0\.0\.0|[\w-]+(\.[\w-]+)+)(:\d+)?(/.*)?(\?.*)?$",
    re.IGNORECASE,
)

_LOCALHOST_RE = re.compile(
    r"^(localhost|127\.0\.0\.1|0\.0\.0\.0|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)(:\d+)?(/.*)?(\?.*)?$",
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
        self.setPlaceholderText(
            "Search Google or type a URL  •  ?ask AI about this page  •  Ctrl+K commands"
        )
        self.setClearButtonEnabled(True)

        self._label_url: dict[str, str] = {}
        self._completing = False

        self._model = QStringListModel(self)
        completer = QCompleter(self._model, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.activated[str].connect(self._on_completion)
        self.setCompleter(completer)

        self.returnPressed.connect(self._submit)

    def _on_completion(self, label: str) -> None:
        """Navigate directly when a suggestion is selected from the dropdown."""
        url_str = self._label_url.get(label)
        if url_str:
            self._completing = True
            self.navigate.emit(QUrl(url_str))
            QTimer.singleShot(50, self._clear_completing)

    def _clear_completing(self) -> None:
        self._completing = False
        self.clear()

    def _submit(self) -> None:
        if self._completing:
            return
        text = self.text().strip()
        if text.startswith("?") and len(text) > 1:
            self.ask.emit(text[1:].strip())
            return
        url_str = self._label_url.get(text)
        if url_str:
            self.navigate.emit(QUrl(url_str))
            self.clear()
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
                if _LOCALHOST_RE.match(text):
                    return QUrl("http://" + text)
                return QUrl("https://" + text)
        return QUrl(self._settings.search_url_for(text))

    def refresh_completions(self) -> None:
        """Rebuild completion candidates from recent history and bookmarks."""
        self._label_url.clear()
        seen: set[str] = set()
        labels: list[str] = []
        for url, title, _ts in self._storage.recent(300):
            label = title if title and title != url else url
            if label and label not in seen:
                seen.add(label)
                labels.append(label)
                self._label_url[label] = url
        for url, title, *_ in self._storage.bookmarks()[:50]:
            label = title if title and title != url else url
            if label and label not in seen:
                seen.add(label)
                labels.append(label)
                self._label_url[label] = url
        self._model.setStringList(labels)
