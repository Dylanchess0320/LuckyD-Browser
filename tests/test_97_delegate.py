"""Tests for tools/delegate.py — background task delegation tools."""

from __future__ import annotations

import asyncio
import threading

import pytest

import core.agent_loop
import tools.delegate as delegate_mod
from tools.delegate import DelegateTaskTool, TaskOutputTool, TaskStopTool
from tools.registry import registry


class FakeAgent:
    """Stand-in for CodingAgent whose run() returns canned text (sync)."""

    def run(self, task):
        return f"canned:{task}"


class FakeAsyncAgent:
    """Stand-in whose run() is a coroutine, like the real CodingAgent."""

    async def run(self, task):
        return f"async-canned:{task}"


class FakeFailingAgent:
    """Stand-in that raises inside run()."""

    def run(self, task):
        raise RuntimeError("boom")


class SlowAgent:
    """Stand-in that blocks until released, for cancellation tests."""

    release = threading.Event()

    def run(self, task):
        SlowAgent.release.wait(timeout=15)
        return "late result"


@pytest.fixture(autouse=True)
def clean_registry():
    """Isolate the shared _tasks dict between tests."""
    delegate_mod._tasks.clear()
    SlowAgent.release.clear()
    yield
    delegate_mod._tasks.clear()


@pytest.fixture
def fake_agent(monkeypatch):
    monkeypatch.setattr(core.agent_loop, "CodingAgent", FakeAgent)


@pytest.fixture
def fake_async_agent(monkeypatch):
    monkeypatch.setattr(core.agent_loop, "CodingAgent", FakeAsyncAgent)


async def _wait_for(task_id, statuses, attempts=60):
    for _ in range(attempts):
        out = await TaskOutputTool().execute(task_id=task_id, wait_sec=1)
        if out.metadata["status"] in statuses:
            return out
    raise AssertionError(f"task {task_id} never reached {statuses}")


async def test_tools_registered():
    assert registry.get("delegate_task") is not None
    assert registry.get("task_output") is not None
    assert registry.get("task_stop") is not None


async def test_delegate_roundtrip(fake_agent):
    out = await DelegateTaskTool().execute(task="hello")
    task_id = out.metadata["task_id"]
    assert task_id.startswith("task_")
    assert "task_id" in out.metadata

    done = await _wait_for(task_id, {"succeeded", "failed"})
    assert done.metadata["status"] == "succeeded"
    assert done.metadata["result"] == "canned:hello"
    assert done.metadata["error"] is None
    assert not done.error


async def test_delegate_async_agent(fake_async_agent):
    out = await DelegateTaskTool().execute(task="hi")
    done = await _wait_for(out.metadata["task_id"], {"succeeded", "failed"})
    assert done.metadata["status"] == "succeeded"
    assert done.metadata["result"] == "async-canned:hi"


async def test_delegate_failure_recorded(monkeypatch):
    monkeypatch.setattr(core.agent_loop, "CodingAgent", FakeFailingAgent)
    out = await DelegateTaskTool().execute(task="x")
    done = await _wait_for(out.metadata["task_id"], {"succeeded", "failed"})
    assert done.metadata["status"] == "failed"
    assert "boom" in (done.metadata["error"] or "")
    assert done.error  # error flag set on failed tasks


async def test_custom_task_id_and_duplicate(fake_agent):
    first = await DelegateTaskTool().execute(task="a", task_id="my-task")
    assert first.metadata["task_id"] == "my-task"
    dup = await DelegateTaskTool().execute(task="b", task_id="my-task")
    assert dup.error
    assert "already in use" in dup.text


async def test_task_output_unknown_id():
    out = await TaskOutputTool().execute(task_id="nope")
    assert out.error
    assert "Unknown task_id" in out.text


async def test_task_output_truncates_long_result(monkeypatch):
    class LongAgent:
        def run(self, task):
            return "x" * 9000

    monkeypatch.setattr(core.agent_loop, "CodingAgent", LongAgent)
    out = await DelegateTaskTool().execute(task="long")
    done = await _wait_for(out.metadata["task_id"], {"succeeded", "failed"})
    assert len(done.metadata["result"]) <= delegate_mod.MAX_RESULT_CHARS


async def test_task_stop_marks_canceled(monkeypatch):
    monkeypatch.setattr(core.agent_loop, "CodingAgent", SlowAgent)
    out = await DelegateTaskTool().execute(task="slow")
    task_id = out.metadata["task_id"]

    stop = await TaskStopTool().execute(task_id=task_id)
    assert not stop.error
    assert stop.metadata["status"] == "canceled"

    # Late result must not overwrite the canceled status.
    SlowAgent.release.set()
    await _wait_for(task_id, {"canceled", "succeeded", "failed"})
    with delegate_mod._tasks_lock:
        assert delegate_mod._tasks[task_id]["status"] == "canceled"


async def test_task_stop_unknown_id():
    out = await TaskStopTool().execute(task_id="nope")
    assert out.error
    assert "Unknown task_id" in out.text


async def test_task_ids_are_unique(fake_agent):
    ids = set()
    for i in range(5):
        out = await DelegateTaskTool().execute(task=f"t{i}")
        ids.add(out.metadata["task_id"])
    assert len(ids) == 5


async def test_permission_levels():
    assert DelegateTaskTool().permission_level == "NORMAL"
    assert TaskOutputTool().permission_level == "ALWAYS_ALLOW"
    assert TaskStopTool().permission_level == "NORMAL"
    assert "forcibly killed" in TaskStopTool().description or "cannot be" in (
        TaskStopTool().description
    )


def test_asyncio_mode_is_auto():
    # sanity: real CodingAgent.run is a coroutine function, handled by the worker
    assert asyncio.iscoroutinefunction(core.agent_loop.CodingAgent.run)
