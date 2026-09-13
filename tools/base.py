"""
Plugin tool system — every tool is a class with JSON Schema params,
auto-registration, alias resolution, and rich output.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolOutput:
    """Rich structured output from a tool."""

    text: str = ""
    title: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    images: list[str] = field(default_factory=list)
    error: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "output": self.text,
            "title": self.title,
            "metadata": self.metadata,
            "images": self.images,
            "is_error": self.error,
        }


class ToolBase(ABC):
    """Base class for all agent tools."""

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}  # JSON Schema for params
    aliases: list[str] = []  # Alternative names
    permission_level = "NORMAL"  # ALWAYS_ALLOW, NORMAL, REQUIRES_APPROVAL, BLOCKED
    tracks_files: bool = False  # If True, checkpoint snapshots are taken
    timeout_sec: float | None = None  # Per-tool timeout override (None = use global default)

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolOutput: ...

    def to_openai_schema(self) -> dict[str, object]:
        clean_props: dict[str, object] = {}
        for k, v in self.parameters.items():
            if isinstance(v, dict):
                clean_props[k] = {pk: pv for pk, pv in v.items() if pk != "required"}
            else:
                clean_props[k] = v
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": clean_props,
                    "required": [
                        k
                        for k, v in self.parameters.items()
                        if isinstance(v, dict) and v.get("required", False)
                    ],
                },
            },
        }

    def schema_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "aliases": self.aliases,
        }
