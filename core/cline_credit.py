"""Cline credit-exhaustion state — honest provider UX after an HTTP 402.

When the Cline gateway (``api.cline.bot``) answers with HTTP 402, the Cline
Credits balance is exhausted: every further usage-billed call fails the same
way until the user tops up. Rather than keep offering Cline as a ready
default, LuckyD records that signal on disk and steers auto-selection away
from Cline until the marker expires or the user clears it.

State file: ``~/.luckyd/cline_credit_state.json`` — ``{"exhausted_at": <unix
epoch seconds>, "reason": <human text>}``. Valid for 24 hours (TTL); older
markers are ignored. There is deliberately NO Cline balance API behind this:
the 402 response is the only trigger, and
``lucky-code providers --clear-credit-state`` clears it by hand after topping
up.

``LUCKYD_CLINE_CREDIT_STATE`` overrides the file location. It exists so the
test suite stays hermetic (a developer's real marker must never flip suite
results); end users should use the ``--clear-credit-state`` command instead.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

__all__ = [
    "CREDIT_STATE_TTL_SEC",
    "ClineCreditState",
    "clear_cline_credit_state",
    "credit_state_path",
    "credit_ttl_remaining",
    "is_cline_credit_exhausted",
    "read_cline_credit_state",
    "record_cline_credit_exhausted",
]

#: How long a 402 marker steers auto-selection away from Cline (24 hours).
CREDIT_STATE_TTL_SEC = 24 * 60 * 60

#: Env var overriding the state file location (test seam; see module docstring).
_CREDIT_STATE_ENV = "LUCKYD_CLINE_CREDIT_STATE"

_STATE_DIRNAME = ".luckyd"
_STATE_FILENAME = "cline_credit_state.json"


class ClineCreditState:
    """A recorded credit-exhaustion marker (timestamp + reason)."""

    __slots__ = ("exhausted_at", "reason")

    def __init__(self, exhausted_at: float, reason: str) -> None:
        self.exhausted_at = exhausted_at
        self.reason = reason


def credit_state_path() -> Path:
    """Location of the marker file (``~/.luckyd/cline_credit_state.json``)."""
    override = (os.environ.get(_CREDIT_STATE_ENV, "") or "").strip()
    if override:
        return Path(override)
    return Path.home() / _STATE_DIRNAME / _STATE_FILENAME


def record_cline_credit_exhausted(reason: str, *, now: float | None = None) -> bool:
    """Persist a fresh 402 marker. Never raises — returns False on failure."""
    try:
        path = credit_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "exhausted_at": now if now is not None else time.time(),
            "reason": (reason or "HTTP 402 from the Cline gateway").strip()[:300],
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        return True
    except Exception:
        return False


def read_cline_credit_state() -> ClineCreditState | None:
    """Return the stored marker, or None when missing/unreadable/malformed."""
    try:
        raw = credit_state_path().read_text(encoding="utf-8")
    except Exception:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    try:
        exhausted_at = float(data["exhausted_at"])
    except (KeyError, TypeError, ValueError):
        return None
    reason = data.get("reason", "")
    return ClineCreditState(exhausted_at, str(reason or ""))


def is_cline_credit_exhausted(*, now: float | None = None) -> bool:
    """True while a stored 402 marker is still inside its 24-hour TTL.

    Fails open: a missing, corrupt, or expired marker means "not exhausted",
    so provider selection can never break because of this file.
    """
    state = read_cline_credit_state()
    if state is None:
        return False
    current = now if now is not None else time.time()
    return (current - state.exhausted_at) < CREDIT_STATE_TTL_SEC


def credit_ttl_remaining(*, now: float | None = None) -> float:
    """Seconds until a valid 402 marker expires (10.5 health snapshot).

    Returns 0.0 when no marker is stored, it is expired, or it is
    unreadable — so the health snapshot can carry "how much longer Cline
    stays steered-away" without a second file read.
    """
    state = read_cline_credit_state()
    if state is None:
        return 0.0
    current = now if now is not None else time.time()
    return max(0.0, CREDIT_STATE_TTL_SEC - (current - state.exhausted_at))


def clear_cline_credit_state() -> bool:
    """Delete the marker. True when a file was removed, False otherwise."""
    try:
        credit_state_path().unlink()
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False
