"""Source quality scoring + deduplication.

Scores a source by domain type (primary/official vs. aggregator vs. unknown),
recency, and uniqueness. Used to rank URLs before fetching and to weight
evidence cards.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlparse

try:
    import tldextract
except Exception:
    tldextract = None  # type: ignore[assignment]

# Domains that tend to be high-quality primary or reputable sources.
_HIGH_QUALITY = {
    "wikipedia.org",
    "arxiv.org",
    "nature.com",
    "science.org",
    "ieee.org",
    "acm.org",
    "nih.gov",
    "cdc.gov",
    "nasa.gov",
    "ssa.gov",
    "gov.uk",
    "eia.gov",
    "bls.gov",
    "sec.gov",
    "federalreserve.gov",
    "who.int",
    "un.org",
    "worldbank.org",
    "imf.org",
    "oecd.org",
    "reuters.com",
    "apnews.com",
    "bbc.com",
    "bbc.co.uk",
    "nytimes.com",
    "wsj.com",
    "bloomberg.com",
    "ft.com",
    "economist.com",
    "github.com",
    "stackoverflow.com",
    "docs.python.org",
    "mozilla.org",
    "w3.org",
    "iso.org",
    "nist.gov",
    "energy.gov",
}

# Low-quality / aggregators / forums to downweight.
_LOW_QUALITY = {
    "pinterest.com",
    "tiktok.com",
    "reddit.com",
    "quora.com",
    "medium.com",
    "substack.com",
    "buzzfeed.com",
    "answers.com",
}


def _registered_domain(url: str) -> str:
    try:
        if tldextract is not None:
            ext = tldextract.extract(url)
            if ext.domain and ext.suffix:
                return f"{ext.domain}.{ext.suffix}".lower()
    except Exception:
        pass
    # Fallback without tldextract: last two host labels.
    try:
        host = (urlparse(url).hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        parts = [p for p in host.split(".") if p]
        if len(parts) >= 2:
            return ".".join(parts[-2:])
        return host
    except Exception:
        pass
    return ""


def domain_type_score(url: str) -> float:
    reg = _registered_domain(url)
    if not reg:
        return 0.35
    if reg in _HIGH_QUALITY:
        return 0.95
    if any(reg.endswith(d) for d in _HIGH_QUALITY):
        return 0.9
    if reg in _LOW_QUALITY:
        return 0.35
    # .gov / .edu / .mil get a bump
    if re.search(r"\.(gov|edu|mil)$", reg):
        return 0.9
    return 0.6


def recency_score(published_date: str) -> float:
    """Higher score for more recent sources. Unknown date -> neutral 0.5."""
    if not published_date:
        return 0.5
    # try several common formats
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y", "%B %d, %Y", "%b %d, %Y", "%d %B %Y"):
        try:
            d = datetime.strptime(published_date.strip(), fmt)
            days_old = (datetime.now(timezone.utc) - d.replace(tzinfo=timezone.utc)).days
            if days_old < 0:
                return 0.7
            if days_old < 90:
                return 0.95
            if days_old < 365:
                return 0.8
            if days_old < 365 * 3:
                return 0.6
            return 0.45
        except ValueError:
            continue
    return 0.5


def score_source(url: str, published_date: str = "", relevance: float = 0.5) -> float:
    """Blend domain quality, recency, and passage relevance into [0, 1]."""
    dt = domain_type_score(url)
    rc = recency_score(published_date)
    rel = max(0.0, min(1.0, relevance))
    # Weighted: relevance matters most, then domain, then recency.
    return round(0.5 * rel + 0.3 * dt + 0.2 * rc, 3)


def dedupe_by_url(urls: list[str]) -> list[str]:
    """Normalize and dedupe URLs using the same canonical form as the cache."""
    from .cache import canonicalize_url

    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        if not url:
            continue
        norm = canonicalize_url(url)
        if norm in seen:
            continue
        seen.add(norm)
        out.append(url)
    return out


def rank_urls(urls: list[str], published_dates: dict[str, str] | None = None) -> list[str]:
    """Rank URLs by source-quality score (best first)."""
    published_dates = published_dates or {}
    scored = sorted(urls, key=lambda u: domain_type_score(u), reverse=True)
    return scored
