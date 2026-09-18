"""Circuit breaker for agent loops — stops a runaway agent.

Build with LuckyD: agents that fail every tool call N turns in a row are
not "trying harder", they are stuck. This breaker counts consecutive
all-tool-failure turns and trips, so your loop can stop, summarize, and
ask for help instead of burning tokens in a hole.

Extracted from LuckyD's agent loop (stops after 4 consecutive
all-tool-failure turns by default).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CircuitBreaker:
    """Trips after ``max_consecutive_failures`` fully-failed turns."""

    max_consecutive_failures: int = 4
    consecutive_failures: int = 0
    tripped: bool = False
    history: list[dict] = field(default_factory=list)

    def record_turn(self, *, tools_called: int, tools_failed: int) -> bool:
        """Record one agent turn. Returns True if the breaker tripped."""
        if tools_called > 0 and tools_failed >= tools_called:
            self.consecutive_failures += 1
        else:
            self.consecutive_failures = 0
        self.history.append(
            {
                "tools_called": tools_called,
                "tools_failed": tools_failed,
                "consecutive_failures": self.consecutive_failures,
            }
        )
        if self.consecutive_failures >= self.max_consecutive_failures:
            self.tripped = True
        return self.tripped

    def reset(self) -> None:
        self.consecutive_failures = 0
        self.tripped = False

    def status(self) -> dict:
        return {
            "tripped": self.tripped,
            "consecutive_failures": self.consecutive_failures,
            "max_consecutive_failures": self.max_consecutive_failures,
            "turns_recorded": len(self.history),
        }
