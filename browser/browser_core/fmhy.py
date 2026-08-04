"""Searchable local index of FMHY (freemediaheckyeah) — official open data.

Syncs the FMHY GitHub wiki markdown (thousands of curated free tools/sites)
into a local JSON cache, then answers "find me a free X" instantly, offline.
No API key required. Cache auto-refreshes weekly.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import httpx

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE_PATH = DATA_DIR / "fmhy.json"
CACHE_TTL = 7 * 24 * 3600  # seconds (weekly refresh)

_WIKI_RAW = "https://raw.githubusercontent.com/wiki/fmhy/FMHY/{page}.md"
_PAGES = {
    "AI": "Artificial-Intelligence",
    "Privacy": "Adblock",
    "Streaming": "Streaming",
    "Music": "Music",
    "Gaming": "Gaming",
    "Reading": "Reading",
    "Downloading": "Downloading",
    "Torrenting": "Torrenting",
    "Educational": "Educational",
    "Mobile": "Mobile",
    "Linux": "Linux",
    "Misc": "Misc",
}

_LINK_RE = re.compile(r"\*\s*\[([^\]]+)\]\((https?://[^)\s]+)\)[^-\n]*?(?:-\s*(.*))?$")
_SECTION_RE = re.compile(r"^#{2,4}\s+(.+?)\s*$")


def parse_markdown(text: str, category: str) -> list[dict]:
    """Extract (name, url, description, section) entries from FMHY markdown."""
    entries: list[dict] = []
    section = ""
    for line in text.splitlines():
        stripped = line.strip()
        header = _SECTION_RE.match(stripped)
        if header:
            section = header.group(1).strip("* ").strip()
            continue
        match = _LINK_RE.search(stripped)
        if match:
            name = match.group(1).replace("**", "").strip()
            url = match.group(2)
            desc = (match.group(3) or "").strip()
            desc = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", desc).strip()
            entries.append(
                {
                    "name": name,
                    "url": url,
                    "desc": desc,
                    "category": category,
                    "section": section,
                }
            )
    return entries


class FmhyIndex:
    def __init__(self, cache_path: Path = CACHE_PATH):
        self._path = cache_path
        self._entries: list[dict] = []
        self._fetched_at = 0.0
        self._load_cache()

    def _load_cache(self) -> None:
        try:
            if self._path.exists():
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._entries = data.get("entries", [])
                self._fetched_at = float(data.get("fetched_at", 0))
        except Exception:
            self._entries = []
            self._fetched_at = 0.0

    @property
    def is_stale(self) -> bool:
        return time.time() - self._fetched_at > CACHE_TTL

    @property
    def size(self) -> int:
        return len(self._entries)

    def sync(self) -> int:
        """Fetch + parse all wiki pages. Returns total entries indexed."""
        entries: list[dict] = []
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            for category, page in _PAGES.items():
                try:
                    resp = client.get(_WIKI_RAW.format(page=page))
                    if resp.status_code == 200:
                        entries.extend(parse_markdown(resp.text, category))
                except Exception:
                    continue  # offline / page renamed — skip gracefully
        if entries:
            self._entries = entries
            self._fetched_at = time.time()
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"fetched_at": self._fetched_at, "entries": entries}),
                encoding="utf-8",
            )
        return len(self._entries)

    def ensure_fresh(self) -> None:
        if not self._entries or self.is_stale:
            self.sync()

    def search(self, query: str, limit: int = 8) -> list[dict]:
        terms = [t for t in query.lower().split() if len(t) > 1]
        if not terms:
            return []
        scored: list[tuple[int, dict]] = []
        for entry in self._entries:
            hay = (
                f"{entry['name']} {entry['desc']} " f"{entry['section']} {entry['category']}"
            ).lower()
            score = 0
            for term in terms:
                if term in hay:
                    score += 3 if term in entry["name"].lower() else 1
            if score >= len(terms):  # every term must match something
                scored.append((score, entry))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [entry for _score, entry in scored[:limit]]
