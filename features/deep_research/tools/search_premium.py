"""Premium search backends (optional API keys, graceful degradation).

- :class:`TavilySearch` — Tavily Search API (``TAVILY_API_KEY``).
- :class:`BraveSearch` — Brave Search API (``BRAVE_API_KEY``).

Both implement :class:`SearchProvider` and return snippet-backed EvidenceCards.
Missing keys, missing ``httpx``, or any transport/API error yields ``[]`` so
the swarm transparently falls back to other backends. No third-party search
SDK is required — plain ``httpx`` only (which also keeps the frozen app
dependency-free here).
"""

from __future__ import annotations

import os

from ..schemas import EvidenceCard
from .search_base import SearchProvider


def _tavily_key() -> str:
    return (os.getenv("TAVILY_API_KEY") or "").strip()


def _brave_key() -> str:
    return (os.getenv("BRAVE_API_KEY") or "").strip()


class TavilySearch(SearchProvider):
    """Tavily AI search (https://tavily.com). Needs TAVILY_API_KEY."""

    _endpoint = "https://api.tavily.com/search"

    def search(self, query: str, max_results: int = 8) -> list[EvidenceCard]:
        key = _tavily_key()
        if not key:
            return []
        try:
            import httpx
        except Exception:
            return []
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.post(
                    self._endpoint,
                    json={
                        "api_key": key,
                        "query": query,
                        "max_results": max(1, min(max_results, 10)),
                        "include_answer": False,
                        "include_raw_content": False,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            out: list[EvidenceCard] = []
            for i, r in enumerate(data.get("results", [])[:max_results]):
                url = str(r.get("url", "") or "")
                if not url:
                    continue
                out.append(
                    EvidenceCard(
                        id=f"e{i}",
                        url=url,
                        title=str(r.get("title", "") or "")[:300],
                        snippet=str(r.get("content", "") or "")[:600],
                        source_query=query,
                        worker_id="tavily",
                        confidence=0.7,
                    )
                )
            return out
        except Exception:
            return []


class BraveSearch(SearchProvider):
    """Brave Search API (https://brave.com/search/api). Needs BRAVE_API_KEY."""

    _endpoint = "https://api.search.brave.com/res/v1/web/search"

    def search(self, query: str, max_results: int = 8) -> list[EvidenceCard]:
        key = _brave_key()
        if not key:
            return []
        try:
            import httpx
        except Exception:
            return []
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.get(
                    self._endpoint,
                    headers={
                        "Accept": "application/json",
                        "X-Subscription-Token": key,
                    },
                    params={"q": query, "count": max(1, min(max_results, 10))},
                )
                resp.raise_for_status()
                data = resp.json()
            out: list[EvidenceCard] = []
            web = data.get("web", {}) or {}
            for i, r in enumerate(web.get("results", [])[:max_results]):
                url = str(r.get("url", "") or "")
                if not url:
                    continue
                out.append(
                    EvidenceCard(
                        id=f"e{i}",
                        url=url,
                        title=str(r.get("title", "") or "")[:300],
                        snippet=str(r.get("description", "") or "")[:600],
                        source_query=query,
                        worker_id="brave",
                        confidence=0.65,
                    )
                )
            return out
        except Exception:
            return []
