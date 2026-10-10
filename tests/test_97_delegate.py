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


# ── concurrency cap ──────────────────────────────────────────────────


async def test_concurrency_cap_rejects_overflow(monkeypatch, fake_agent):
    """delegate_task must refuse new spawns past MAX_CONCURRENT_DELEGATES —
    each delegate burns tokens for many turns, so unbounded spawning is a
    fork-bomb on the provider bill."""
    monkeypatch.setattr(delegate_mod, "MAX_CONCURRENT_DELEGATES", 2)
    # Occupy both slots with slow agents that never finish on their own.
    monkeypatch.setattr(core.agent_loop, "CodingAgent", SlowAgent)
    out1 = await DelegateTaskTool().execute(task="one")
    out2 = await DelegateTaskTool().execute(task="two")
    assert not out1.error and not out2.error

    third = await DelegateTaskTool().execute(task="three")
    assert third.error
    assert "Too many delegated tasks" in third.text
    SlowAgent.release.set()


async def test_concurrency_slot_frees_after_completion(monkeypatch, fake_agent):
    """A finished task frees its concurrency slot for the next spawn."""
    monkeypatch.setattr(delegate_mod, "MAX_CONCURRENT_DELEGATES", 1)
    out1 = await DelegateTaskTool().execute(task="one")
    done = await _wait_for(out1.metadata["task_id"], {"succeeded", "failed"})
    assert done.metadata["status"] == "succeeded"

    out2 = await DelegateTaskTool().execute(task="two")
    assert not out2.error
    await _wait_for(out2.metadata["task_id"], {"succeeded", "failed"})


# ── registry pruning ─────────────────────────────────────────────────


async def test_terminal_tasks_pruned_beyond_cap(monkeypatch, fake_agent):
    """Oldest terminal entries are evicted past MAX_KEPT_TASKS so the
    registry (thread handles + full result texts) can't grow forever."""
    monkeypatch.setattr(delegate_mod, "MAX_KEPT_TASKS", 3)
    ids = []
    for i in range(5):
        out = await DelegateTaskTool().execute(task=f"t{i}")
        ids.append(out.metadata["task_id"])
        await _wait_for(out.metadata["task_id"], {"succeeded", "failed"})
    with delegate_mod._tasks_lock:
        kept = list(delegate_mod._tasks.keys())
    assert len(kept) == 3
    assert kept == ids[-3:]
    # evicted ids read back as unknown
    gone = await TaskOutputTool().execute(task_id=ids[0])
    assert gone.error and "Unknown task_id" in gone.text


async def test_running_tasks_never_pruned(monkeypatch):
    """Pruning must never drop a queued/running task, however small the cap."""
    monkeypatch.setattr(delegate_mod, "MAX_KEPT_TASKS", 1)
    monkeypatch.setattr(delegate_mod, "MAX_CONCURRENT_DELEGATES", 8)
    monkeypatch.setattr(core.agent_loop, "CodingAgent", SlowAgent)
    out = await DelegateTaskTool().execute(task="slow")
    assert not out.error
    with delegate_mod._tasks_lock:
        assert out.metadata["task_id"] in delegate_mod._tasks
    SlowAgent.release.set()
    await _wait_for(out.metadata["task_id"], {"succeeded", "failed"})


# ── quarantine hardening ─────────────────────────────────────────────


def test_quarantine_stamp_has_microseconds(tmp_path, monkeypatch):
    """Same-second double polls must not collide on the quarantine filename."""
    import tools.delegate as d

    monkeypatch.setattr(d, "QUARANTINE_CHARS", 5)
    monkeypatch.setattr("config.DATA_DIR", str(tmp_path))
    n1 = d.quarantine_result("123456789", "task-x")
    n2 = d.quarantine_result("123456789", "task-x")
    p1 = next(line for line in n1.splitlines() if line.startswith("Full result saved to:"))
    p2 = next(line for line in n2.splitlines() if line.startswith("Full result saved to:"))
    assert p1 != p2
    files = list((tmp_path / "delegate-results").iterdir())
    assert len(files) == 2


def test_quarantine_dir_pruned(tmp_path, monkeypatch):
    """The quarantine dir is capped at MAX_KEPT_QUARANTINE_FILES."""
    import tools.delegate as d

    monkeypatch.setattr(d, "QUARANTINE_CHARS", 5)
    monkeypatch.setattr(d, "MAX_KEPT_QUARANTINE_FILES", 3)
    monkeypatch.setattr("config.DATA_DIR", str(tmp_path))
    for i in range(5):
        d.quarantine_result(f"12345{i}", f"task-{i}")
    files = list((tmp_path / "delegate-results").iterdir())
    assert len(files) == 3
