"""
Memory tools: remember, recall, forget, search, context.
Tightly integrated with the memory graph for persistent cross-session knowledge.
"""

from __future__ import annotations

from memory.store import get_memory

from .base import ToolBase, ToolOutput
from .registry import register_tool


class MemoryRemember(ToolBase):
    name = "MemoryRemember"
    description = "Store a fact, preference, or piece of knowledge for future sessions."
    aliases = ["Remember", "Memorize", "SaveMemory"]
    parameters = {
        "content": {"type": "string", "description": "The fact or knowledge to remember"},
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Tags for categorization",
        },
        "alias": {"type": "string", "description": "Short alias/key for lookup"},
        "source": {"type": "string", "description": "Where this knowledge came from"},
    }

    async def execute(
        self, content: str, tags: list[str] | None = None, alias: str = "", source: str = ""
    ) -> ToolOutput:
        mem = get_memory()
        mem_id = mem.add(content, tags=tags or [], alias=alias, source=source)
        return ToolOutput(
            text=f"Stored as {mem_id[:8]}...",
            title="Remembered",
            metadata={"memory_id": mem_id, "tags": tags or []},
        )


class MemoryRecall(ToolBase):
    name = "MemoryRecall"
    description = "Search your memory for relevant knowledge by text query."
    aliases = ["Recall", "RememberSearch", "FindMemory"]
    parameters = {
        "query": {"type": "string", "description": "What to search for"},
        "limit": {"type": "integer", "description": "Max results (default: 5)"},
    }

    async def execute(self, query: str, limit: int = 5) -> ToolOutput:
        mem = get_memory()
        context = mem.get_context(query, limit=limit)
        return ToolOutput(
            text=context,
            title=f"Memory: {query}",
            metadata={"query": query},
        )


class MemoryForget(ToolBase):
    name = "MemoryForget"
    description = "Delete a specific memory by its ID."
    aliases = ["Forget", "DeleteMemory"]
    parameters = {
        "memory_id": {"type": "string", "description": "The memory ID (or first 8 chars of it)"},
    }

    async def execute(self, memory_id: str) -> ToolOutput:
        # An empty id matches every stored id via str.startswith("") — reject
        # it instead of deleting the first memory in the list.
        if not memory_id or not memory_id.strip():
            return ToolOutput(text="memory_id must not be empty.", error=True)
        mem = get_memory()
        # Allow partial ID match
        target = None
        if memory_id in mem.graph.memories:
            target = memory_id
        else:
            for mid in mem.graph.memories:
                if mid.startswith(memory_id):
                    target = mid
                    break
        if not target:
            return ToolOutput(
                text=f"No memory found with ID starting with '{memory_id}'", error=True
            )

        ok = mem.delete(target)
        if ok:
            return ToolOutput(text=f"Deleted memory {target[:8]}...", title="Forgotten")
        return ToolOutput(text="Failed to delete.", error=True)


class MemorySummary(ToolBase):
    name = "MemorySummary"
    description = "Show a summary of your memory: count, clusters, recent additions."
    aliases = ["MemoryStats", "WhatDoYouKnow"]
    parameters = {}

    async def execute(self) -> ToolOutput:
        mem = get_memory()
        summary = mem.summarize()
        return ToolOutput(text=summary, title="Memory Summary")


class MemoryClear(ToolBase):
    name = "MemoryClear"
    description = "Clear all memories. WARNING: this is destructive."
    aliases = ["ClearMemory", "WipeMemory"]
    parameters = {}

    async def execute(self) -> ToolOutput:
        mem = get_memory()
        mem.clear()
        return ToolOutput(text="All memories cleared.", title="Memory Wiped")


for cls in [MemoryRemember, MemoryRecall, MemoryForget, MemorySummary, MemoryClear]:
    register_tool(cls())
