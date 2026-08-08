"""Workflow schedules — auto-replay saved workflows on an interval.

The store is pure JSON (`data/schedules.json`); the runner lives in the app
(a 30s QTimer tick checks `due()` and replays via the Control API backend).
Pure helpers are unit-testable without Qt.
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

SCHEDULES_PATH = DATA_DIR / "schedules.json"

# Minutes → menu label (0 = disabled).
INTERVALS = {
    0: "Off",
    15: "Every 15 min",
    30: "Every 30 min",
    60: "Hourly",
    360: "Every 6 hours",
    1440: "Daily",
}


def next_run_at(every_min: int, from_ts: float | None = None) -> float:
    """When a schedule with this interval next fires."""
    return (from_ts if from_ts is not None else time.time()) + every_min * 60


def is_due(entry: dict, now: float | None = None) -> bool:
    """True when an enabled schedule's next_run has passed."""
    every = int(entry.get("every_min", 0) or 0)
    if every <= 0:
        return False
    return (now if now is not None else time.time()) >= float(entry.get("next_run", 0) or 0)


class ScheduleStore:
    """name → {"every_min", "next_run", "last_run", "last_result"} persisted as JSON."""

    def __init__(self, path: Path | None = None):
        self._path = Path(path) if path is not None else SCHEDULES_PATH

    def _read(self) -> dict:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8-sig"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write(self, data: dict) -> None:
        with contextlib.suppress(Exception):
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            tmp.replace(self._path)

    def list(self) -> list[dict]:
        rows = []
        for name, entry in sorted(self._read().items()):
            every = int(entry.get("every_min", 0) or 0)
            rows.append(
                {
                    "name": name,
                    "every_min": every,
                    "label": INTERVALS.get(every, INTERVALS[0]),
                    "next_run": float(entry.get("next_run", 0) or 0),
                    "last_run": float(entry.get("last_run", 0) or 0),
                    "last_result": str(entry.get("last_result", "") or ""),
                }
            )
        return rows

    def set(self, name: str, every_min: int) -> dict:
        """Set (or disable, every_min=0) a workflow's schedule. Returns the entry."""
        data = self._read()
        every_min = int(every_min)
        if every_min <= 0:
            data.pop(name, None)
            entry = {"every_min": 0}
        else:
            entry = dict(data.get(name, {}))
            entry["every_min"] = every_min
            entry["next_run"] = next_run_at(every_min)
            data[name] = entry
        self._write(data)
        return entry

    def due(self, now: float | None = None) -> list[str]:
        return [name for name, entry in self._read().items() if is_due(entry, now)]

    def mark_run(self, name: str, result: str, now: float | None = None) -> None:
        """Record an execution and push next_run forward by the interval."""
        now = now if now is not None else time.time()
        data = self._read()
        entry = data.get(name)
        if entry is None:
            return
        entry["last_run"] = now
        entry["last_result"] = result[:200]
        entry["next_run"] = next_run_at(int(entry.get("every_min", 0) or 0), now)
        data[name] = entry
        self._write(data)
