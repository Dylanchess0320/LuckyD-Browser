"""Night-5 tests: core/parallel_executor.py (was ~0% covered).

Tool classification, parallel vs sequential execution, order preservation,
failure isolation, timeouts, sync-tool thread-pool path, unknown-tool errors,
extract_tool_calls parsing, and the global singleton. The tools registry is
stubbed — no real tools run.
"""

from __future__ import annotations

import asyncio
import time

import pytest

import core.parallel_executor  # noqa: F401 — module import kept for parity with from-imports below
from core.parallel_executor import (
    ParallelExecutor,
    ToolCall,
    extract_tool_calls,
    get_executor,
)


@pytest.fixture()
def stub_registry(monkeypatch):
    """Replace registry lookups with scripted fake tools."""
    tools: dict = {}

    def fake_get(name):
        return tools.get(name)

    import core.parallel_executor as mod

    monkeypatch.setattr(mod.registry, "get", fake_get)
    return tools


# ── classification ─────────────────────────────────────────────────────


def test_can_parallelize_classification():
    ex = ParallelExecutor()
    calls = [
        ToolCall(id="1", name="Read", arguments={}),
        ToolCall(id="2", name="Grep", arguments={}),
        ToolCall(id="3", name="Write", arguments={}),
        ToolCall(id="4", name="Bash", arguments={}),
        ToolCall(id="5", name="AskUserQuestion", arguments={}),
        ToolCall(id="6", name="SomeUnknownTool", arguments={}),
        ToolCall(id="7", name="Edit", arguments={}, depends_on=["3"]),
    ]
    parallel, sequential = ex.can_parallelize(calls)
    assert {c.name for c in parallel} == {"Read", "Grep"}
    assert {c.name for c in sequential} == {
        "Write",
        "Bash",
        "AskUserQuestion",
        "SomeUnknownTool",
        "Edit",
    }


# ── batch execution ──────────────────────────────────────────────────────


async def test_execute_batch_parallel_order_and_counts(stub_registry):
    async def fake_read(path: str = ""):
        await asyncio.sleep(0.01)
        return f"contents of {path}"

    stub_registry["Read"] = fake_read
    ex = ParallelExecutor(max_concurrent=5)
    calls = [
        ToolCall(id="a", name="Read", arguments={"path": "a.py"}),
        ToolCall(id="b", name="Read", arguments={"path": "b.py"}),
        ToolCall(id="c", name="Read", arguments={"path": "c.py"}),
    ]
    batch = await ex.execute_batch(calls)
    assert [r.id for r in batch.results] == ["a", "b", "c"]  # original order
    assert [r.output for r in batch.results] == [
        "contents of a.py",
        "contents of b.py",
        "contents of c.py",
    ]
    assert batch.succeeded == 3 and batch.failed == 0
    assert batch.total_duration_ms > 0
    assert batch.parallel_speedup >= 1.0


async def test_parallel_actually_concurrent(stub_registry):
    async def slow():
        await asyncio.sleep(0.1)
        return "done"

    stub_registry["Read"] = slow
    ex = ParallelExecutor(max_concurrent=2)
    start = time.monotonic()
    batch = await ex.execute_batch(
        [ToolCall(id=str(i), name="Read", arguments={}) for i in range(2)]
    )
    elapsed = time.monotonic() - start
    assert batch.succeeded == 2
    assert elapsed < 0.19  # sequential would take >= 0.2


async def test_failure_does_not_block_others(stub_registry):
    async def boom():
        raise ValueError("kaput")

    async def fine():
        return "ok"

    stub_registry["Grep"] = boom
    stub_registry["Read"] = fine
    ex = ParallelExecutor(max_concurrent=2)
    batch = await ex.execute_batch(
        [
            ToolCall(id="bad", name="Grep", arguments={}),
            ToolCall(id="good", name="Read", arguments={}),
        ]
    )
    assert batch.succeeded == 1 and batch.failed == 1
    by_id = {r.id: r for r in batch.results}
    assert by_id["bad"].success is False
    assert "kaput" in by_id["bad"].error
    assert by_id["good"].output == "ok"


async def test_unknown_tool_error_result(stub_registry):
    ex = ParallelExecutor()
    batch = await ex.execute_batch([ToolCall(id="u", name="Nope", arguments={})])
    assert batch.failed == 1
    # unknown tools are classified sequential
    assert "not found in registry" in batch.results[0].error


async def test_tool_timeout(stub_registry):
    async def hanging():
        await asyncio.sleep(5)

    stub_registry["Read"] = hanging
    ex = ParallelExecutor(max_concurrent=1, default_timeout_sec=0.05)
    batch = await ex.execute_batch([ToolCall(id="t", name="Read", arguments={})])
    assert batch.failed == 1
    assert "Timed out" in batch.results[0].error
    assert batch.results[0].duration_ms >= 40


async def test_sync_tool_runs_in_threadpool(stub_registry):
    def sync_tool(x: int = 0):
        time.sleep(0.01)
        return x * 2

    stub_registry["Read"] = sync_tool
    ex = ParallelExecutor()
    batch = await ex.execute_batch([ToolCall(id="s", name="Read", arguments={"x": 21})])
    assert batch.succeeded == 1
    assert batch.results[0].output == "42"


async def test_sequential_write_tools_run_in_order(stub_registry):
    order: list[str] = []

    async def fake_write(path: str = ""):
        order.append(path)
        return "wrote"

    stub_registry["Write"] = fake_write
    ex = ParallelExecutor()
    batch = await ex.execute_batch(
        [
            ToolCall(id="1", name="Write", arguments={"path": "first"}),
            ToolCall(id="2", name="Write", arguments={"path": "second"}),
        ]
    )
    assert order == ["first", "second"]
    assert batch.succeeded == 2


async def test_empty_batch():
    ex = ParallelExecutor()
    batch = await ex.execute_batch([])
    assert batch.results == []
    assert batch.succeeded == 0 and batch.failed == 0


# ── extract_tool_calls ───────────────────────────────────────────────────


def test_extract_tool_calls_valid_json():
    text = 'Some prose [{"name": "Read", "arguments": {"path": "a.py"}}, {"name": "Grep"}] trailing'
    calls = extract_tool_calls(text)
    assert len(calls) == 2
    assert calls[0].name == "Read"
    assert calls[0].arguments == {"path": "a.py"}
    assert calls[0].id == "0_0"
    assert calls[1].name == "Grep"
    assert calls[1].arguments == {}


def test_extract_tool_calls_invalid_returns_empty():
    assert extract_tool_calls("no tool calls here") == []
    assert extract_tool_calls('[{"name": "Read",]') == []


def test_extract_tool_calls_with_depends_on():
    text = '[{"name": "Edit", "depends_on": ["0_0"]}]'
    calls = extract_tool_calls(text)
    assert calls[0].depends_on == ["0_0"]


# ── singleton ────────────────────────────────────────────────────────────


def test_get_executor_singleton():
    assert get_executor() is get_executor()
    assert get_executor().max_concurrent == 5
