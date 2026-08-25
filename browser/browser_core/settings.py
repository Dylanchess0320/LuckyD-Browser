"""Persistent browser settings stored as JSON."""

from __future__ import annotations

import contextlib
import copy
import json
import os
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus


def _data_dir() -> Path:
    if getattr(sys, "frozen", False):
        # Packaged build: per-user data dir (writable even under Program Files,
        # survives reinstalls — same model as Chrome/VS Code).
        base = os.environ.get("LOCALAPPDATA", "").strip()
        if base:
            return Path(base) / "LuckyDBrowser"
        # Fallback when LOCALAPPDATA is missing (rare) — not Path("").
        return Path.home() / "AppData" / "Local" / "LuckyDBrowser"
    return Path(__file__).resolve().parent.parent / "data"


DATA_DIR = _data_dir()
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
    # Auto-generated on first run by SettingsStore — binding to 127.0.0.1
    # does NOT make this safe to leave
    # unauthenticated, since any page open in the browser itself can still
    # reach it with a normal cross-origin fetch(). Do not hand-set to "".
    "browser_api_token": "",
    # Embedded terminal: which LuckyD Code CLI to spawn. Empty = auto-detect
    # (Desktop exe → repo exe → live source). Set a full path to force one.
    "terminal_cli": "",
    # 2nd agent: standalone coding-agent CLI (exe, main.py, or run.bat).
    # Empty = auto-detect the coding-agent checkout on the Desktop.
    "terminal_cli2": "",
    # Port of the WebSocket↔PTY bridge the /terminal tab connects to.
    "terminal_port": 9881,
    # Auto-generated on first run if empty, same rationale as browser_api_token
    # — required as a ?token= query param since the browser WebSocket API
    # can't set an Authorization header.
    "terminal_token": "",
    # Full-agent mode is on for new profiles; users can still opt out in the
    # sidebar, and that choice is persisted separately from auto-start.
    "harness_mode": True,
    "harness_autostart": True,
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
        self._data = copy.deepcopy(DEFAULTS)
        self.load()
        if self._ensure_local_tokens():
            self.save()

    def load(self) -> None:
        try:
            if self._path.exists():
                # utf-8-sig accepts the UTF-8 BOM that PowerShell 5's Set-Content
                # prepends — without it the BOM breaks JSON parsing silently and
                # settings silently fall back to defaults (ask me how I know).
                text = self._path.read_text(encoding="utf-8-sig")
                try:
                    loaded = json.loads(text)
                except Exception as exc:
                    # Preserve corrupt file for debugging instead of silently dropping.
                    with contextlib.suppress(Exception):
                        corrupt = self._path.with_name(
                            f"settings.corrupt.{int(time.time())}.json"
                        )
                        self._path.replace(corrupt)
                    raise exc
                if isinstance(loaded, dict):
                    self._data.update(loaded)
                    if self._migrate_legacy_terminal_cli():
                        self.save()
        except Exception:
            pass  # corrupted settings fall back to defaults

    def _migrate_legacy_terminal_cli(self) -> bool:
        """Repair the old terminal setting that targeted the HQ web server.

        Older browser releases wrote ``luckyd-code.exe`` into ``terminal_cli``.
        That executable runs ``web_server.py`` and has no interactive prompt,
        so terminal tabs appeared to hang.  Replace it with the sibling
        ``luckyd-cli.exe`` when available; otherwise clear it so normal
        auto-discovery can select the packaged interactive CLI.
        """
        raw = str(self._data.get("terminal_cli", "") or "").strip()
        if not raw:
            return False
        # Expand %VARS% / ~ so is_file check works for user-copied paths.
        expanded = Path(os.path.expandvars(os.path.expanduser(raw)))
        if expanded.name.casefold() != "luckyd-code.exe":
            return False
        cli = expanded.with_name("luckyd-cli.exe")
        self._data["terminal_cli"] = str(cli) if cli.is_file() else ""
        return True

    def _ensure_local_tokens(self) -> bool:
        """Create secrets for localhost control surfaces on first run.

        Loopback is not a trust boundary for browser features: an arbitrary
        website can ask the user's browser to connect to a loopback port.
        Both the tab-control HTTP API and the terminal's WebSocket-to-PTY
        bridge therefore need unguessable, per-profile credentials.  The
        values stay in the user's settings file and never need to be entered
        manually.
        """
        changed = False
        for key in ("browser_api_token", "terminal_token"):
            if not str(self._data.get(key, "") or "").strip():
                self._data[key] = secrets.token_urlsafe(32)
                changed = True
        return changed

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
            # Atomic replace — power loss or AV lock can't leave a truncated file.
            tmp.replace(self._path)
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
