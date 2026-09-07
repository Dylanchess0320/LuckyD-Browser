"""HTTPS-Only Mode — upgrade main-frame http:// navigations to https://.

Localhost, private LAN addresses, and non-http schemes are left alone so
printers, routers, and the Control API keep working. Pure logic: no Qt.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit, urlunsplit


def _is_private_host(host: str) -> bool:
    name = (host or "").strip("[]").lower()
    if not name:
        return True
    if name in ("localhost", "localhost.localdomain"):
        return True
    if name.endswith(".local") or name.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return False
    return bool(
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
    )


def is_upgradable(url: str) -> bool:
    """True when HTTPS-Only should rewrite this URL."""
    try:
        parts = urlsplit(url or "")
    except Exception:
        return False
    if parts.scheme.lower() != "http":
        return False
    host = parts.hostname or ""
    return not _is_private_host(host)


def upgrade_url(url: str) -> str | None:
    """Return the https:// equivalent, or None when no rewrite is needed."""
    if not is_upgradable(url):
        return None
    try:
        parts = urlsplit(url)
    except Exception:
        return None
    netloc = parts.netloc
    # Drop explicit :80 so https://host:80 does not leak through.
    if netloc.endswith(":80"):
        netloc = netloc[:-3]
    return urlunsplit(("https", netloc, parts.path, parts.query, parts.fragment))
