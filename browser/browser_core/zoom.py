"""Per-site zoom memory helpers — pure logic, unit-testable without Qt.

Zoom factors are remembered per origin (scheme://host[:port]) in the
`zoom_levels` settings dict. Only http/https pages participate; internal
localhost pages (dashboard/terminal/HQ) share their origin like any other
site, which is exactly what you want.
"""

from __future__ import annotations

from urllib.parse import urlsplit

MIN_ZOOM = 0.25
MAX_ZOOM = 5.0
DEFAULT_ZOOM = 1.0


def clamp_zoom(factor: float) -> float:
    """Keep zoom inside Qt WebEngine's sane range, rounded to whole percents."""
    try:
        value = float(factor)
    except (TypeError, ValueError):
        return DEFAULT_ZOOM
    return max(MIN_ZOOM, min(MAX_ZOOM, round(value, 2)))


def origin_key(url: str) -> str:
    """Stable per-site key for `url`: "scheme://host[:port]".

    Returns "" for anything that should not carry zoom memory (files, blank
    pages, view-source, malformed URLs). Default ports are normalized away so
    https://example.com and https://example.com:443 share one entry.
    """
    try:
        parts = urlsplit(url or "")
    except Exception:
        return ""
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return ""
    host = (parts.hostname or "").lower()
    if not host:
        return ""
    try:
        port = parts.port
    except ValueError:  # malformed port in URL
        return ""
    if port and not (scheme == "http" and port == 80) and not (scheme == "https" and port == 443):
        return f"{scheme}://{host}:{port}"
    return f"{scheme}://{host}"


def zoom_for(levels: dict | None, url: str, default: float = DEFAULT_ZOOM) -> float:
    """Look up the remembered zoom for `url` (clamped), or `default`."""
    key = origin_key(url)
    if not key or not isinstance(levels, dict):
        return clamp_zoom(default)
    return clamp_zoom(levels.get(key, default))


def remember(levels: dict | None, url: str, factor: float) -> dict:
    """Return a new levels dict with `factor` stored for `url`'s origin.

    A factor of ~1.0 deletes the entry instead — the default needs no memory.
    """
    updated = dict(levels) if isinstance(levels, dict) else {}
    key = origin_key(url)
    if not key:
        return updated
    factor = clamp_zoom(factor)
    if abs(factor - DEFAULT_ZOOM) < 0.01:
        updated.pop(key, None)
    else:
        updated[key] = factor
    return updated
