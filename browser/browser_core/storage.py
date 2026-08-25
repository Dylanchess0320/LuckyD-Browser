"""SQLite-backed browsing history and bookmarks storage."""

from __future__ import annotations

import os
import sqlite3
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

if getattr(sys, "frozen", False):
    # Packaged build: per-user data dir (writable even under Program Files,
    # survives reinstalls — same model as Chrome/VS Code).
    DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "LuckyDBrowser"
else:
    DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "browser.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    visit_time REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_time ON history(visit_time DESC);
CREATE INDEX IF NOT EXISTS idx_history_url ON history(url);

CREATE TABLE IF NOT EXISTS bookmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL DEFAULT '',
    folder TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL
);
"""


class _BookmarkHTMLParser(HTMLParser):
    """Parses the Netscape bookmark format used by Chrome/Edge/Firefox exports."""

    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href:
            self.links.append((self._href, "".join(self._text).strip()))
            self._href = None


class Storage:
    def __init__(self, db_path: Path = DB_PATH):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ── history ──────────────────────────────────────────────────────

    def add_visit(self, url: str, title: str) -> None:
        self._conn.execute(
            "INSERT INTO history (url, title, visit_time) VALUES (?, ?, ?)",
            (url, title or "", time.time()),
        )
        self._conn.commit()

    def recent(self, limit: int = 500) -> list[tuple[str, str, float]]:
        cur = self._conn.execute(
            "SELECT url, title, visit_time FROM history ORDER BY visit_time DESC LIMIT ?",
            (limit,),
        )
        return cur.fetchall()

    def search_history(self, query: str, limit: int = 500) -> list[tuple[str, str, float]]:
        like = f"%{query}%"
        cur = self._conn.execute(
            "SELECT url, title, visit_time FROM history "
            "WHERE url LIKE ? OR title LIKE ? ORDER BY visit_time DESC LIMIT ?",
            (like, like, limit),
        )
        return cur.fetchall()

    def clear_history(self) -> None:
        self._conn.execute("DELETE FROM history")
        self._conn.commit()

    def delete_history_entry(self, url: str) -> None:
        """Delete a single history entry by URL."""
        self._conn.execute("DELETE FROM history WHERE url = ?", (url,))
        self._conn.commit()

    # ── bookmarks ────────────────────────────────────────────────────

    def add_bookmark(self, url: str, title: str, folder: str = "") -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO bookmarks (url, title, folder, created) VALUES (?, ?, ?, ?)",
            (url, title or "", folder, time.time()),
        )
        self._conn.commit()

    def remove_bookmark(self, url: str) -> None:
        self._conn.execute("DELETE FROM bookmarks WHERE url = ?", (url,))
        self._conn.commit()

    def is_bookmarked(self, url: str) -> bool:
        cur = self._conn.execute("SELECT 1 FROM bookmarks WHERE url = ?", (url,))
        return cur.fetchone() is not None

    def bookmarks(self) -> list[tuple[str, str, str, float]]:
        cur = self._conn.execute(
            "SELECT url, title, folder, created FROM bookmarks ORDER BY created DESC"
        )
        return cur.fetchall()

    def import_from_html(self, path: str | Path) -> int:
        """Import a Chrome/Edge/Firefox bookmarks export. Returns count imported."""
        parser = _BookmarkHTMLParser()
        parser.feed(Path(path).read_text(encoding="utf-8", errors="ignore"))
        count = 0
        for href, title in parser.links:
            if href.startswith(("http://", "https://")):
                self.add_bookmark(href, title or href)
                count += 1
        return count
