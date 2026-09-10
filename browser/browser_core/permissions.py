"""Per-origin site permissions — camera, mic, location, notifications.

Qt WebEngine denies feature requests unless the page handles
``featurePermissionRequested``. This store remembers Allow/Deny per origin
so the prompt only fires once. Pure JSON + stdlib: unit-testable without Qt.

Incognito windows should never persist — callers pass ``persist=False``.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

ALLOW = "allow"
DENY = "deny"
ASK = "ask"

# Qt QWebEnginePage.Feature enum names -> stable storage keys.
FEATURE_KEYS = {
    "Notifications": "notifications",
    "Geolocation": "geolocation",
    "MediaAudioCapture": "microphone",
    "MediaVideoCapture": "camera",
    "MediaAudioVideoCapture": "camera_mic",
    "MouseLock": "pointer_lock",
    "DesktopVideoCapture": "screen",
    "DesktopAudioVideoCapture": "screen_audio",
}

FEATURE_LABELS = {
    "notifications": "Notifications",
    "geolocation": "Location",
    "microphone": "Microphone",
    "camera": "Camera",
    "camera_mic": "Camera and microphone",
    "pointer_lock": "Pointer lock",
    "screen": "Screen capture",
    "screen_audio": "Screen and audio capture",
}


def _data_dir() -> Path:
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA", "").strip()
        if base:
            return Path(base) / "LuckyDBrowser"
        return Path.home() / "AppData" / "Local" / "LuckyDBrowser"
    return Path(__file__).resolve().parent.parent / "data"


def origin_of(url: str) -> str:
    """scheme://host[:port] for http(s) URLs, else ''."""
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
    except ValueError:
        return ""
    if port and not (scheme == "http" and port == 80) and not (scheme == "https" and port == 443):
        return f"{scheme}://{host}:{port}"
    return f"{scheme}://{host}"


def feature_key(feature) -> str:
    """Map a Qt Feature enum (or a string name) onto a storage key."""
    name = getattr(feature, "name", None) or str(feature)
    name = name.split(".")[-1]
    return FEATURE_KEYS.get(name, name.lower())


def feature_label(key: str) -> str:
    return FEATURE_LABELS.get(key, key.replace("_", " ").title())


class PermissionStore:
    """JSON map of origin -> {feature: allow|deny}."""

    def __init__(self, path: Path | None = None):
        self._path = path or (_data_dir() / "permissions.json")
        self._data: dict[str, dict] = {}
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8-sig"))
        except Exception:
            self._data = {}
            return
        sites = raw.get("sites") if isinstance(raw, dict) else None
        self._data = sites if isinstance(sites, dict) else {}

    def save(self) -> bool:
        """Persist permissions atomically. Returns True on success; logs and
        returns False on failure instead of silently dropping the write."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"version": 1, "saved_at": time.time(), "sites": self._data}
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except Exception as exc:
            print(f"[permissions] save failed for {self._path}: {exc}")
            return False
        return True

    def get(self, origin: str, key: str) -> str:
        """Return allow, deny, or ask."""
        site = self._data.get(origin) or {}
        decision = site.get(key)
        if decision in (ALLOW, DENY):
            return decision
        return ASK

    def set(self, origin: str, key: str, decision: str, persist: bool = True) -> None:
        if not origin or decision not in (ALLOW, DENY, ASK):
            return
        site = dict(self._data.get(origin) or {})
        if decision == ASK:
            site.pop(key, None)
        else:
            site[key] = decision
        if site:
            self._data[origin] = site
        else:
            self._data.pop(origin, None)
        if persist:
            self.save()

    def clear_origin(self, origin: str, persist: bool = True) -> None:
        self._data.pop(origin, None)
        if persist:
            self.save()

    def clear_all(self, persist: bool = True) -> None:
        self._data = {}
        if persist:
            self.save()

    def sites(self) -> list[tuple[str, dict]]:
        """(origin, {feature: decision}) sorted by origin."""
        return sorted(self._data.items(), key=lambda item: item[0])
