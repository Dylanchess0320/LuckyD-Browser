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
    "LAST_WORKING_TTL_SEC",
    "LastWorkingState",
    "clear_last_working",
    "clear_last_working_model",
    "is_last_working_fresh",
    "last_working_age_label",
    "last_working_pair",
    "last_working_path",
    "last_working_summary",
    "read_last_working",
    "read_last_working_model",
    "record_last_working",
    "record_last_working_model",
]

#: Env var overriding the state file location (test seam; see module docstring).
_LAST_WORKING_ENV = "LUCKYD_LAST_WORKING_STATE"

_STATE_DIRNAME = ".luckyd"
_STATE_FILENAME = "last_working_model.json"

#: A record older than this is "stale" — still shown, but no longer preferred
#: by rotation or reported as fresh. 24h keeps yesterday's evidence relevant
#: without letting a week-old success steer today's picks.
LAST_WORKING_TTL_SEC = 86400


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


def is_last_working_fresh(*, now: float | None = None) -> bool:
    """True when a record exists and is younger than ``LAST_WORKING_TTL_SEC``."""
    state = read_last_working()
    if state is None:
        return False
    current = now if now is not None else time.time()
    return max(0.0, current - state.worked_at) <= LAST_WORKING_TTL_SEC


def last_working_pair(*, now: float | None = None) -> tuple[str, str] | None:
    """``(provider, model)`` of the last-known-working pair, or None.

    Returns None when there is no record or the record is stale (older than
    ``LAST_WORKING_TTL_SEC``) — rotation must not prefer ancient evidence.
    """
    if not is_last_working_fresh(now=now):
        return None
    state = read_last_working()
    if state is None:
        return None
    return (state.provider, state.model)


def last_working_summary(*, now: float | None = None) -> str | None:
    """One-line ``"provider/model (worked X ago)"``; None when no record."""
    state = read_last_working()
    if state is None:
        return None
    label = last_working_age_label(now=now)
    return (
        f"{state.provider}/{state.model} ({label})" if label else f"{state.provider}/{state.model}"
    )


# ── _model-suffixed aliases ──────────────────────────────────────────────
# The HQ/tests surface historically used the explicit ``*_model`` names;
# they are the same functions, kept so both spellings work.
record_last_working_model = record_last_working
read_last_working_model = read_last_working
clear_last_working_model = clear_last_working
