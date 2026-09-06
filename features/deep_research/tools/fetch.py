"""Robust URL fetch + main-content extraction.

Uses ``trafilatura`` for high-quality boilerplate removal (best for articles),
falling back to ``BeautifulSoup`` when trafilatura fails or returns nothing.
Returns a SourceDocument with clean text suitable for passage extraction.
"""

from __future__ import annotations

import logging

import httpx

try:
    from tenacity import retry, stop_after_attempt, wait_exponential

    _retry_decorator = retry(
        stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=4), reraise=True
    )
except Exception:  # tenacity optional; single attempt without it

    def _retry_decorator(fn):  # type: ignore[no-redef]
        return fn


from ..schemas import SourceDocument

log = logging.getLogger("luckyd.deep_research.fetch")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


@_retry_decorator
def _get_html(url: str, timeout: float) -> str:
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=_HEADERS) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.text or ""


def _extract_with_trafilatura(html: str) -> tuple[str, str]:
    """Return (text, title). Best-effort; returns ('','') on failure."""
    try:
        import trafilatura  # type: ignore
    except Exception:
        return "", ""
    try:
        text = trafilatura.extract(html, include_comments=False, include_tables=False) or ""
        meta = trafilatura.extract_metadata(html) if html else None
        title = ""
        if meta is not None:
            title = getattr(meta, "title", "") or ""
        return text, title
    except Exception:
        return "", ""


def _extract_with_bs4(html: str) -> tuple[str, str]:
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except Exception:
        return "", ""
    try:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "form"]):
            tag.decompose()
        title = (soup.title.string.strip() if soup.title and soup.title.string else "") or ""
        text = " ".join(soup.stripped_strings)
        return text, title
    except Exception:
        return "", ""


def fetch_document(url: str, timeout: float = 10.0, max_chars: int = 20000) -> SourceDocument:
    """GET a URL and return a SourceDocument with cleaned main-content text.

    Results are cached by canonical URL, so repeated runs don't re-fetch.
    """
    from ..runtime.budget import BudgetExhausted, get_budget
    from .cache import get_cache

    cache = get_cache()
    try:
        cached = cache.get_document(url)
        if cached is not None:
            title, text, src_url, fetch_ok = cached
            return SourceDocument(url=src_url, title=title, text=text, fetch_ok=fetch_ok)
    except Exception:
        pass

    try:
        get_budget().record_fetch()
    except BudgetExhausted:
        # Propagate so the worker loop can stop with budget_exhausted.
        raise

    try:
        html = _get_html(url, timeout)
        if not html:
            doc = SourceDocument(url=url, fetch_ok=False, error="empty response")
            cache.put_document(url, "", "", False)
            return doc
    except Exception as e:
        doc = SourceDocument(url=url, fetch_ok=False, error=f"{type(e).__name__}: {e}"[:200])
        cache.put_document(url, "", "", False)
        return doc

    text, title = _extract_with_trafilatura(html)
    if not text:
        text, t2 = _extract_with_bs4(html)
        if not title and t2:
            title = t2
    text = (text or "")[:max_chars]
    if not text:
        doc = SourceDocument(url=url, title=title, fetch_ok=False, error="extraction empty")
        cache.put_document(url, title or "", "", False)
        return doc
    cache.put_document(url, title, text, True)
    return SourceDocument(url=url, title=title, text=text, fetch_ok=True)
