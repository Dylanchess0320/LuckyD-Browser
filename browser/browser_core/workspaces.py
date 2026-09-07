"""Named workspaces — saved tab collections you can switch between.

A workspace is a named snapshot of restorable tabs (same shape as a session
window). Switching saves the current window into the active workspace, then
loads the target. Pure JSON: unit-testable without Qt.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
from pathlib import Path

from .session import MAX_TABS_PER_WINDOW, is_restorable, tab_record

MAX_WORKSPACES = 20


def _data_dir() -> Path:
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA", "").strip()
        if base:
            return Path(base) / "LuckyDBrowser"
        return Path.home() / "AppData" / "Local" / "LuckyDBrowser"
    return Path(__file__).resolve().parent.parent / "data"


def new_id() -> str:
    return "ws_" + secrets.token_hex(4)


def sanitize_tabs(tabs: list) -> list[dict]:
    clean: list[dict] = []
    for item in tabs or []:
        if not isinstance(item, dict):
            continue
        rec = tab_record(
            str(item.get("url") or ""),
            str(item.get("title") or ""),
            bool(item.get("pinned")),
            group=str(item.get("group") or ""),
        )
        if rec is not None:
            clean.append(rec)
        if len(clean) >= MAX_TABS_PER_WINDOW:
            break
    return clean


class WorkspaceStore:
    def __init__(self, path: Path | None = None):
        self._path = path or (_data_dir() / "workspaces.json")
        self._workspaces: list[dict] = []
        self._active: str = ""
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8-sig"))
        except Exception:
            self._workspaces = []
            self._active = ""
            return
        if not isinstance(raw, dict):
            self._workspaces = []
            self._active = ""
            return
        items = raw.get("workspaces")
        out: list[dict] = []
        if isinstance(items, list):
            for item in items[:MAX_WORKSPACES]:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                out.append(
                    {
                        "id": str(item["id"]),
                        "name": str(item.get("name") or "Workspace")[:60],
                        "tabs": sanitize_tabs(item.get("tabs") or []),
                        "current": int(item.get("current") or 0),
                        "updated_at": float(item.get("updated_at") or 0),
                    }
                )
        self._workspaces = out
        active = str(raw.get("active") or "")
        self._active = active if any(w["id"] == active for w in out) else ""

    def save(self) -> bool:
        payload = {
            "version": 1,
            "active": self._active,
            "workspaces": self._workspaces[:MAX_WORKSPACES],
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except Exception:
            return False
        return True

    def list(self) -> list[dict]:
        return list(self._workspaces)

    def get(self, ws_id: str) -> dict | None:
        for item in self._workspaces:
            if item["id"] == ws_id:
                return item
        return None

    @property
    def active_id(self) -> str:
        return self._active

    def set_active(self, ws_id: str) -> None:
        if ws_id and self.get(ws_id) is None:
            return
        self._active = ws_id
        self.save()

    def create(self, name: str, tabs: list[dict], current: int = 0) -> dict:
        item = {
            "id": new_id(),
            "name": (name or "").strip()[:60] or "Workspace",
            "tabs": sanitize_tabs(tabs),
            "current": max(0, int(current)),
            "updated_at": time.time(),
        }
        if len(self._workspaces) >= MAX_WORKSPACES:
            self._workspaces.pop(0)
        self._workspaces.append(item)
        self._active = item["id"]
        self.save()
        return item

    def update_tabs(self, ws_id: str, tabs: list[dict], current: int = 0) -> bool:
        item = self.get(ws_id)
        if item is None:
            return False
        item["tabs"] = sanitize_tabs(tabs)
        item["current"] = max(0, int(current))
        item["updated_at"] = time.time()
        self.save()
        return True

    def rename(self, ws_id: str, name: str) -> bool:
        item = self.get(ws_id)
        if item is None:
            return False
        item["name"] = (name or "").strip()[:60] or item["name"]
        item["updated_at"] = time.time()
        self.save()
        return True

    def delete(self, ws_id: str) -> bool:
        before = len(self._workspaces)
        self._workspaces = [w for w in self._workspaces if w["id"] != ws_id]
        if self._active == ws_id:
            self._active = self._workspaces[0]["id"] if self._workspaces else ""
        if len(self._workspaces) == before:
            return False
        self.save()
        return True


def snapshot_from_records(records: list[dict], current: int = 0) -> dict:
    """Helper used by the window: filter restorable tab dicts."""
    tabs = sanitize_tabs(records)
    return {"tabs": tabs, "current": max(0, min(int(current), max(len(tabs) - 1, 0)))}


def is_workspace_url(url: str) -> bool:
    return is_restorable(url)
