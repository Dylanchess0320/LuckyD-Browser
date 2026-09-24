"""P2: ParallelExecutor batching wired into the agent loop.

Consecutive read-only + ALWAYS_ALLOW calls run together via gather;
writes, prompts, and unknown tools stay strictly sequential, and
program order is always preserved. CODING_AGENT_NO_PARALLEL kills it.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.agent_loop import CodingAgent


def _tc(name, call_id, args=None):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args or {})},
    }


@pytest.fixture
def _registry(monkeypatch):
    import tools.registry as reg

    real_get = reg.registry.get

    def fake_get(name):
        if name in ("Read", "Grep"):
            tool = MagicMock()
            tool.name = name
            tool.permission_level = "ALWAYS_ALLOW"
            return tool
        if name in ("Write", "AskUserQuestion"):
            tool = MagicMock()
            tool.name = name
            tool.permission_level = "REQUIRES_APPROVAL"
            return tool
        return real_get(name)

    monkeypatch.setattr(reg.registry, "get", fake_get)


def _ns():
    return SimpleNamespace(
        _emit_event=lambda *a, **k: None,
        _parse_tool_call=CodingAgent._parse_tool_call,
        _parallel_safe=CodingAgent._parallel_safe,
    )


class TestBatching:
    def test_consecutive_reads_batch(self, _registry):
        batches = CodingAgent._batch_tool_calls(_ns(), [_tc("Read", "a"), _tc("Grep", "b")])
        assert len(batches) == 1
        assert [p[0] for p in batches[0]] == ["Read", "Grep"]

    def test_write_splits_batches_in_order(self, _registry):
        tcs = [_tc("Read", "a"), _tc("Write", "b"), _tc("Read", "c")]
        batches = CodingAgent._batch_tool_calls(_ns(), tcs)
        assert [[p[0] for p in b] for b in batches] == [["Read"], ["Write"], ["Read"]]

    def test_unknown_tool_runs_alone(self, _registry):
        batches = CodingAgent._batch_tool_calls(_ns(), [_tc("Nope", "a")])
        assert len(batches) == 1 and len(batches[0]) == 1

    def test_kill_switch_forces_sequential(self, _registry, monkeypatch):
        monkeypatch.setenv("CODING_AGENT_NO_PARALLEL", "1")
        batches = CodingAgent._batch_tool_calls(_ns(), [_tc("Read", "a"), _tc("Read", "b")])
        assert len(batches) == 2

    def test_prompt_tool_never_parallel(self, _registry):
        batches = CodingAgent._batch_tool_calls(
            _ns(), [_tc("AskUserQuestion", "a"), _tc("AskUserQuestion", "b")]
        )
        assert len(batches) == 2

    def test_parse_assigns_fallback_ids(self):
        name, args = CodingAgent._parse_tool_call({"function": {"name": "Read"}}, 3)
        assert (name, args["_id"]) == ("Read", "call_3")


class TestConcurrency:
    async def test_batch_runs_concurrently_in_order(self, _registry):
        in_flight = 0
        peak = 0

        async def fake_execute(name, args):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.05)
            in_flight -= 1
            return {"role": "tool", "tool_call_id": args["_id"], "content": f"ok:{name}"}

        ns = _ns()
        ns._execute_tool = fake_execute
        tcs = [_tc("Read", "a"), _tc("Grep", "b"), _tc("Read", "c")]
        batches = CodingAgent._batch_tool_calls(ns, tcs)
        assert len(batches) == 1
        results = await asyncio.gather(*(CodingAgent._run_tool_call(ns, p) for p in batches[0]))
        assert peak == 3  # all three overlapped
        assert [r["tool_call_id"] for r in results] == ["a", "b", "c"]  # order kept
