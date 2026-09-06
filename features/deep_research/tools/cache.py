"""SQLite cache for fetched documents, extracted passages, and search results.

Cuts cost and latency on repeated runs: the same URL is fetched and read at
most once per TTL window. The cache is keyed on a *canonical* URL (scheme +
host + path, lowercased, query/fragment stripped) so query-string variants
don't sneak past the dedupe.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

try:
    import tldextract  # optional; only used for domain helpers
except Exception:
    tldextract = None  # type: ignore[assignment]

from ..config import settings


def canonicalize_url(url: str) -> str:
    """Normalize a URL to a stable cache key.

    Lowercases scheme + host only (paths may be case-sensitive). Strips
    tracking query params (utm_*, fbclid, gclid, ...) and fragments, but
    preserves meaningful query params like ?id=123.
    """
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    u = (url or "").strip()
    if not u:
        return ""
    parts = urlsplit(u)
    scheme = (parts.scheme or "https").lower()
    if scheme == "http":
        scheme = "https"
    netloc = parts.netloc.lower()
    # drop www. for dedupe
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parts.path.rstrip("/")
    # strip only tracking params
    tracking = {"fbclid", "gclid", "mc_cid", "mc_eid", "_ga"}
    q = [
        (k, v)
        for k, v in parse_qsl(parts.query)
        if not k.lower().startswith("utm_") and k.lower() not in tracking
    ]
    query = urlencode(q)
    return urlunsplit((scheme, netloc, path, query, ""))


class Cache:
    def __init__(
        self, path: str | None = None, enabled: bool | None = None, ttl_days: int | None = None
    ) -> None:
        self.enabled = settings.cache_enabled if enabled is None else enabled
        self.ttl_days = settings.cache_ttl_days if ttl_days is None else ttl_days
        self.path = Path(path or settings.cache_dir)
        if not self.enabled:
            return
        self.path.mkdir(parents=True, exist_ok=True)
        self._db = self.path / "cache.sqlite"
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db))
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS documents (
                    url_key TEXT PRIMARY KEY,
                    url TEXT,
                    title TEXT,
                    text TEXT,
                    fetch_ok INTEGER,
                    fetched_at REAL
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS search_results (
                    query TEXT,
                    backend TEXT,
                    result_json TEXT,
                    fetched_at REAL,
                    PRIMARY KEY (query, backend)
                )"""
            )

    def _expired(self, fetched_at: float) -> bool:
        if self.ttl_days <= 0:
            return False
        return (time.time() - fetched_at) > self.ttl_days * 86400

    # -- documents ---------------------------------------------------------
    def get_document(self, url: str) -> tuple[str, str, str, bool] | None:
        """Return (title, text, url, fetch_ok) if cached and fresh, else None."""
        if not self.enabled:
            return None
        key = canonicalize_url(url)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT title, text, url, fetch_ok, fetched_at FROM documents WHERE url_key=?",
                (key,),
            ).fetchone()
        if row is None or self._expired(row["fetched_at"]):
            return None
        return row["title"], row["text"], row["url"], bool(row["fetch_ok"])

    def put_document(self, url: str, title: str, text: str, fetch_ok: bool) -> None:
        if not self.enabled:
            return
        key = canonicalize_url(url)
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO documents
                   (url_key, url, title, text, fetch_ok, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (key, url, title, text, int(fetch_ok), time.time()),
            )

    # -- search results ----------------------------------------------------
    def get_search(self, query: str, backend: str) -> str | None:
        if not self.enabled:
            return None
        with self._conn() as conn:
            row = conn.execute(
                "SELECT result_json, fetched_at FROM search_results WHERE query=? AND backend=?",
                (query, backend),
            ).fetchone()
        if row is None or self._expired(row["fetched_at"]):
            return None
        return row["result_json"]

    def put_search(self, query: str, backend: str, result_json: str) -> None:
        if not self.enabled:
            return
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO search_results
                   (query, backend, result_json, fetched_at) VALUES (?, ?, ?, ?)""",
                (query, backend, result_json, time.time()),
            )

    # -- maintenance -------------------------------------------------------
    def stats(self) -> dict[str, int]:
        if not self.enabled:
            return {"enabled": 0, "documents": 0, "search_results": 0}
        with self._conn() as conn:
            docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            sr = conn.execute("SELECT COUNT(*) FROM search_results").fetchone()[0]
        return {"enabled": 1, "documents": docs, "search_results": sr}

    def clear(self) -> None:
        if not self.enabled:
            return
        with self._conn() as conn:
            conn.execute("DELETE FROM documents")
            conn.execute("DELETE FROM search_results")


_cache: Cache | None = None
_lock = threading.Lock()


def get_cache() -> Cache:
    global _cache
    with _lock:
        if _cache is None:
            _cache = Cache()
        return _cache


def reset_cache() -> Cache:
    """Rebuild the cache singleton (picks up settings changes like --no-cache)."""
    global _cache
    with _lock:
        _cache = Cache()
        return _cache
