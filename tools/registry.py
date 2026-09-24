"""
Tool registry with auto-registration, alias resolution, and name flexibility.
Tools register themselves by importing this module and calling register().
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import ToolBase


class ToolRegistry:
    """Global registry for all tool plugins."""

    def __init__(self):
        self._tools: dict[str, ToolBase] = {}
        self._aliases: dict[str, str] = {}

    def register(self, tool: ToolBase):
        name = tool.name.lower()
        self._tools[name] = tool
        for alias in tool.aliases:
            self._aliases[alias.lower()] = name

    def get(self, name: str) -> ToolBase | None:
        key = name.lower()
        if key in self._tools:
            return self._tools[key]
        if key in self._aliases:
            return self._tools[self._aliases[key]]
        return None

    def list_tools(self) -> list[str]:
        return sorted(self._tools.keys())

    def list_with_descriptions(self) -> list[dict]:
        return [
            {"name": t.name, "description": t.description, "aliases": t.aliases}
            for t in self._tools.values()
        ]

    def openai_tools(self, names: list[str] | None = None) -> list[dict]:
        """Function schemas for all tools, or just ``names`` when given."""
        if names is None:
            return [t.to_openai_schema() for t in self._tools.values()]
        return [
            self._tools[name].to_openai_schema() for name in sorted(names) if name in self._tools
        ]

    def prompt_description(self) -> str:
        lines = []
        for name in sorted(self._tools.keys()):
            t = self._tools[name]
            aliases = f" (aliases: {', '.join(t.aliases)})" if t.aliases else ""
            lines.append(f"- **{t.name}**{aliases}: {t.description}")
        return "\n".join(lines)

    @property
    def count(self) -> int:
        return len(self._tools)


# Global singleton
registry = ToolRegistry()


def register_tool(tool: ToolBase):
    registry.register(tool)
