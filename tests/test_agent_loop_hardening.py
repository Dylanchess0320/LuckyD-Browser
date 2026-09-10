"""Regression tests for agent-loop hardening (core/agent_loop.py).

1. Circuit breaker: when every tool call fails turn after turn, the loop
   stops early with an actionable message instead of spinning (and billing)
   all the way to max_turns.
2. Hint dedupe: several failing tool calls in one turn produce a single
   HINT system message, not one identical hint per failure.
3. Error prefixing: tool failures (unknown tool, argument errors) surface
   as "Error: ..." so the loop's error detection — and the [ERR] console
   marker — treats timeouts/argument errors the same as other failures.
4. API-unreachable message names the provider/model and tells the user
   what to check.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

# Intentionally fake API key for tests (not a real secret).
TEST_API_KEY = "test-key-12345"


def _make_agent():
    with patch("llm.ProviderRouter"):
        from core.agent_loop import CodingAgent

        ag = CodingAgent(api_key=TEST_API_KEY, model="test-model", temperature=0.0)
        # Memory extraction spawns a real background LLM call; neutralize it.
        ag._extract_session_memories = AsyncMock()
        return ag


def _tool_call(name, call_id, arguments="{}"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _llm_result(text="", tool_calls=None):
    from llm import LLMResult

    return LLMResult(content=text, tool_calls=tool_calls)


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_stops_stuck_loop_early(self):
        ag = _make_agent()
        events = []
        ag.callbacks.on_event = lambda e: events.append(e)
        # The model keeps calling a tool that does not exist: every turn fails.
        ag.llm_client = AsyncMock()
        ag.llm_client.chat_stream = AsyncMock(
            return_value=_llm_result(tool_calls=[_tool_call("nope_tool_xyz", "c1")])
        )
        result = await ag.run("do the thing", max_turns=20)
        assert "consecutive turns" in result
        assert "every tool call failed" in result
        # Breaker fires at 4, far short of the 20-turn budget.
        assert ag.turn_count == ag._max_consecutive_failed_tool_turns == 4
        assert ag.llm_client.chat_stream.call_count == 4
        stuck = [e for e in events if e.type.name == "ERROR" and e.payload.get("stuck_loop")]
        assert stuck, "expected an ERROR event flagged stuck_loop=True"

    @pytest.mark.asyncio
    async def test_success_resets_breaker(self):
        ag = _make_agent()
        ag.llm_client = AsyncMock()
        calls = [
            _llm_result(tool_calls=[_tool_call("nope_tool_xyz", "c1")]),  # turn 1: fail
            _llm_result(tool_calls=[_tool_call("nope_tool_xyz", "c2")]),  # turn 2: fail
            _llm_result(text="done"),  # turn 3: final answer
        ]
        ag.llm_client.chat_stream = AsyncMock(side_effect=calls)
        result = await ag.run("do the thing", max_turns=20)
        assert result == "done"
        assert ag._consecutive_failed_tool_turns == 0

    @pytest.mark.asyncio
    async def test_partial_success_does_not_trip_breaker(self):
        """A turn with one failing and one succeeding tool call is not a
        fully-failed turn, so the breaker must not count it."""
        from tools.base import ToolBase, ToolOutput
        from tools.registry import registry

        class _OkTool(ToolBase):
            name = "test_ok_tool_xyz"
            description = "always succeeds"
            parameters = {}

            async def execute(self, **kwargs) -> ToolOutput:
                return ToolOutput(text="fine")

        registry.register(_OkTool())
        try:
            ag = _make_agent()
            ag.llm_client = AsyncMock()
            ag.llm_client.chat_stream = AsyncMock(
                side_effect=[
                    _llm_result(
                        tool_calls=[
                            _tool_call("nope_tool_xyz", "c1"),
                            _tool_call("test_ok_tool_xyz", "c2"),
                        ]
                    ),
                    _llm_result(text="done"),
                ]
            )
            result = await ag.run("do the thing", max_turns=20)
            assert result == "done"
            assert ag._consecutive_failed_tool_turns == 0
        finally:
            registry._tools.pop("test_ok_tool_xyz", None)

    def test_reset_clears_breaker(self):
        ag = _make_agent()
        ag._consecutive_failed_tool_turns = 3
        ag._no_tool_call_turns = 2
        ag.reset()
        assert ag._consecutive_failed_tool_turns == 0
        assert ag._no_tool_call_turns == 0


class TestHintDedupe:
    @pytest.mark.asyncio
    async def test_single_hint_per_turn(self):
        ag = _make_agent()
        ag.llm_client = AsyncMock()
        ag.llm_client.chat_stream = AsyncMock(
            side_effect=[
                # Turn 1: two failing calls (no previous error -> no hint yet).
                _llm_result(
                    tool_calls=[
                        _tool_call("nope_tool_xyz", "c1"),
                        _tool_call("nope_tool_xyz", "c2"),
                    ]
                ),
                # Turn 2: two more failing calls (previous errors exist -> hint).
                _llm_result(
                    tool_calls=[
                        _tool_call("nope_tool_xyz", "c3"),
                        _tool_call("nope_tool_xyz", "c4"),
                    ]
                ),
                _llm_result(text="giving up"),
            ]
        )
        await ag.run("do the thing", max_turns=20)
        hints = [m for m in ag.messages if (m.get("content") or "").startswith("HINT:")]
        assert len(hints) == 1, f"expected exactly one HINT message, got {len(hints)}"


class TestErrorPrefixing:
    @pytest.mark.asyncio
    async def test_unknown_tool_error_prefixed(self):
        ag = _make_agent()
        msg = await ag._execute_tool("nope_tool_xyz", {"_id": "c1"})
        assert msg["content"].startswith("Error:")

    @pytest.mark.asyncio
    async def test_argument_error_prefixed(self):
        from tools.base import ToolBase, ToolOutput
        from tools.registry import registry

        class _NeedsArg(ToolBase):
            name = "test_needs_arg_xyz"
            description = "requires an argument"
            parameters = {}

            async def execute(self, required_param: str) -> ToolOutput:
                return ToolOutput(text="ok")

        registry.register(_NeedsArg())
        try:
            ag = _make_agent()
            msg = await ag._execute_tool("test_needs_arg_xyz", {"_id": "c1"})
            assert msg["content"].startswith("Error:")
            assert "argument" in msg["content"].lower()
        finally:
            registry._tools.pop("test_needs_arg_xyz", None)


class TestApiUnreachableMessage:
    def test_names_provider_and_model(self):
        ag = _make_agent()
        msg = ag._api_unreachable_message(Exception("conn refused"))
        assert "test-model" in msg
        assert "conn refused" in msg
        assert "API key" in msg
        assert "/model" in msg

    def test_no_exception_still_actionable(self):
        ag = _make_agent()
        msg = ag._api_unreachable_message()
        assert "Could not reach" in msg
        assert "network" in msg.lower()
