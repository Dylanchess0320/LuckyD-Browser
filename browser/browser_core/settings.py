"""Persistent browser settings stored as JSON."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import quote_plus

if getattr(sys, "frozen", False):
    # Packaged build: per-user data dir (writable even under Program Files,
    # survives reinstalls — same model as Chrome/VS Code).
    DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "LuckyDBrowser"
else:
    DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SETTINGS_PATH = DATA_DIR / "settings.json"

SEARCH_ENGINES = {
    "Google": "https://www.google.com/search?q={query}",
    "Bing": "https://www.bing.com/search?q={query}",
    "DuckDuckGo": "https://duckduckgo.com/?q={query}",
    "Brave": "https://search.brave.com/search?q={query}",
    "Startpage": "https://www.startpage.com/sp/search?query={query}",
}

DEFAULTS = {
    "homepage": "newtab",  # "newtab" or a full URL
    "search_engine": "Google",
    "adblock_enabled": True,
    "download_dir": "",  # empty = system Downloads folder
    "zoom_factor": 1.0,
    # Session restore: "restore" reopens last session's tabs on launch,
    # "newtab" always starts fresh. Incognito windows are never saved.
    "startup_mode": "restore",
    # Bookmark bar under the navigation toolbar (Ctrl+Shift+B toggles).
    "bookmark_bar_visible": True,
    # Zoom: remember per-site levels (origin -> factor) in addition to the
    # global "zoom_factor" default above.
    "zoom_remember": True,
    "zoom_levels": {},
    # Browser Control API (localhost HTTP control of the live browser —
    # lets the luckyd-code.exe harness drive tabs; see browser/README.md).
    "browser_api_enabled": True,
    "browser_api_port": 9777,
    "browser_api_token": "",  # empty = trust localhost; set to require Bearer
    # Embedded terminal: which LuckyD Code CLI to spawn. Empty = auto-detect
    # (Desktop exe → repo exe → live source). Set a full path to force one.
    "terminal_cli": "",
    # 2nd agent: standalone coding-agent CLI (exe, main.py, or run.bat).
    # Empty = auto-detect the coding-agent checkout on the Desktop.
    "terminal_cli2": "",
    # Port of the WebSocket↔PTY bridge the /terminal tab connects to.
    "terminal_port": 9881,
    # Auto-update: silently check for a newer release shortly after launch.
    "update_auto_check": True,
    # A version tag the user chose to skip (don't prompt for it again).
    "update_skipped_version": "",
    # Userscripts disabled by name. "Dark Mode Everywhere" inverts EVERY page
    # (images included) — powerful but strictly opt-in; the other built-ins
    # (YouTube ad-block, video speed) are safe defaults.
    "userscript_disabled": ["Dark Mode Everywhere"],
}


class SettingsStore:
    """Tiny JSON key/value store with defaults and safe writes."""

    def __init__(self, path: Path = SETTINGS_PATH):
        self._path = path
        self._data = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            if self._path.exists():
                # utf-8-sig accepts the UTF-8 BOM that PowerShell 5's Set-Content
                # prepends — without it the BOM breaks JSON parsing silently and
                # settings silently fall back to defaults (ask me how I know).
                loaded = json.loads(self._path.read_text(encoding="utf-8-sig"))
                if isinstance(loaded, dict):
                    self._data.update(loaded)
        except Exception:
            pass  # corrupted settings fall back to defaults

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = value
        self.save()

    def search_url_for(self, query: str) -> str:
        template = SEARCH_ENGINES.get(
            self._data.get("search_engine", "Google"), SEARCH_ENGINES["Google"]
        )
        return template.format(query=quote_plus(query))
