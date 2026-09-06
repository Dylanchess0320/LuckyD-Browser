"""DuckDuckGo search (no API key required).

Resolution order:
1. The ``ddgs`` package (or legacy ``duckduckgo-search`` shim).
2. A zero-dependency ``httpx`` scrape of DuckDuckGo's HTML endpoint
   (keeps keyless search working in the frozen app, where the ddg
   packages may be excluded from the bundle).

Returns EvidenceCards with snippets; ``[]`` when unavailable.
"""

from __future__ import annotations

import html as _html
import re as _re
import urllib.parse as _urlparse

from ..schemas import EvidenceCard
from .search_base import SearchProvider


def _load_ddgs():
    try:
        from ddgs import DDGS  # type: ignore

        return DDGS
    except Exception:
        pass
    try:
        from duckduckgo_search import DDGS  # type: ignore

        return DDGS
    except Exception:
        return None


def _ddg_html_fallback(query: str, max_results: int = 8) -> list[EvidenceCard]:
    """Scrape html.duckduckgo.com with httpx only (frozen-safe)."""
    try:
        import httpx
    except Exception:
        return []
    try:
        url = f"https://html.duckduckgo.com/html/?q={_urlparse.quote(query)}"
        with httpx.Client(
            timeout=15.0,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            follow_redirects=True,
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
            page = resp.text or ""
    except Exception:
        return []

    link_re = _re.compile(
        r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        _re.DOTALL | _re.IGNORECASE,
    )
    snippet_re = _re.compile(r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', _re.DOTALL)
    links = link_re.findall(page)
    snippets = snippet_re.findall(page)
    out: list[EvidenceCard] = []
    for i, (href, title) in enumerate(links[:max_results]):
        title = _re.sub(r"<[^>]+>", "", title).strip()
        snippet = ""
        if i < len(snippets):
            snippet = _re.sub(r"<[^>]+>", "", snippets[i]).strip()
        out.append(
            EvidenceCard(
                id=f"e{i}",
                url=_html.unescape(href),
                title=_html.unescape(title)[:300],
                snippet=_html.unescape(snippet)[:600],
                source_query=query,
                worker_id="ddg-html",
                confidence=0.5,
            )
        )
    return out


class DDGSearch(SearchProvider):
    def search(self, query: str, max_results: int = 8) -> list[EvidenceCard]:
        ddgs_cls = _load_ddgs()
        if ddgs_cls is not None:
            results: list[EvidenceCard] = []
            try:
                with ddgs_cls() as ddgs:
                    for i, r in enumerate(ddgs.text(query, max_results=max_results)):
                        results.append(
                            EvidenceCard(
                                id=f"e{i}",
                                url=r.get("href") or r.get("url") or "",
                                title=r.get("title") or "",
                                snippet=(r.get("body") or "")[:600],
                                source_query=query,
                                worker_id="ddg",
                                confidence=0.55,
                            )
                        )
            except Exception:
                pass
            if results:
                return results
        # Library missing or errored: frozen-safe HTML fallback.
        return _ddg_html_fallback(query, max_results)
