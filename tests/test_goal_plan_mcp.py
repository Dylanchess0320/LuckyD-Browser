"""A1: live goals, enforced plan mode, MCP in one-shot.

- The active goal used to be display-only: its text never reached the
  model and the token budget never accrued (``add_spent`` had no
  callers). The goal is now injected into the prompt once per revision
  and spend tracks real usage.
- Plan mode was advisory: ``is_plan_mode`` had no consumers, so the
  model could write mid-plan. State-modifying tools are now blocked
  while plan mode is active (ExitPlanMode itself stays allowed).
- MCP servers connected in the REPL only; one-shot and JSON runs never
  saw MCP tools. All entry paths now share ``_connect_mcp``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import tools.file_tools
import tools.plan_tools  # noqa: F401  (register Enter/ExitPlanMode)

# Intentionally fake API key for tests (not a real secret).
TEST_API_KEY = "test-key-not-a-secret"


class FakeLLMClient:
    """Scripted stand-in (mirrors tests/test_cov_agent_loop.py)."""

    def __init__(self, script):
        self._script = list(script)
        self.model = "fake-model"

    async def chat_stream(self, *args, **kwargs):
        if not self._script:
            return {"content": "default final answer"}
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _make_agent(**kwargs):
    with patch("llm.ProviderRouter"):
        from core.agent_loop import CodingAgent

        ag = CodingAgent(api_key=TEST_API_KEY, model="test-model", **kwargs)

    async def _noop_extract(user_message):
        return None

    async def _noop_refresh(query):
        return False

    ag._extract_session_memories = _noop_extract
    ag._refresh_memory_context = _noop_refresh
    return ag


def _goal_messages(ag):
    return [
        m for m in ag.messages if m.get("role") == "user" and "[goal]" in str(m.get("content", ""))
    ]


class TestGoalPrompt:
    async def test_goal_injected_once_across_runs(self):
        ag = _make_agent()
        ag.set_goal("ship the rover")
        ag.llm_client = FakeLLMClient([{"content": "done"}])
        await ag.run("go", max_turns=2)
        goals = _goal_messages(ag)
        assert len(goals) == 1
        assert "ship the rover" in goals[0]["content"]

        ag.llm_client = FakeLLMClient([{"content": "done"}])
        await ag.run("again", max_turns=2)
        assert len(_goal_messages(ag)) == 1

    async def test_goal_reinjected_on_edit_skipped_when_paused(self):
        ag = _make_agent()
        ag.set_goal("v1")
        ag.llm_client = FakeLLMClient([{"content": "done"}])
        await ag.run("go", max_turns=2)
        assert len(_goal_messages(ag)) == 1

        ag.goals.edit("v2")
        ag.llm_client = FakeLLMClient([{"content": "done"}])
        await ag.run("go", max_turns=2)
        goals = _goal_messages(ag)
        assert len(goals) == 2
        assert "v2" in goals[-1]["content"]

        ag.goals.pause()
        ag.llm_client = FakeLLMClient([{"content": "done"}])
        await ag.run("go", max_turns=2)
        assert len(_goal_messages(ag)) == 2

    async def test_goal_spent_accrues_usage(self):
        ag = _make_agent()
        ag.set_goal("x")
        ag.llm_client = FakeLLMClient(
            [{"content": "done", "_usage": {"input_tokens": 10, "output_tokens": 5}}]
        )
        await ag.run("go", max_turns=2)
        assert ag.goals.goal is not None
        assert ag.goals.goal.spent == 15

    async def test_no_goal_no_injection(self):
        ag = _make_agent()
        ag.llm_client = FakeLLMClient([{"content": "done"}])
        await ag.run("go", max_turns=2)
        assert _goal_messages(ag) == []


class TestPlanModeEnforcement:
    async def test_writes_blocked_reads_and_exit_allowed(self):
        import tools.plan_tools as plans
        from tools.plan_tools import EnterPlanModeTool, ExitPlanModeTool
        from tools.registry import registry

        ag = _make_agent()
        plans._awaiting_approval = False
        await EnterPlanModeTool().execute()
        try:
            decision, reason = ag._permission_mode_decision(registry.get("Write"), "Write")
            assert decision == "block"
            assert "plan mode" in reason.lower()

            decision, _ = ag._permission_mode_decision(registry.get("Edit"), "Edit")
            assert decision == "block"

            decision, _ = ag._permission_mode_decision(registry.get("Read"), "Read")
            assert decision == "allow"

            decision, _ = ag._permission_mode_decision(registry.get("ExitPlanMode"), "ExitPlanMode")
            assert decision == "allow"
        finally:
            await ExitPlanModeTool().execute(plan="cleanup")
            plans._awaiting_approval = False

        decision, _ = ag._permission_mode_decision(registry.get("Write"), "Write")
        assert decision in ("allow", "defer")


class TestMcpConnect:
    async def test_connect_mcp_noop_when_unconfigured(self):
        import main

        mgr = SimpleNamespace(connect_all=AsyncMock(return_value=0))
        await main._connect_mcp(SimpleNamespace(_mcp_manager=mgr))
        mgr.connect_all.assert_awaited_once()

    async def test_connect_mcp_registers_and_reports(self, capsys):
        import main

        mgr = SimpleNamespace(connect_all=AsyncMock(return_value=2))
        with patch("tools.mcp_tools.register_mcp_tools", new=AsyncMock(return_value=5)):
            await main._connect_mcp(SimpleNamespace(_mcp_manager=mgr))
        out = capsys.readouterr().out
        assert "2 server(s), 5 tool(s)" in out

    async def test_one_shot_connects_mcp(self):
        import main

        ag = _make_agent()
        ag.llm_client = FakeLLMClient([{"content": "done"}])
        with (
            patch("main._connect_mcp", new=AsyncMock()) as mcp,
            patch.object(main.ui, "play_done_sound", lambda: None),
        ):
            await main.run_one_shot(ag, "hi")
        mcp.assert_awaited_once()

    async def test_one_shot_json_connects_mcp(self, capsys):
        import main

        ag = _make_agent()
        ag.llm_client = FakeLLMClient([{"content": "done"}])
        with patch("main._connect_mcp", new=AsyncMock()) as mcp:
            await main.run_one_shot_json(ag, "hi")
        mcp.assert_awaited_once()
        assert '{"type": "done"}' in capsys.readouterr().out
