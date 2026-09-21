"""Active objective tracking for the coding agent (LuckyD 9.8).

Complements minimax-code's ``/goal`` idea with a native implementation:
:class:`Goal` holds the objective text plus an optional token budget and a
paused flag; :class:`GoalStore` owns one goal and serializes it into the
session autosave payload so goals survive ``--continue``/``/resume``.
"""

from __future__ import annotations

import contextlib
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class Goal:
    """One active objective with an optional token budget."""

    text: str = ""
    budget: int | None = None
    paused: bool = False
    spent: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Goal | None:
        """Rebuild a goal from a session payload dict (None when absent)."""
        if not data:
            return None
        try:
            text = str(data.get("text", "") or "")
            if not text:
                return None
            budget = data.get("budget")
            spent = data.get("spent", 0)
            return cls(
                text=text,
                budget=int(budget) if budget is not None else None,
                paused=bool(data.get("paused", False)),
                spent=int(spent or 0),
            )
        except (TypeError, ValueError):
            return None

    def status_line(self) -> str:
        """One-line human-readable status for the REPL / ACP."""
        state = "paused" if self.paused else "active"
        if self.budget is not None:
            return (
                f"Goal ({state}, budget {_format_budget(self.budget)},"
                f" spent {self.spent}): {self.text}"
            )
        return f"Goal ({state}): {self.text}"

    def remaining(self) -> int | None:
        """Tokens left under the budget, or None when unbounded."""
        if self.budget is None:
            return None
        return max(0, self.budget - self.spent)


def _format_budget(budget: int) -> str:
    if budget >= 1_000_000 and budget % 1_000_000 == 0:
        return f"{budget // 1_000_000}M"
    if budget >= 1_000 and budget % 1_000 == 0:
        return f"{budget // 1_000}K"
    return str(budget)


def parse_budget(raw: str) -> int | None:
    """Parse ``50K`` / ``2M`` / ``1000`` / ``clear`` budget values.

    Returns the budget in tokens, or None for ``clear`` (remove budget).
    Raises :class:`ValueError` on malformed input.
    """
    text = (raw or "").strip()
    if text.lower() == "clear":
        return None
    if not text:
        raise ValueError("empty budget")
    upper = text.upper()
    multiplier = 1
    if upper.endswith("K"):
        multiplier = 1_000
        upper = upper[:-1]
    elif upper.endswith("M"):
        multiplier = 1_000_000
        upper = upper[:-1]
    try:
        value = float(upper.strip())
    except ValueError:
        raise ValueError(f"invalid budget: {raw!r} (try 50K, 2M, or clear)") from None
    if value < 0:
        raise ValueError(f"invalid budget: {raw!r} (must be >= 0)")
    return int(value * multiplier)


def parse_goal_command(args: str) -> tuple[str, str]:
    """Split a ``/goal ...`` line into (action, value).

    Actions: ``set`` (free text), ``budget``, ``pause``, ``resume``,
    ``edit``, ``clear``, ``status``. Bare ``/goal`` reports status.
    """
    rest = (args or "").strip()
    if not rest:
        return ("status", "")
    low = rest.lower()
    if low in ("pause", "resume", "clear", "status"):
        return (low, "")
    if low.startswith("budget"):
        tail = rest[len("budget") :].strip()
        if tail.startswith("="):
            return ("budget", tail[1:].strip())
        if not tail:
            return ("budget", "")
        return ("budget", tail)
    if low.startswith("edit"):
        tail = rest[len("edit") :].strip()
        return ("edit", tail)
    return ("set", rest)


@dataclass
class GoalStore:
    """Owns one optional Goal; serialize via to_dict."""

    goal: Goal | None = None

    def set(self, text: str) -> Goal:
        self.goal = Goal(text=text.strip())
        return self.goal

    def edit(self, text: str) -> Goal | None:
        if self.goal is None:
            return None
        text = text.strip()
        if text:
            self.goal.text = text
        return self.goal

    def set_budget(self, budget: int | None) -> Goal | None:
        if self.goal is None:
            return None
        self.goal.budget = budget
        return self.goal

    def pause(self) -> Goal | None:
        if self.goal is None:
            return None
        self.goal.paused = True
        return self.goal

    def resume(self) -> Goal | None:
        if self.goal is None:
            return None
        self.goal.paused = False
        return self.goal

    def clear(self) -> None:
        self.goal = None

    def add_spent(self, tokens: int) -> None:
        if self.goal is not None and tokens:
            with contextlib.suppress(TypeError, ValueError):
                self.goal.spent += int(tokens)

    def to_dict(self) -> dict[str, Any] | None:
        return self.goal.to_dict() if self.goal else None

    def restore(self, data: dict[str, Any] | None) -> None:
        self.goal = Goal.from_dict(data)

    def describe(self) -> str:
        if self.goal is None:
            return "No active goal. Set one with /goal <text>."
        return self.goal.status_line()

    def handle_command(self, args: str) -> str:
        """Apply a /goal line; return the REPL response text."""
        action, value = parse_goal_command(args)
        if action == "status":
            return self.describe()
        if action == "set":
            goal = self.set(value)
            return f"Goal set: {goal.text}"
        if action == "budget":
            if self.goal is None:
                return "No active goal. Set one with /goal <text> first."
            if not value:
                if self.goal.budget is None:
                    return "No budget set on the current goal."
                return f"Budget: {_format_budget(self.goal.budget)} (spent {self.goal.spent})."
            try:
                budget = parse_budget(value)
            except ValueError as exc:
                return str(exc)
            self.goal.budget = budget
            if budget is None:
                return "Goal budget cleared."
            return f"Goal budget set to {_format_budget(budget)}."
        if action == "pause":
            if self.goal is None:
                return "No active goal to pause."
            self.goal.paused = True
            return "Goal paused."
        if action == "resume":
            if self.goal is None:
                return "No active goal to resume."
            self.goal.paused = False
            return "Goal resumed."
        if action == "edit":
            if self.goal is None:
                return "No active goal to edit."
            if not value:
                return f"Current goal: {self.goal.text}"
            self.goal.text = value
            return f"Goal updated: {self.goal.text}"
        if action == "clear":
            if self.goal is None:
                return "No active goal."
            self.clear()
            return "Goal cleared."
        return self.describe()


def goal_from_session(record: dict[str, Any] | None) -> Goal | None:
    """Extract a Goal from a session record (or its meta)."""
    if not record:
        return None
    data = record.get("goal")
    if isinstance(data, dict):
        return Goal.from_dict(data)
    meta = record.get("meta")
    if isinstance(meta, dict) and isinstance(meta.get("goal"), dict):
        return Goal.from_dict(meta["goal"])
    return None


def goal_into_session(payload: dict[str, Any], goal: Goal | None) -> dict[str, Any]:
    """Attach goal to a session-save payload (top-level + meta mirror)."""
    data: dict[str, Any] | None = goal.to_dict() if goal else None
    payload["goal"] = data
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        payload["meta"] = meta
    if data is None:
        meta.pop("goal", None)
    else:
        meta["goal"] = data
    return payload
