"""Memory saver policy for background tabs.

Qt WebEngine pages can be Frozen (JS paused) then Discarded (unloaded).
This module decides *whether* and *how far* to put a tab to sleep. The
Qt calls live in the tab widget; the policy is pure and testable.
"""

from __future__ import annotations

from urllib.parse import urlsplit

# Default idle thresholds (seconds).
FREEZE_AFTER = 5 * 60
DISCARD_AFTER = 15 * 60

ACTIVE = "active"
FROZEN = "frozen"
DISCARDED = "discarded"


def is_protected_url(url: str) -> bool:
    """Never sleep Control API / HQ / terminal / file pages."""
    try:
        parts = urlsplit(url or "")
    except Exception:
        return True
    scheme = parts.scheme.lower()
    if scheme in ("", "about", "file", "luckyd", "data", "blob", "view-source"):
        return True
    host = (parts.hostname or "").lower()
    if host in ("127.0.0.1", "localhost", "::1"):
        return True
    path = (parts.path or "").lower()
    return path.startswith(("/dashboard", "/hq", "/terminal", "/research", "/network"))


def should_protect(
    *,
    url: str,
    is_current: bool,
    pinned: bool,
    audible: bool,
) -> bool:
    """True when this tab must stay fully alive."""
    if is_current or pinned or audible:
        return True
    return is_protected_url(url)


def next_state(
    idle_seconds: float, freeze_after: int = FREEZE_AFTER, discard_after: int = DISCARD_AFTER
) -> str:
    """Lifecycle state a background tab should move to after `idle_seconds`."""
    idle = max(0.0, float(idle_seconds))
    freeze_after = max(15, int(freeze_after))
    discard_after = max(freeze_after, int(discard_after))
    if idle >= discard_after:
        return DISCARDED
    if idle >= freeze_after:
        return FROZEN
    return ACTIVE
