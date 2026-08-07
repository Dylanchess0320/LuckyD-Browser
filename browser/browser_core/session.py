"""Session persistence: restore open tabs on the next launch.

"Continue where you left off" — every non-incognito window periodically
snapshots its tabs (URL, title, pinned state, active index) to a JSON file.
On startup the BrowserApp reopens them. Writes are atomic (tmp + replace) so
a crash mid-save can never corrupt the previous session.

Pure Python — no Qt imports — so the whole module is unit-testable headless.
Set LUCKYD_SESSION_PATH to redirect the storage file (used by the selftest
and by portable installs).
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from pathlib import Path

if getattr(sys, "frozen", False):
    # Packaged build: per-user data dir (same model as settings.py).
    DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "LuckyDBrowser"
else:
    DATA_DIR = Path(__file__).resolve().parent.parent / "data"

SESSION_VERSION = 1

# Safety rails: a runaway session can never restore an unusable wall of tabs.
MAX_WINDOWS = 5
MAX_TABS_PER_WINDOW = 50

# Schemes worth restoring. Internal pages (dashboard, terminal, HQ) are plain
# localhost http URLs, so they ride along naturally; about:/view-source: and
# friends are deliberately dropped.
_RESTORABLE_PREFIXES = ("http://", "https://", "file://")


def session_path() -> Path:
    """Where the session file lives (env override wins)."""
    override = os.environ.get("LUCKYD_SESSION_PATH", "").strip()
    if override:
        return Path(override)
    return DATA_DIR / "session.json"


def is_restorable(url: str) -> bool:
    """True when `url` is worth reopening next launch."""
    return url.startswith(_RESTORABLE_PREFIXES)


def tab_record(url: str, title: str = "", pinned: bool = False) -> dict | None:
    """One tab's serializable state, or None when not restorable."""
    if not is_restorable(url):
        return None
    return {"url": url, "title": (title or "")[:200], "pinned": bool(pinned)}


def window_record(tabs: list[dict], current: int = 0) -> dict | None:
    """One window's serializable state, or None when it has no restorable tabs."""
    tabs = [t for t in tabs if t is not None][:MAX_TABS_PER_WINDOW]
    if not tabs:
        return None
    return {"tabs": tabs, "current": max(0, min(int(current), len(tabs) - 1))}


class SessionStore:
    """JSON-backed record of open windows/tabs with safe atomic writes."""

    def __init__(self, path: Path | None = None):
        self._path = Path(path) if path is not None else session_path()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict:
        """Return the saved session ({"version", "saved_at", "windows"}), {} on any problem."""
        try:
            data = json.loads(self._path.read_text(encoding="utf-8-sig"))
        except Exception:
            return {}
        if not isinstance(data, dict) or data.get("version") != SESSION_VERSION:
            return {}
        windows = data.get("windows")
        if not isinstance(windows, list):
            return {}
        # Clamp to the safety rails even if the file was hand-edited.
        data["windows"] = windows[:MAX_WINDOWS]
        return data

    def save(self, windows: list[dict]) -> bool:
        """Persist the window snapshots atomically. Returns success."""
        payload = {
            "version": SESSION_VERSION,
            "saved_at": time.time(),
            "windows": windows[:MAX_WINDOWS],
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(self._path.name + ".tmp")
            tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
            tmp.replace(self._path)
        except Exception:
            return False
        return True

    def clear(self) -> None:
        """Forget the saved session (e.g. after a clean shutdown with no tabs)."""
        with contextlib.suppress(Exception):
            self._path.unlink(missing_ok=True)
