"""Turn-by-turn trajectory recorder (opt-in JSONL audit trail).

Enable with ``CODING_AGENT_TRAJECTORY=1``. Each :meth:`run` call then
appends one ``<conversation>_<timestamp>.jsonl`` file under
``data/trajectories/`` with the run's typed events (turns, model
responses, tool calls, approvals, errors). Token-chunk and debug
events are excluded to keep files small. Attaching chains the agent's
existing ``on_event`` callback, so recorders compose with REPL/ACP
consumers instead of replacing them.
"""

from __future__ import annotations

import contextlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import DATA_DIR

from .types import AgentEventType

_TRUTHY = {"1", "true", "yes"}

#: High-volume event types never recorded.
SKIP_EVENT_TYPES = frozenset(
    {
        AgentEventType.MODEL_CHUNK.value,
        AgentEventType.MODEL_THINK_CHUNK.value,
        AgentEventType.DEBUG.value,
    }
)


class TrajectoryRecorder:
    """Append-only JSONL recorder attached to one agent run."""

    def __init__(
        self,
        path: str | Path,
        max_events: int = 2000,
        max_payload_chars: int = 2000,
    ):
        self.path = Path(path)
        self.max_events = max_events
        self.max_payload_chars = max_payload_chars
        self._count = 0
        self._closed = False
        self._agent: Any = None
        self._previous: Any = None
        self._chained: Any = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")

    @property
    def closed(self) -> bool:
        return self._closed

    @classmethod
    def attach_if_enabled(cls, agent: Any) -> TrajectoryRecorder | None:
        """Attach a recorder for this run when the env flag is on."""
        if os.environ.get("CODING_AGENT_TRAJECTORY", "").lower() not in _TRUTHY:
            return None
        stale = getattr(agent, "_trajectory_recorder", None)
        if stale is not None:
            with contextlib.suppress(Exception):
                stale.close()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        conv = str(getattr(agent, "conversation_id", "noconv"))
        rec = cls.attach(agent, Path(DATA_DIR) / "trajectories" / f"{conv}_{stamp}.jsonl")
        agent._trajectory_recorder = rec
        return rec

    @classmethod
    def attach(cls, agent: Any, path: str | Path) -> TrajectoryRecorder:
        """Attach unconditionally (tests and the eval harness use this)."""
        rec = cls(path)
        rec._agent = agent
        prev = agent.callbacks.on_event
        rec._previous = prev

        def _chained(event: Any) -> None:
            if prev is not None:
                with contextlib.suppress(Exception):
                    prev(event)
            rec.record(event)
            etype = getattr(getattr(event, "type", None), "value", "")
            if etype == AgentEventType.SESSION_END.value:
                rec.close()

        rec._chained = _chained
        agent.callbacks.on_event = _chained
        return rec

    def record(self, event: Any) -> None:
        """Append one event (no-op when closed, capped, or skipped)."""
        if self._closed or self._count >= self.max_events:
            return
        try:
            etype = getattr(getattr(event, "type", None), "value", "unknown")
            if etype in SKIP_EVENT_TYPES:
                return
            payload = getattr(event, "payload", {}) or {}
            raw = json.dumps(payload, default=str)
            kept: Any
            if len(raw) > self.max_payload_chars:
                kept = raw[: self.max_payload_chars] + "…[truncated]"
            else:
                kept = json.loads(raw)
            row = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "type": etype,
                "turn": getattr(event, "turn", 0),
                "payload": kept,
            }
            self._fh.write(json.dumps(row, default=str) + "\n")
            self._fh.flush()
            self._count += 1
        except Exception:
            pass

    def close(self) -> None:
        """Flush, close, and detach (restores the previous callback)."""
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(Exception):
            self._fh.close()
        agent, prev, chained = self._agent, self._previous, self._chained
        self._agent = None
        if agent is not None:
            with contextlib.suppress(Exception):
                if getattr(agent.callbacks, "on_event", None) is chained:
                    agent.callbacks.on_event = prev
                if getattr(agent, "_trajectory_recorder", None) is self:
                    agent._trajectory_recorder = None
