"""Tests for the CodingAgent (now from core/agent_loop.py via agent.py shim)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

# Intentionally fake API key for tests (not a real secret).
TEST_API_KEY = "test-api-key"


@pytest.fixture
def agent():
    """Create a CodingAgent with mocked dependencies."""
    import agent as agent_mod
    from project.types import ProjectInfo

    agent_mod._project_info = ProjectInfo(name="test", language="Python")
    with patch("llm.ProviderRouter"):
        from agent import CodingAgent

        ag = CodingAgent(api_key=TEST_API_KEY, model="test-model", temperature=0.0, max_tokens=100)
        return ag


def response(text="", tool_calls=None):
    from llm import LLMResult

    return LLMResult(content=text, tool_calls=tool_calls)


class TestInit:
    def test_defaults(self):
        import agent as agent_mod
        from project.types import ProjectInfo

        agent_mod._project_info = ProjectInfo()
        with patch("llm.ProviderRouter"):
            from agent import CodingAgent

            ag = CodingAgent()
        assert ag.model is not None
        assert ag.turn_count == 0
        assert ag.messages == []
        assert ag.conversation_id.startswith("conv_")

    def test_custom(self):
        import agent as agent_mod
        from project.types import ProjectInfo

        agent_mod._project_info = ProjectInfo()
        with patch("llm.ProviderRouter"):
            from agent import CodingAgent

            ag = CodingAgent(api_key="ck", model="cm", temperature=0.5, max_tokens=2000)
        assert ag.api_key == "ck"
        assert ag.temperature == 0.5
        assert ag.max_tokens == 2000

    def test_reset(self):
        import agent as agent_mod
        from project.types import ProjectInfo

        agent_mod._project_info = ProjectInfo()
        with patch("llm.ProviderRouter"):
            from agent import CodingAgent

            ag = CodingAgent()
            ag.messages = [{"role": "user", "content": "hi"}]
            ag.turn_count = 5
            ag.reset()
        assert ag.messages == []
        assert ag.turn_count == 0


class TestRun:
    @pytest.mark.asyncio
    async def test_simple_reply(self, agent):
        agent.llm_client = AsyncMock()
        agent.llm_client.chat_stream = AsyncMock(return_value=response(text="Hello, world!"))
        result = await agent.run("Hi!")
        assert "Hello, world!" in result
        assert agent.turn_count >= 1

    @pytest.mark.asyncio
    async def test_api_error(self, agent):
        agent.llm_client = AsyncMock()
        agent.llm_client.chat_stream = AsyncMock(side_effect=Exception("API error"))
        result = await agent.run("Hello")
        assert result is not None


class TestReset:
    def test_reset_clears_state(self, agent):
        agent.reset()
        assert agent.conversation_id.startswith("conv_")


class TestTenTenRegressions:
    """10.9.3 hardening contracts: approval ownership, retries, turn ceiling."""

    def test_coding_agent_does_not_install_a_bare_approval_hook(self):
        # The entry point (main / web_server / acp_server) owns the configured
        # ApprovalHook. A bare duplicate parks approvals in the queue with no
        # callback and blocks for 300s, so CodingAgent must not install one.
        from core.approval_hook import ApprovalHook
        from core.hooks import get_hooks, reset_hooks

        reset_hooks()
        with patch("llm.ProviderRouter"):
            from agent import CodingAgent

            CodingAgent(
                api_key=TEST_API_KEY,
                model="test-model",
                temperature=0.0,
                max_tokens=100,
            )
        installed = [getattr(hook, "__self__", hook) for hook in get_hooks().before_tool]
        assert not any(isinstance(hook, ApprovalHook) for hook in installed)

    def test_agent_llm_client_uses_two_retries(self, agent):
        assert agent.max_retries == 2
        assert agent.llm_client.max_retries == 2

    def test_config_max_turns_default_and_override(self, monkeypatch):
        import config

        monkeypatch.setattr(config, "resolve_provider_config", lambda: {})
        monkeypatch.delenv("CODING_AGENT_MAX_TURNS", raising=False)
        assert config.get_config()["max_turns"] == 10000
        monkeypatch.setenv("CODING_AGENT_MAX_TURNS", "42")
        assert config.get_config()["max_turns"] == 42

    def test_edit_requires_approval(self):
        from tools.file_tools import EditTool

        assert EditTool.permission_level == "REQUIRES_APPROVAL"
