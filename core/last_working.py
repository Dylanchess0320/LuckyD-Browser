"""Last-known-working model memory — "what actually answered" (10.5).

Every successful LLM call records its ``(provider, model)`` pair here so the
next surface — the REPL banner, the HQ Models view, the next rotation — can
prefer and display the pair that provably worked ("worked 2h ago") instead
of guessing.

State file: ``~/.luckyd/last_working_model.json`` — ``{"provider": ...,
"model": ..., "worked_at": <unix epoch seconds>}``. Same pattern as
``core/cline_credit.py``: the file is advisory only, every reader fails
open, and writers never raise.

``LUCKYD_LAST_WORKING_STATE`` overrides the file location. It exists so the
test suite stays hermetic (a developer's real record must never flip suite
results); end users never need it.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

__all__ = [
    "LastWorkingState",
    "clear_last_working",
    "last_working_age_label",
    "last_working_path",
    "read_last_working",
    "record_last_working",
]

#: Env var overriding the state file location (test seam; see module docstring).
_LAST_WORKING_ENV = "LUCKYD_LAST_WORKING_STATE"

_STATE_DIRNAME = ".luckyd"
_STATE_FILENAME = "last_working_model.json"


class LastWorkingState:
    """A recorded successful ``(provider, model)`` pair + timestamp."""

    __slots__ = ("model", "provider", "worked_at")

    def __init__(self, provider: str, model: str, worked_at: float) -> None:
        self.provider = provider
        self.model = model
        self.worked_at = worked_at


def last_working_path() -> Path:
    """Location of the record file (``~/.luckyd/last_working_model.json``)."""
    override = (os.environ.get(_LAST_WORKING_ENV, "") or "").strip()
    if override:
        return Path(override)
    return Path.home() / _STATE_DIRNAME / _STATE_FILENAME


def record_last_working(provider: str, model: str, *, now: float | None = None) -> bool:
    """Persist a successful pair. Never raises — returns False on failure."""
    provider = (provider or "").strip().lower()
    model = (model or "").strip()
    if not provider or not model:
        return False
    try:
        path = last_working_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "provider": provider[:120],
            "model": model[:300],
            "worked_at": now if now is not None else time.time(),
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        return True
    except Exception:
        return False


def read_last_working() -> LastWorkingState | None:
    """Return the stored record, or None when missing/unreadable/malformed."""
    try:
        raw = last_working_path().read_text(encoding="utf-8")
    except Exception:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    try:
        worked_at = float(data["worked_at"])
    except (KeyError, TypeError, ValueError):
        return None
    provider = str(data.get("provider", "") or "").strip().lower()
    model = str(data.get("model", "") or "").strip()
    if not provider or not model:
        return None
    return LastWorkingState(provider, model, worked_at)


def last_working_age_label(*, now: float | None = None) -> str | None:
    """Human age of the record ("worked 2h ago"); None when no record."""
    state = read_last_working()
    if state is None:
        return None
    current = now if now is not None else time.time()
    age = max(0.0, current - state.worked_at)
    if age < 60:
        return "worked just now"
    if age < 3600:
        return f"worked {int(age // 60)}m ago"
    if age < 48 * 3600:
        return f"worked {int(age // 3600)}h ago"
    return f"worked {int(age // 86400)}d ago"


def clear_last_working() -> bool:
    """Delete the record. True when a file was removed, False otherwise."""
    try:
        last_working_path().unlink()
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False
