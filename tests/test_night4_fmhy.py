"""Night-4 browser-core audit: fmhy.py (FMHY index) round 1.

No dedicated tests existed. Covers parse_markdown's section/link
extraction, the FmhyIndex cache lifecycle (missing/corrupt/fresh/stale),
the search scoring contract, and sync() with a stubbed httpx.Client —
no network.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

for _mod in (
    "PySide6",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

import browser_core.fmhy as fmhy
from browser_core.fmhy import FmhyIndex, parse_markdown

_SAMPLE = """# Title (h1 is not a section)

## **Streaming Sites**
* [**FlixHQ**](https://flixhq.to/) - Watch movies and shows
* [Plain](https://plain.example/) - no bold, [see guide](https://g.example/) inside desc

### Subsection
* [**MusicTool**](https://music.example/) - free **music** downloads

not a link line
* [NoDesc](https://nodesc.example/)
"""


# ── parse_markdown ─────────────────────────────────────────────────


def test_parse_markdown_sections_and_entries() -> None:
    entries = parse_markdown(_SAMPLE, "Streaming")
    by_name = {e["name"]: e for e in entries}
    assert set(by_name) == {"FlixHQ", "Plain", "MusicTool", "NoDesc"}
    assert by_name["FlixHQ"]["url"] == "https://flixhq.to/"
    assert by_name["FlixHQ"]["desc"] == "Watch movies and shows"
    assert by_name["FlixHQ"]["section"] == "Streaming Sites"  # ** stripped
    assert by_name["FlixHQ"]["category"] == "Streaming"
    assert by_name["MusicTool"]["section"] == "Subsection"


def test_parse_markdown_bold_stripped_from_name() -> None:
    entries = parse_markdown(_SAMPLE, "Streaming")
    assert all("**" not in e["name"] for e in entries)


def test_parse_markdown_desc_links_flattened() -> None:
    entries = parse_markdown(_SAMPLE, "Streaming")
    plain = next(e for e in entries if e["name"] == "Plain")
    assert plain["desc"] == "no bold, see guide inside desc"


def test_parse_markdown_entry_without_desc() -> None:
    entries = parse_markdown(_SAMPLE, "Streaming")
    nodesc = next(e for e in entries if e["name"] == "NoDesc")
    assert nodesc["desc"] == ""


def test_parse_markdown_ignores_non_links() -> None:
    assert parse_markdown("just text\n# header\n", "Misc") == []


def test_parse_markdown_empty() -> None:
    assert parse_markdown("", "Misc") == []


# ── cache lifecycle ────────────────────────────────────────────────


def test_index_missing_cache_is_empty(tmp_path) -> None:
    idx = FmhyIndex(tmp_path / "fmhy.json")
    assert idx.size == 0
    assert idx.is_stale is True  # fetched_at=0 → older than TTL
    assert idx.search("anything") == []


def test_index_corrupt_cache_is_empty(tmp_path) -> None:
    p = tmp_path / "fmhy.json"
    p.write_text("{not json", encoding="utf-8")
    idx = FmhyIndex(p)
    assert idx.size == 0 and idx._fetched_at == 0.0


def test_index_loads_cache(tmp_path) -> None:
    p = tmp_path / "fmhy.json"
    entries = [{"name": "A", "url": "https://a/", "desc": "d", "category": "C", "section": "S"}]
    p.write_text(json.dumps({"fetched_at": time.time(), "entries": entries}), encoding="utf-8")
    idx = FmhyIndex(p)
    assert idx.size == 1
    assert idx.is_stale is False


def test_index_stale_after_ttl(tmp_path) -> None:
    p = tmp_path / "fmhy.json"
    p.write_text(
        json.dumps({"fetched_at": time.time() - fmhy.CACHE_TTL - 1, "entries": []}),
        encoding="utf-8",
    )
    assert FmhyIndex(p).is_stale is True


# ── search ─────────────────────────────────────────────────────────


def _idx_with(entries, tmp_path):
    idx = FmhyIndex(tmp_path / "fmhy.json")
    idx._entries = entries
    idx._fetched_at = time.time()
    return idx


_ENTRIES = [
    {
        "name": "FlixHQ",
        "url": "u1",
        "desc": "watch movies",
        "category": "Streaming",
        "section": "Sites",
    },
    {
        "name": "Random",
        "url": "u2",
        "desc": "a flixhq mirror list",
        "category": "Misc",
        "section": "X",
    },
    {"name": "MusicFree", "url": "u3", "desc": "free music", "category": "Music", "section": "Y"},
]


def test_search_name_match_ranks_first(tmp_path) -> None:
    idx = _idx_with(_ENTRIES, tmp_path)
    results = idx.search("flixhq")
    assert [r["name"] for r in results] == ["FlixHQ", "Random"]  # name hit = 3 pts


def test_search_requires_every_term(tmp_path) -> None:
    idx = _idx_with(_ENTRIES, tmp_path)
    assert idx.search("flixhq music") == []  # no entry has both
    assert [r["name"] for r in idx.search("free music")] == ["MusicFree"]


def test_search_case_insensitive_and_short_terms_dropped(tmp_path) -> None:
    idx = _idx_with(_ENTRIES, tmp_path)
    assert [r["name"] for r in idx.search("FLIXHQ")] == ["FlixHQ", "Random"]
    assert idx.search("a") == []  # single-char terms are dropped → empty
    assert idx.search("") == []


def test_search_limit(tmp_path) -> None:
    many = _ENTRIES * 5
    idx2 = _idx_with(many, tmp_path)
    assert len(idx2.search("music", limit=2)) == 2


# ── sync ───────────────────────────────────────────────────────────


class _Resp:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status


class _Client:
    def __init__(self, pages):
        self.pages = pages
        self.urls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url):
        self.urls.append(url)
        for page, resp in self.pages.items():
            if url.endswith(f"/{page}.md"):
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return _Resp("", status=404)


def test_sync_indexes_all_pages_and_caches(tmp_path, monkeypatch) -> None:
    md = "* [**Tool**](https://t.example/) - does things\n"
    made = []
    client = _Client({p: _Resp(md) for p in fmhy._PAGES.values()})
    made.append(client)
    monkeypatch.setattr(fmhy.httpx, "Client", lambda **kw: made[0])
    p = tmp_path / "fmhy.json"
    idx = FmhyIndex(p)
    n = idx.sync()
    assert n == len(fmhy._PAGES)
    assert idx.size == n
    assert idx.is_stale is False
    cached = json.loads(p.read_text(encoding="utf-8"))
    assert len(cached["entries"]) == n
    assert cached["entries"][0]["category"] == "AI"
    # one request per wiki page, all against the raw wiki host
    assert len(client.urls) == len(fmhy._PAGES)
    assert all(
        u.startswith("https://raw.githubusercontent.com/wiki/fmhy/FMHY/") for u in client.urls
    )


def test_sync_skips_failing_pages(tmp_path, monkeypatch) -> None:
    md = "* [**Tool**](https://t.example/) - ok\n"
    pages = dict(fmhy._PAGES)
    first = next(iter(pages.values()))
    client_pages = {p: _Resp(md) for p in pages.values()}
    client_pages[first] = ConnectionError("offline")
    monkeypatch.setattr(fmhy.httpx, "Client", lambda **kw: _Client(client_pages))
    idx = FmhyIndex(tmp_path / "fmhy.json")
    assert idx.sync() == len(pages) - 1


def test_sync_no_entries_keeps_old_state(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        fmhy.httpx,
        "Client",
        lambda **kw: _Client({p: _Resp("", status=404) for p in fmhy._PAGES.values()}),
    )
    idx = FmhyIndex(tmp_path / "fmhy.json")
    assert idx.sync() == 0
    assert idx.size == 0
    assert not (tmp_path / "fmhy.json").exists()  # nothing written


def test_ensure_fresh_syncs_when_stale(tmp_path, monkeypatch) -> None:
    idx = FmhyIndex(tmp_path / "fmhy.json")
    calls = []
    monkeypatch.setattr(idx, "sync", lambda: calls.append(1) or 0)
    idx.ensure_fresh()
    assert calls == [1]


def test_ensure_fresh_skips_when_fresh(tmp_path, monkeypatch) -> None:
    idx = _idx_with(_ENTRIES, tmp_path)
    calls = []
    monkeypatch.setattr(idx, "sync", lambda: calls.append(1) or 0)
    idx.ensure_fresh()
    assert calls == []
