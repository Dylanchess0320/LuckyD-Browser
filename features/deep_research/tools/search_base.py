"""Abstract LLM + search provider interfaces so the graph is backend-agnostic."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel

from ..schemas import EvidenceCard

T = TypeVar("T", bound=BaseModel)


class LLMProvider(ABC):
    """A minimal chat-completion + structured-output + grounding interface."""

    @abstractmethod
    def structured(
        self,
        role: str,
        system: str,
        user: str,
        schema: type[T],
        temperature: float | None = None,
    ) -> T:
        """Return a Pydantic object validated against `schema`."""

    @abstractmethod
    def text(
        self,
        role: str,
        system: str,
        user: str,
        temperature: float | None = None,
    ) -> str:
        """Return plain text."""

    @abstractmethod
    def grounded(
        self,
        role: str,
        system: str,
        user: str,
    ) -> tuple[str, list[EvidenceCard]]:
        """Run with web-search grounding. Returns (answer_text, evidence_cards)."""


class SearchProvider(ABC):
    """Fallback standalone search (used when not using Gemini grounding)."""

    @abstractmethod
    def search(self, query: str, max_results: int = 8) -> list[EvidenceCard]: ...
