"""Proactive Hub cards — growth-watch changes surfaced on the Chat page
(Hub slice 5).

The growth watch (GitHub stars / Discord joins, cloud-side
``luckyd-watch/check.py``) used to ping chat directly. Slice 5 routes it
through the Hub instead: the watch adapter POSTs each real change to
``/chat/cards`` and the Chat page renders it as a dismissible card.
A no-change day posts nothing, so nothing renders — silence, not a ping.

Same ownership/style as the slice-3 handoff note (``hub_memory.py``):
a tiny JSON store owned by the browser, atomic writes (tmp + replace),
a corrupt or missing file reads as empty, and nothing here ever raises
to a caller. No YouTube — Dylan: no YouTube updates (2026-09-30).
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

if getattr(sys, "frozen", False):
    DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "LuckyDBrowser"
else:
    DATA_DIR = Path(__file__).resolve().parent.parent / "data"

CARDS_PATH = DATA_DIR / "hub_cards.json"

#: Cards are capped so the file can never grow without bound.
MAX_CARDS = 20
#: A card title/detail is a short line, not a document.
MAX_TITLE_LEN = 200
MAX_DETAIL_LEN = 1000

#: Kinds the watch actually produces (check.py change ``type`` values).
KNOWN_KINDS = ("github_stars", "discord_members")


class CardStore:
    """Dismissible proactive-card log persisted as JSON."""

    def __init__(self, path: Path | None = None):
        self._path = Path(path) if path is not None else CARDS_PATH

    def _read(self) -> list:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8-sig"))
            if isinstance(data, list):
                return [e for e in data if isinstance(e, dict)]
        except Exception:
            pass
        return []

    def _write(self, entries: list) -> None:
        with contextlib.suppress(Exception):
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(entries, indent=1), encoding="utf-8")
            tmp.replace(self._path)

    def add(self, kind: str, title: str, detail: str = "") -> dict:
        """Store one card. Returns {"state": "ok", "card": ...} or
        {"state": "error", "error": ...} for an unknown kind or empty title."""
        k = str(kind or "").strip()
        if k not in KNOWN_KINDS:
            return {"state": "error", "error": f"unknown card kind: {k or '(empty)'}"}
        head = str(title or "").strip()
        if not head:
            return {"state": "error", "error": "title required"}
        body = str(detail or "").strip()
        entries = self._read()
        card = {
            "id": uuid.uuid4().hex[:12],
            "kind": k,
            "title": head[:MAX_TITLE_LEN],
            "detail": body[:MAX_DETAIL_LEN],
            "created_at": time.time(),
            "dismissed": False,
        }
        entries.append(card)
        self._write(entries[-MAX_CARDS:])
        return {"state": "ok", "card": card}

    def dismiss(self, card_id: str) -> dict:
        """Mark one card dismissed. Returns {"state": "ok"} or an error
        when the id is unknown (already dismissed counts as ok)."""
        cid = str(card_id or "").strip()
        if not cid:
            return {"state": "error", "error": "id required"}
        entries = self._read()
        for entry in entries:
            if entry.get("id") == cid:
                entry["dismissed"] = True
                self._write(entries)
                return {"state": "ok", "id": cid}
        return {"state": "error", "error": f"unknown card id: {cid}"}

    def current(self) -> list:
        """Undismissed cards, oldest first."""
        return [e for e in self._read() if not e.get("dismissed")]

    def list(self, limit: int = 20) -> list:
        entries = self._read()
        try:
            n = max(0, int(limit))
        except Exception:
            n = 20
        return entries[-n:] if n else []

    def count(self) -> int:
        return len(self.current())


def card_from_watch_change(change: dict) -> dict:
    """Turn one ``check.py`` change dict into card fields.

    check.py emits ``{"type": "github_stars", "from": 4, "to": 5,
    "new_stargazers": ["login", ...]}`` and ``{"type":
    "discord_members", "from": 12, "to": 13}``. Returns
    ``{"state": "ok", "kind": ..., "title": ..., "detail": ...}`` or
    ``{"state": "error", "error": ...}`` for an unknown kind or a
    malformed change (missing/non-integer from/to, or a non-list
    new_stargazers). Never raises.
    """
    try:
        if not isinstance(change, dict):
            return {"state": "error", "error": "change must be an object"}
        kind = str(change.get("type") or "").strip()
        if kind not in KNOWN_KINDS:
            return {"state": "error", "error": f"unknown change type: {kind or '(empty)'}"}
        src, dst = change.get("from"), change.get("to")
        if (
            not isinstance(src, int)
            or not isinstance(dst, int)
            or isinstance(src, bool)
            or isinstance(dst, bool)
        ):
            return {"state": "error", "error": "change needs integer 'from' and 'to'"}
        if kind == "github_stars":
            new = change.get("new_stargazers", [])
            if not isinstance(new, list) or not all(isinstance(x, str) for x in new):
                return {"state": "error", "error": "'new_stargazers' must be a list of logins"}
            names = ", ".join(new[:5])
            extra = f" (+{len(new) - 5} more)" if len(new) > 5 else ""
            title = f"⭐ New GitHub star{'s' if dst - src != 1 else ''} — {src} → {dst}"
            detail = f"New stargazer{'s' if len(new) != 1 else ''}: {names}{extra}" if names else ""
            return {"state": "ok", "kind": kind, "title": title, "detail": detail}
        title = f"👥 Discord members — {src} → {dst}"
        detail = f"{dst - src:+d} member{'s' if abs(dst - src) != 1 else ''} since the last check"
        return {"state": "ok", "kind": kind, "title": title, "detail": detail}
    except Exception as exc:  # never raise to the ingest endpoint
        return {"state": "error", "error": f"invalid change: {exc}"}


def cards_state(store: CardStore | None = None) -> dict:
    """Queryable card state for /chat/state. Never raises."""
    try:
        store = store or CardStore()
        return {"cards": store.current(), "count": store.count()}
    except Exception:
        return {"cards": [], "count": 0}
