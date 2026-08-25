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
        assert agent.messages == []
        assert agent.turn_count == 0
