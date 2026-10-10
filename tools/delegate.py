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
import contextlib
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from .base import ToolBase, ToolOutput
from .registry import register_tool

MAX_RESULT_CHARS = 4000
MAX_WAIT_SEC = 30


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


#: Cap on concurrently running/queued delegated subagents. Each delegate
#: spawns a full CodingAgent that burns tokens for many turns — without a
#: cap the model can fork-bomb the provider bill (and the thread pool) by
#: spawning delegates in a loop. 8 matches the deep-research max_parallel
#: ceiling. Override with DELEGATE_MAX_CONCURRENT.
MAX_CONCURRENT_DELEGATES = _env_int("DELEGATE_MAX_CONCURRENT", 8)

#: Above this size, delegated results are quarantined to disk and only a
#: short pointer note enters the model context — the Terminal's mesh
#: pattern: never dump megabytes of delegated output into context.
QUARANTINE_CHARS = MAX_RESULT_CHARS
_QUARANTINE_DIR_NAME = "delegate-results"
#: Cap on quarantined result files; oldest are pruned so the dir can't grow
#: without bound on long sessions.
MAX_KEPT_QUARANTINE_FILES = 100


def _prune_quarantine_dir(qdir: Path) -> None:
    """Delete oldest quarantined files beyond MAX_KEPT_QUARANTINE_FILES. Never raises."""
    try:
        files = sorted(
            (p for p in qdir.iterdir() if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
        for stale in files[: max(0, len(files) - MAX_KEPT_QUARANTINE_FILES)]:
            with contextlib.suppress(OSError):
                stale.unlink()
    except OSError:
        pass


def quarantine_result(text: str, label: str) -> str:
    """Quarantine a large delegated result to disk; return the pointer note.

    Returns the original text unchanged when it fits in context; otherwise
    writes the full text under DATA_DIR/delegate-results/ and returns a
    short note with the file path plus a head excerpt, so the model can
    Read the file if it needs the details.
    """
    if len(text) <= QUARANTINE_CHARS:
        return text
    try:
        from datetime import datetime, timezone

        from config import DATA_DIR

        qdir = Path(DATA_DIR) / _QUARANTINE_DIR_NAME
        qdir.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)[:40] or "result"
        # Microseconds in the stamp: task_output is often polled twice in
        # the same second for one large result — second-granularity stamps
        # collided and overwrote the first file.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        path = qdir / f"{safe}-{stamp}.md"
        path.write_text(text, encoding="utf-8")
        _prune_quarantine_dir(qdir)
        return (
            f"[delegated result quarantined: {len(text):,} chars — too large for context]\n"
            f"Full result saved to: {path}\n"
            "Use the Read tool on that path if you need the details.\n\n"
            f"--- excerpt (first 1000 chars) ---\n{text[:1000]}"
        )
    except Exception:
        return text[:QUARANTINE_CHARS] + "\n... [truncated]"


_tasks: dict[str, dict[str, Any]] = {}
_tasks_lock = threading.Lock()


#: How many finished delegated tasks to keep for task_output polling.
#: Older terminal entries are evicted (their threads are already done).
MAX_KEPT_TASKS = 50


def _new_task_id() -> str:
    return f"task_{uuid.uuid4().hex[:8]}"


def _active_delegate_count_locked() -> int:
    """Number of delegated tasks currently queued or running.

    Must be called with _tasks_lock held.
    """
    return sum(1 for e in _tasks.values() if e.get("status") in ("queued", "running"))


def _prune_terminal_tasks() -> None:
    """Evict oldest terminal entries so the registry can't grow forever.

    Long sessions accumulate one entry (thread handle + full result text)
    per delegate call. Only terminal states (succeeded/failed/canceled) are
    evicted — running/queued tasks are never dropped. Keeps the newest
    ``MAX_KEPT_TASKS`` terminal entries so task_output stays useful.
    Must be called with _tasks_lock held.
    """
    terminal = [tid for tid, e in _tasks.items() if e.get("status") not in ("queued", "running")]
    overflow = len(terminal) - MAX_KEPT_TASKS
    for tid in terminal[: max(0, overflow)]:
        del _tasks[tid]


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
            _prune_terminal_tasks()
        return

    with _tasks_lock:
        entry = _tasks.get(task_id)
        if entry is None:
            return
        entry["result"] = result
        if entry["status"] != "canceled":
            entry["status"] = "succeeded"
        _prune_terminal_tasks()


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
            if _active_delegate_count_locked() >= MAX_CONCURRENT_DELEGATES:
                return ToolOutput(
                    text=(
                        f"Too many delegated tasks already running "
                        f"({MAX_CONCURRENT_DELEGATES} concurrent limit). "
                        "Wait for some to finish with task_output, or stop "
                        "one with task_stop, then try again."
                    ),
                    error=True,
                )
            _tasks[task_id] = entry
            _prune_terminal_tasks()

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

        with _tasks_lock:
            thread = entry["thread"]
        if thread is not None and thread.is_alive() and wait_sec > 0:
            thread.join(timeout=wait_sec)

        with _tasks_lock:
            status = entry["status"]
            result = entry["result"]
            error = entry["error"]

        text_result = (
            quarantine_result(str(result), f"task-{task_id}") if result is not None else None
        )
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
