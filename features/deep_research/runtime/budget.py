"""Central budget tracker — guards against runaway cost and latency.

Every LLM call, grounded search, and URL fetch checks in here. If a limit is
hit, ``check()`` raises ``BudgetExhausted`` so workers can stop gracefully with
``stop_reason=\"budget_exhausted\"`` instead of crashing mid-research.
"""

from __future__ import annotations

import threading
import time

from ..config import settings
from ..schemas import BudgetUsage


class BudgetExhausted(Exception):  # noqa: N818 - public name kept from the original swarm
    """Raised when a configured budget limit is reached."""


class BudgetTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._llm_calls = 0
        self._search_calls = 0
        self._fetched_urls = 0
        self._start = time.time()
        self._exhausted = False
        self._reason = ""

    @property
    def limits(self) -> dict[str, int | None]:
        return {
            "max_llm_calls": settings.max_llm_calls or None,
            "max_search_queries": settings.max_search_queries or None,
            "max_fetches": settings.max_fetches or None,
            "max_seconds": settings.max_seconds or None,
        }

    def _mark_exhausted(self, reason: str) -> None:
        self._exhausted = True
        self._reason = reason

    def check(self) -> None:
        """Raise BudgetExhausted if any limit has been reached."""
        if self._exhausted:
            raise BudgetExhausted(self._reason)
        if settings.max_llm_calls and self._llm_calls >= settings.max_llm_calls:
            self._mark_exhausted("max_llm_calls")
            raise BudgetExhausted(self._reason)
        if settings.max_search_queries and self._search_calls >= settings.max_search_queries:
            self._mark_exhausted("max_search_queries")
            raise BudgetExhausted(self._reason)
        if settings.max_fetches and self._fetched_urls >= settings.max_fetches:
            self._mark_exhausted("max_fetches")
            raise BudgetExhausted(self._reason)
        if settings.max_seconds and (time.time() - self._start) >= settings.max_seconds:
            self._mark_exhausted("max_seconds")
            raise BudgetExhausted(self._reason)

    def record_llm(self, n: int = 1) -> None:
        # Check BEFORE incrementing so a limit of N allows exactly N calls;
        # the (N+1)th call raises. _exhausted is then sticky for all later ops.
        self.check()
        with self._lock:
            self._llm_calls += n

    def record_search(self, n: int = 1) -> None:
        self.check()
        with self._lock:
            self._search_calls += n

    def record_fetch(self, n: int = 1) -> None:
        self.check()
        with self._lock:
            self._fetched_urls += n

    def usage(self) -> BudgetUsage:
        with self._lock:
            return BudgetUsage(
                llm_calls=self._llm_calls,
                search_calls=self._search_calls,
                fetched_urls=self._fetched_urls,
                elapsed_seconds=round(time.time() - self._start, 1),
                limits=self.limits,
                exhausted=self._exhausted,
                reason=self._reason,
            )


# Module-level singleton shared across the whole graph run.
_budget: BudgetTracker | None = None
_lock = threading.Lock()


def get_budget() -> BudgetTracker:
    global _budget
    with _lock:
        if _budget is None:
            _budget = BudgetTracker()
        return _budget


def reset_budget() -> BudgetTracker:
    global _budget
    with _lock:
        _budget = BudgetTracker()
        return _budget
