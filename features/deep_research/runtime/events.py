"""Run events emitted by the graph nodes for live UI + artifacts."""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class RunEvent:
    node: str
    message: str
    level: Literal["info", "ok", "warn", "err"] = "info"
    ts: float = field(default_factory=time.time)


class EventEmitter:
    """A tiny pub-sub so the TUI and run store both observe node events."""

    def __init__(self) -> None:
        self._subs: list = []

    def subscribe(self, fn) -> None:
        self._subs.append(fn)

    def emit(self, event: RunEvent) -> None:
        for fn in self._subs:
            with contextlib.suppress(Exception):
                fn(event)
