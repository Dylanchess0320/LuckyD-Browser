"""
Session tools: TodoWrite, TodoRead, ShellHistory.
In-session task tracking and command history review.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from .base import ToolBase, ToolOutput
from .registry import register_tool

# ── shared in-session state ────────────────────────────────────────

_todos: dict[str, dict] = {}
_shell_history: list[dict] = []


def record_shell_command(command: str, exit_code: int, output: str = ""):
    """Called by bash_tool / powershell to record every command run."""
    _shell_history.append(
        {
            "command": command[:500],
            "exit_code": exit_code,
            "output_preview": output[:200],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
    while len(_shell_history) > 200:
        _shell_history.pop(0)


# ── TodoWrite ──────────────────────────────────────────────────────


class TodoWriteTool(ToolBase):
    name = "TodoWrite"
    description = "Update the session todo list — your in-session task checklist. Use to track multi-step work."
    aliases = ["Todos", "Checklist"]
    parameters = {
        "todos": {
            "type": "array",
            "description": "Full todo list (replaces current). Each item: id, content, status (pending|in_progress|completed|cancelled), priority (low|normal|high)",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Unique ID (e.g. 'todo_1')"},
                    "content": {"type": "string", "description": "What needs to be done"},
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "completed", "cancelled"],
                    },
                    "priority": {"type": "string", "enum": ["low", "normal", "high"]},
                },
                "required": ["id", "content", "status"],
            },
        },
    }

    async def execute(self, todos: list) -> ToolOutput:
        global _todos
        new_todos = {}
        for t in todos:
            tid = t.get("id", str(uuid.uuid4())[:8])
            new_todos[tid] = {
                "content": t.get("content", ""),
                "status": t.get("status", "pending"),
                "priority": t.get("priority", "normal"),
            }
        _todos = new_todos

        counts = {"pending": 0, "in_progress": 0, "completed": 0, "cancelled": 0}
        lines = []
        for tid, t in sorted(_todos.items()):
            st = t["status"]
            counts[st] = counts.get(st, 0) + 1
            emoji = {"pending": "○", "in_progress": "◉", "completed": "✓", "cancelled": "✗"}.get(
                st, "?"
            )
            lines.append(f"  {emoji} [{tid}] {t['content']}")

        summary = f"Todos updated: {counts['pending']} pending, {counts['in_progress']} in progress, {counts['completed']} done"
        return ToolOutput(
            text=summary + "\n" + ("\n".join(lines) if lines else "(empty)"),
            title=summary,
            metadata={"counts": counts},
        )


# ── TodoRead ───────────────────────────────────────────────────────


class TodoReadTool(ToolBase):
    name = "TodoRead"
    description = "Read the current session todo list. Shows what you're currently tracking."
    aliases = ["ShowTodos", "CheckTodos"]
    parameters = {}

    async def execute(self) -> ToolOutput:
        if not _todos:
            return ToolOutput(text="No todos tracked this session.", title="Todos (0)")

        counts = {"pending": 0, "in_progress": 0, "completed": 0, "cancelled": 0}
        lines = []
        for tid, t in sorted(_todos.items()):
            st = t["status"]
            counts[st] = counts.get(st, 0) + 1
            emoji = {"pending": "○", "in_progress": "◉", "completed": "✓", "cancelled": "✗"}.get(
                st, "?"
            )
            lines.append(f"  {emoji} [{tid}] {t['content']} ({t.get('priority', 'normal')})")

        return ToolOutput(
            text="\n".join(lines) if lines else "(empty)",
            title=f"Todos ({counts['pending']} pending, {counts['in_progress']} active)",
            metadata={"counts": counts, "todos": list(_todos.values())},
        )


# ── ShellHistory ───────────────────────────────────────────────────


class ShellHistoryTool(ToolBase):
    name = "ShellHistory"
    description = (
        "Review shell commands (Bash + PowerShell) run during this session. Supports filtering."
    )
    aliases = ["CmdHistory", "History"]
    parameters = {
        "last_n": {
            "type": "integer",
            "description": "Only show the last N commands (default: 20, max: 100)",
        },
        "failed_only": {
            "type": "boolean",
            "description": "Only show commands that failed (exit code != 0)",
        },
        "shell_filter": {
            "type": "string",
            "enum": ["all", "bash", "powershell", "cmd"],
            "description": "Filter by shell type",
        },
        "search": {"type": "string", "description": "Only show commands containing this substring"},
        "show_output": {
            "type": "boolean",
            "description": "Include a preview of each command's output (first 200 chars)",
        },
    }

    async def execute(
        self,
        last_n: int = 20,
        failed_only: bool = False,
        shell_filter: str = "all",
        search: str = "",
        show_output: bool = False,
    ) -> ToolOutput:
        entries = list(_shell_history)

        if shell_filter != "all":
            entries = [e for e in entries if shell_filter in e.get("command", "").lower()[:50]]

        if failed_only:
            entries = [e for e in entries if e.get("exit_code", 0) != 0]

        if search:
            entries = [e for e in entries if search.lower() in e.get("command", "").lower()]

        entries = entries[-last_n:]

        if not entries:
            return ToolOutput(text="No matching commands.", title="History (0)")

        lines = []
        for i, e in enumerate(entries):
            status = "X" if e.get("exit_code", 0) != 0 else "OK"
            cmd = e["command"][:120]
            ts = e.get("timestamp", "")[:19]
            lines.append(f"  {status} [{i + 1}] {cmd}")
            lines.append(f"      {ts}")
            if show_output and e.get("output_preview"):
                lines.append(f"      -> {e['output_preview'][:200]}")

        return ToolOutput(
            text="\n".join(lines),
            title=f"History ({len(entries)} commands)",
            metadata={"count": len(entries)},
        )


# ── Auto-register ──────────────────────────────────────────────────

register_tool(TodoWriteTool())
register_tool(TodoReadTool())
register_tool(ShellHistoryTool())
