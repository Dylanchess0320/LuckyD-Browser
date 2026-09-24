"""File-relevance retrieval: SmartContextEngine as a model-invokable tool."""

from __future__ import annotations

from config import PROJECT_DIR
from core.context_manager import estimate_tokens
from core.smart_context import SmartContextEngine

from .base import ToolBase, ToolOutput
from .registry import register_tool


class FindRelevantFilesTool(ToolBase):
    name = "FindRelevantFiles"
    description = (
        "Rank project files by relevance to a task and return the best ones "
        "within a token budget. Use to orient in unfamiliar code before "
        "reading files individually."
    )
    aliases = ["FindFiles", "RelevantFiles"]
    parameters = {
        "query": {
            "type": "string",
            "description": "Task description to rank files against",
        },
        "root": {
            "type": "string",
            "description": "Project root to search (defaults to the current project)",
        },
        "max_tokens": {
            "type": "integer",
            "description": "Token budget for packed file contents (default 8000)",
        },
    }

    async def execute(self, query: str = "", root: str = "", max_tokens: int = 8000) -> ToolOutput:
        project_root = (root or "").strip() or str(PROJECT_DIR)
        try:
            budget = max(500, min(int(max_tokens or 8000), 32000))
        except (TypeError, ValueError):
            budget = 8000
        try:
            packed = SmartContextEngine(root=project_root).build_context(
                task_query=query or "", max_tokens=budget
            )
        except Exception as exc:
            return ToolOutput(text=f"Error: relevance ranking failed: {exc}", error=True)
        if not packed:
            return ToolOutput(
                text=f"No relevant files found under {project_root}.",
                title="Relevant Files",
                metadata={"root": project_root, "files": [], "total_tokens": 0},
            )
        chunks = [
            f"--- {path} (score {score:.2f}) ---\n{content}" for path, content, score in packed
        ]
        total = sum(estimate_tokens(c) for _, c, _ in packed)
        return ToolOutput(
            text="\n\n".join(chunks),
            title=f"{len(packed)} relevant files",
            metadata={
                "root": project_root,
                "files": [p for p, _, _ in packed],
                "total_tokens": total,
            },
        )


register_tool(FindRelevantFilesTool())
