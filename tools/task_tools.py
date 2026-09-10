"""
Task management tools — create, update, list, get tasks for tracking multi-step work.
Persistent across sessions via JSON storage.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from config import TASKS_DIR

from .base import ToolBase, ToolOutput
from .registry import register_tool


def _tasks_file() -> Path:
    return TASKS_DIR / "tasks.json"


def _load_tasks() -> list[dict]:
    path = _tasks_file()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, KeyError):
            return []
    return []


def _save_tasks(tasks: list[dict]):
    path = _tasks_file()
    # TASKS_DIR may not exist yet (fresh machine / redirected config) — the
    # write would otherwise raise FileNotFoundError out of the tool.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tasks, indent=2, default=str))


class TaskCreateTool(ToolBase):
    name = "TaskCreate"
    description = "Create a new task and add it to the task list. Use for multi-step work, delegation, or breaking large goals into subtasks."
    aliases = ["NewTask", "AddTask"]
    parameters = {
        "subject": {"type": "string", "description": "Short title for the task"},
        "description": {
            "type": "string",
            "description": "What needs to be done — include context, acceptance criteria, etc.",
        },
        "priority": {"type": "string", "description": "Task priority: low, normal, high, critical"},
        "tags": {
            "type": "array",
            "description": "Free-form tags for filtering",
            "items": {"type": "string"},
        },
        "blocked_by": {
            "type": "array",
            "description": "List of task IDs this task is blocked by",
            "items": {"type": "string"},
        },
    }

    async def execute(
        self,
        subject: str,
        description: str = "",
        priority: str = "normal",
        tags: list | None = None,
        blocked_by: list | None = None,
    ) -> ToolOutput:
        tasks = _load_tasks()
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        task = {
            "id": task_id,
            "subject": subject,
            "description": description,
            "status": "pending",
            "priority": priority,
            "tags": tags or [],
            "blocked_by": blocked_by or [],
            "created_at": now,
            "updated_at": now,
            "output": "",
        }
        tasks.append(task)
        _save_tasks(tasks)

        return ToolOutput(
            text=f"Task created: [{task_id}] {subject} ({priority})\n{description[:200]}",
            title=f"✅ Created {task_id}",
            metadata={"task_id": task_id, "total_tasks": len(tasks)},
        )


class TaskUpdateTool(ToolBase):
    name = "TaskUpdate"
    description = "Update an existing task's status, subject, description, or output."
    aliases = ["UpdateTask", "SetTask"]
    parameters = {
        "task_id": {"type": "string", "description": "The ID of the task to update"},
        "status": {
            "type": "string",
            "enum": ["pending", "in_progress", "completed", "blocked", "cancelled"],
            "description": "New status for the task",
        },
        "subject": {"type": "string", "description": "New subject/title (optional)"},
        "description": {"type": "string", "description": "Updated description (optional)"},
        "output": {
            "type": "string",
            "description": "Result or output text to attach to the task (optional)",
        },
    }

    async def execute(
        self,
        task_id: str,
        status: str = "",
        subject: str = "",
        description: str = "",
        output: str = "",
    ) -> ToolOutput:
        tasks = _load_tasks()
        now = datetime.now(timezone.utc).isoformat()
        for t in tasks:
            if t["id"] == task_id:
                if status:
                    t["status"] = status
                if subject:
                    t["subject"] = subject
                if description:
                    t["description"] = description
                if output:
                    t["output"] = output
                t["updated_at"] = now
                _save_tasks(tasks)
                return ToolOutput(
                    text=f"Updated [{task_id}] {t['subject']} → {t['status']}",
                    title=f"📝 Updated {task_id}",
                    metadata={"task_id": task_id, "status": t["status"]},
                )
        return ToolOutput(text=f"Task not found: {task_id}", error=True)


class TaskGetTool(ToolBase):
    name = "TaskGet"
    description = "Retrieve full details of a single task by ID."
    aliases = ["GetTask"]
    parameters = {
        "task_id": {"type": "string", "description": "The ID of the task to retrieve"},
    }

    async def execute(self, task_id: str) -> ToolOutput:
        tasks = _load_tasks()
        for t in tasks:
            if t["id"] == task_id:
                text = f"[{t['id']}] {t['subject']}\n"
                text += f"Status: {t['status']} | Priority: {t['priority']}\n"
                if t.get("description"):
                    text += f"\n{t['description']}\n"
                if t.get("blocked_by"):
                    text += f"\nBlocked by: {', '.join(t['blocked_by'])}\n"
                if t.get("output"):
                    text += f"\nOutput:\n{t['output'][:2000]}\n"
                text += (
                    f"\nCreated: {t.get('created_at', '?')} | Updated: {t.get('updated_at', '?')}"
                )
                return ToolOutput(
                    text=text,
                    title=f"Task: {t['subject']}",
                    metadata={"task_id": task_id, "status": t["status"]},
                )
        return ToolOutput(text=f"Task not found: {task_id}", error=True)


class TaskListTool(ToolBase):
    name = "TaskList"
    description = "List all tasks, optionally filtered by status or tag."
    aliases = ["ListTasks", "Tasks"]
    parameters = {
        "status": {
            "type": "string",
            "enum": ["pending", "in_progress", "completed", "blocked", "cancelled", "all"],
            "description": "Filter by status. Default: 'all'",
        },
        "tag": {"type": "string", "description": "Filter by tag (optional)"},
    }

    async def execute(self, status: str = "all", tag: str = "") -> ToolOutput:
        tasks = _load_tasks()
        filtered = tasks
        if status and status != "all":
            filtered = [t for t in filtered if t.get("status") == status]
        if tag:
            filtered = [
                t for t in filtered if tag.lower() in [x.lower() for x in t.get("tags", [])]
            ]

        if not filtered:
            return ToolOutput(text="No tasks found.", title="Tasks (0)")

        lines = []
        for t in filtered:
            emoji = {
                "pending": "○",
                "in_progress": "◉",
                "completed": "✓",
                "blocked": "⊘",
                "cancelled": "✗",
            }.get(t.get("status", ""), "?")
            lines.append(f"  {emoji} [{t['id']}] {t['subject']} ({t.get('priority', 'normal')})")
            if t.get("description"):
                lines.append(f"      {t['description'][:100]}")

        return ToolOutput(
            text="\n".join(lines),
            title=f"Tasks ({len(filtered)})",
            metadata={"count": len(filtered), "total": len(tasks)},
        )


class TaskStopTool(ToolBase):
    name = "TaskStop"
    description = "Cancel or delete a task."
    aliases = ["CancelTask", "DeleteTask"]
    parameters = {
        "task_id": {"type": "string", "description": "The ID of the task to stop"},
        "action": {
            "type": "string",
            "description": "'cancel' keeps the task with cancelled status; 'delete' removes it",
        },
        "reason": {"type": "string", "description": "Optional reason for cancellation"},
    }

    async def execute(self, task_id: str, action: str = "cancel", reason: str = "") -> ToolOutput:
        tasks = _load_tasks()
        if action == "delete":
            new_tasks = [t for t in tasks if t["id"] != task_id]
            if len(new_tasks) == len(tasks):
                return ToolOutput(text=f"Task not found: {task_id}", error=True)
            _save_tasks(new_tasks)
            return ToolOutput(text=f"Deleted task: {task_id}", title="🗑️ Deleted")
        else:
            for t in tasks:
                if t["id"] == task_id:
                    t["status"] = "cancelled"
                    t["updated_at"] = datetime.now(timezone.utc).isoformat()
                    if reason:
                        t["output"] = f"Cancelled: {reason}"
                    _save_tasks(tasks)
                    return ToolOutput(text=f"Cancelled: {task_id}", title="✗ Cancelled")
            return ToolOutput(text=f"Task not found: {task_id}", error=True)


# Auto-register
for cls in [TaskCreateTool, TaskUpdateTool, TaskGetTool, TaskListTool, TaskStopTool]:
    register_tool(cls())
