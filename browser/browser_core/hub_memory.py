"""Shared Hub memory — one handoff note every surface can read and write
(Hub slice 3).

Browser chat, Hub chat, Claw task receipts, terminal/LuckyD Code and the
puck each kept their own thread; nothing carried context between them.
This is the first shared piece: a tiny append-only JSON log owned by the
browser (same DATA_DIR pattern as the scheduler), exposed on /chat/state
(latest note + count) and POST /chat/handoff. Any surface that can reach
the control server can read it — the Chat page shows the latest note and
posts Claw receipts into it automatically.

Rules, same fail-quiet style as the rest of the Hub surface: a corrupt
or missing file reads as empty, writes are atomic (tmp + replace), and
nothing here ever raises to a caller.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from pathlib import Path

if getattr(sys, "frozen", False):
    DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "LuckyDBrowser"
else:
    DATA_DIR = Path(__file__).resolve().parent.parent / "data"

HANDOFF_PATH = DATA_DIR / "hub_handoff.json"

#: Entries are capped so the file can never grow without bound.
MAX_ENTRIES = 50
#: A handoff note is a short note, not a document.
MAX_TEXT_LEN = 2000


class HandoffStore:
    """Append-only handoff log persisted as JSON."""

    def __init__(self, path: Path | None = None):
        self._path = Path(path) if path is not None else HANDOFF_PATH

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

    def append(self, source: str, text: str) -> dict:
        """Append one entry. Returns {"state": "ok", "entry": ...} or
        {"state": "error", "error": ...} for empty/oversize text."""
        src = str(source or "").strip() or "chat"
        body = str(text or "").strip()
        if not body:
            return {"state": "error", "error": "text required"}
        if len(body) > MAX_TEXT_LEN:
            return {"state": "error", "error": "text too long (max 2000 chars)"}
        entries = self._read()
        entry = {"ts": time.time(), "source": src[:40], "text": body}
        entries.append(entry)
        self._write(entries[-MAX_ENTRIES:])
        return {"state": "ok", "entry": entry}

    def latest(self) -> dict | None:
        entries = self._read()
        return entries[-1] if entries else None

    def list(self, limit: int = 10) -> list:
        entries = self._read()
        try:
            n = max(0, int(limit))
        except Exception:
            n = 10
        return entries[-n:] if n else []

    def count(self) -> int:
        return len(self._read())


def handoff_state(store: HandoffStore | None = None) -> dict:
    """Queryable handoff state for /chat/state. Never raises."""
    try:
        store = store or HandoffStore()
        return {"latest": store.latest(), "count": store.count()}
    except Exception:
        return {"latest": None, "count": 0}
