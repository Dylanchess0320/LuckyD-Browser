"""
Background task delegation — spawn subagents that run in parallel threads.

Three tools are registered here:

- ``delegate_task``: spawn a fresh ``CodingAgent`` on a background thread and
  return a ``task_id`` immediately (non-blocking).
- ``task_output``: poll a task for its status/result, with an optional bounded
  wait (up to 30s).
- ``task_stop``: mark a task canceled (cooperative — Python threads cannot be
  forcibly killed, so the underlying thread keeps running until it finishes and
  its late result is discarded).

Each background entry is tracked in a module-level dict guarded by a lock:

    task_id -> {"thread", "status", "result", "error"}

Statuses: ``queued`` / ``running`` / ``succeeded`` / ``failed`` / ``canceled``.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from typing import Any

from .base import ToolBase, ToolOutput
from .registry import register_tool

MAX_RESULT_CHARS = 4000
MAX_WAIT_SEC = 30


_tasks: dict[str, dict[str, Any]] = {}
_tasks_lock = threading.Lock()


def _new_task_id() -> str:
    return f"task_{uuid.uuid4().hex[:8]}"


def _run_subagent(task_id: str, task: str) -> None:
    """Thread entry point: run a fresh CodingAgent to completion, recording outcome.

    All exceptions are caught and recorded as ``failed`` so the polling tool
    always sees a terminal state.
    """
    # Imported inside the worker (rather than at module top) to avoid import
    # cycles, and resolved as a module attribute so tests can monkeypatch
    # ``core.agent_loop.CodingAgent``.
    import core.agent_loop

    try:
        agent = core.agent_loop.CodingAgent()
        raw_result = agent.run(task)
        # CodingAgent.run is async; fakes used in tests may return a plain value.
        result = asyncio.run(raw_result) if asyncio.iscoroutine(raw_result) else raw_result
    except Exception as e:  # every failure must land in the entry
        with _tasks_lock:
            entry = _tasks.get(task_id)
            if entry is not None:
                entry["status"] = "failed"
                entry["error"] = f"{type(e).__name__}: {e}"
        return

    with _tasks_lock:
        entry = _tasks.get(task_id)
        if entry is None:
            return
        entry["result"] = result
        if entry["status"] != "canceled":
            entry["status"] = "succeeded"


class DelegateTaskTool(ToolBase):
    """Spawn a background subagent for a parallelizable chunk of work."""

    name = "delegate_task"
    description = (
        "Spawn a background subagent for a parallelizable chunk of work. "
        "Runs a fresh CodingAgent in a background thread and returns a "
        "task_id immediately (non-blocking). Poll with task_output to check "
        "status/result, and use task_stop to request cancellation."
    )
    parameters = {
        "task": {
            "type": "string",
            "description": (
                "The self-contained task for the subagent, with enough context "
                "for it to work independently."
            ),
            "required": True,
        },
        "task_id": {
            "type": "string",
            "description": "Optional pre-chosen task id (must be unique).",
            "required": False,
        },
    }
    permission_level = "NORMAL"
    timeout_sec = None  # returns immediately after spawning; the work runs in the thread

    async def execute(self, **kwargs: Any) -> ToolOutput:
        task = kwargs.get("task") or ""
        task_id = kwargs.get("task_id") or _new_task_id()

        entry: dict[str, Any] = {
            "thread": None,
            "status": "queued",
            "result": None,
            "error": None,
        }
        with _tasks_lock:
            if task_id in _tasks:
                return ToolOutput(
                    text=f"task_id '{task_id}' is already in use.",
                    error=True,
                )
            _tasks[task_id] = entry

        thread = threading.Thread(
            target=_run_subagent,
            args=(task_id, task),
            daemon=True,
            name=f"delegate-{task_id}",
        )
        with _tasks_lock:
            entry["thread"] = thread
            entry["status"] = "running"
        thread.start()

        return ToolOutput(
            text=f"Spawned background task '{task_id}'. Poll with task_output.",
            metadata={"task_id": task_id},
        )


class TaskOutputTool(ToolBase):
    """Get the status and result of a background delegated task."""

    name = "task_output"
    description = (
        "Get the status and result of a background delegated task created by "
        "delegate_task. Returns status (queued/running/succeeded/failed/canceled), "
        "the result truncated to ~4000 characters, and any error. If the task "
        "is still running and wait_sec > 0, blocks up to wait_sec seconds "
        "(max 30) for it to finish."
    )
    parameters = {
        "task_id": {
            "type": "string",
            "description": "The task id returned by delegate_task.",
            "required": True,
        },
        "wait_sec": {
            "type": "number",
            "description": "Seconds to wait for a running task (default 0, max 30).",
            "required": False,
        },
    }
    permission_level = "ALWAYS_ALLOW"

    async def execute(self, **kwargs: Any) -> ToolOutput:
        task_id = str(kwargs.get("task_id") or "")
        try:
            wait_sec = float(kwargs.get("wait_sec", 0) or 0)
        except (TypeError, ValueError):
            wait_sec = 0.0
        wait_sec = max(0.0, min(wait_sec, MAX_WAIT_SEC))

        with _tasks_lock:
            entry = _tasks.get(task_id)
        if entry is None:
            return ToolOutput(
                text=f"Unknown task_id: {task_id!r}.",
                error=True,
            )

        thread = entry["thread"]
        if thread is not None and thread.is_alive() and wait_sec > 0:
            thread.join(timeout=wait_sec)

        with _tasks_lock:
            status = entry["status"]
            result = entry["result"]
            error = entry["error"]

        text_result = str(result)[:MAX_RESULT_CHARS] if result is not None else None
        lines = [f"task_id: {task_id}", f"status: {status}"]
        if text_result is not None:
            lines.append(f"result:\n{text_result}")
        if error:
            lines.append(f"error: {error}")
        return ToolOutput(
            text="\n".join(lines),
            metadata={"task_id": task_id, "status": status, "result": text_result, "error": error},
            error=status == "failed",
        )


class TaskStopTool(ToolBase):
    """Request cancellation of a background delegated task (cooperative)."""

    name = "task_stop"
    description = (
        "Request cancellation of a background delegated task. Honest caveat: "
        "Python threads cannot be forcibly killed, so this only marks the task "
        "'canceled' cooperatively. The underlying thread keeps running in the "
        "background until it finishes on its own; its late result is discarded."
    )
    parameters = {
        "task_id": {
            "type": "string",
            "description": "The task id returned by delegate_task.",
            "required": True,
        },
    }
    permission_level = "NORMAL"

    async def execute(self, **kwargs: Any) -> ToolOutput:
        task_id = str(kwargs.get("task_id") or "")
        with _tasks_lock:
            entry = _tasks.get(task_id)
            if entry is None:
                return ToolOutput(
                    text=f"Unknown task_id: {task_id!r}.",
                    error=True,
                )
            entry["status"] = "canceled"

        return ToolOutput(
            text=(
                f"Task '{task_id}' marked canceled. Note: the underlying thread "
                "cannot be killed and will finish in the background; its result "
                "will be discarded."
            ),
            metadata={"task_id": task_id, "status": "canceled"},
        )


register_tool(DelegateTaskTool())
register_tool(TaskOutputTool())
register_tool(TaskStopTool())
