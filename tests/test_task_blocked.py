"""P1: TaskUpdate enforces blocked_by ordering.

A task with open blockers can no longer jump to in_progress/completed —
previously the dependency list was display-only and ordering was fiction.
"""

from __future__ import annotations

import tools.task_tools as tasks
from tools.task_tools import TaskCreateTool, TaskUpdateTool


async def _create(subject, blocked_by=None):
    out = await TaskCreateTool().execute(subject=subject, blocked_by=blocked_by or [])
    assert out.error is False, out.text
    return out.metadata["task_id"]


class TestBlockedBy:
    async def test_blocked_task_cannot_start(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tasks, "TASKS_DIR", tmp_path)
        first = await _create("foundation")
        second = await _create("tower", blocked_by=[first])
        out = await TaskUpdateTool().execute(task_id=second, status="in_progress")
        assert out.error is True
        assert first in out.text

    async def test_unblocked_after_dependency_completes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tasks, "TASKS_DIR", tmp_path)
        first = await _create("foundation")
        second = await _create("tower", blocked_by=[first])
        done = await TaskUpdateTool().execute(task_id=first, status="completed")
        assert done.error is False
        out = await TaskUpdateTool().execute(task_id=second, status="in_progress")
        assert out.error is False
        assert out.metadata["status"] == "in_progress"

    async def test_cancelled_blocker_satisfies(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tasks, "TASKS_DIR", tmp_path)
        first = await _create("abandoned")
        second = await _create("tower", blocked_by=[first])
        out = await TaskUpdateTool().execute(task_id=first, status="cancelled")
        assert out.error is False
        out = await TaskUpdateTool().execute(task_id=second, status="completed")
        assert out.error is False

    async def test_missing_blocker_ignored(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tasks, "TASKS_DIR", tmp_path)
        lone = await _create("lone", blocked_by=["task-that-never-existed"])
        out = await TaskUpdateTool().execute(task_id=lone, status="completed")
        assert out.error is False

    async def test_other_statuses_unaffected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tasks, "TASKS_DIR", tmp_path)
        first = await _create("foundation")
        second = await _create("tower", blocked_by=[first])
        out = await TaskUpdateTool().execute(task_id=second, status="blocked")
        assert out.error is False
