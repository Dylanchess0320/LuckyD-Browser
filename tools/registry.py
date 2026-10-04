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

    def canonical_name(self, name: str) -> str:
        """Resolve any tool spelling to the canonical registered tool name.

        The model may type an alias (``Shell`` for ``Bash``) or a different
        case (``bash``). The executor resolves these through :meth:`get`,
        so every policy check (permissions, scopes, risk, audit) must
        resolve the same way — otherwise an alternate spelling silently
        bypasses approval. Unknown names are returned unchanged so callers
        keep applying default policy to them.
        """
        key = (name or "").lower()
        tool = self._tools.get(key)
        if tool is not None:
            return tool.name
        canon_key = self._aliases.get(key)
        if canon_key is not None:
            tool = self._tools.get(canon_key)
            if tool is not None:
                return tool.name
        return name

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


def canonical_name(name: str) -> str:
    """Resolve any tool spelling to the canonical registered tool name.

    Module-level convenience wrapper around the global registry; see
    :meth:`ToolRegistry.canonical_name`.
    """
    return registry.canonical_name(name)
