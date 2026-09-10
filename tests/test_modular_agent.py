"""Tests for the modular CodingAgent (core/agent_loop.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

# Intentionally fake API key for tests (not a real secret).
TEST_API_KEY = "test-api-key"


@pytest.fixture
def modular_agent():
    """Create a modular CodingAgent with mocked dependencies."""
    with patch("llm.ProviderRouter"):
        from core.agent_loop import CodingAgent

        ag = CodingAgent(api_key=TEST_API_KEY, model="test-model", temperature=0.0, max_tokens=100)
        return ag


def mod_response(text="", tool_calls=None):
    from llm import LLMResult

    return LLMResult(content=text, tool_calls=tool_calls)


class TestModularAgentInit:
    def test_defaults(self):
        with patch("llm.ProviderRouter"):
            from core.agent_loop import CodingAgent

            ag = CodingAgent()
        assert ag.model is not None
        assert ag.turn_count == 0
        assert ag.messages == []
        assert ag.conversation_id.startswith("conv_")

    def test_custom_values(self):
        with patch("llm.ProviderRouter"):
            from core.agent_loop import CodingAgent

            ag = CodingAgent(api_key="ck", model="cm", temperature=0.5, max_tokens=2000)
        assert ag.api_key == "ck"
        assert ag.temperature == 0.5
        assert ag.max_tokens == 2000

    def test_has_required_attributes(self, modular_agent):
        for attr in [
            "stream_callback",
            "think_callback",
            "provider_name",
            "cost_tracker",
            "_project_info",
            "llm_client",
            "_router",
            "_provider_config",
        ]:
            assert hasattr(modular_agent, attr), "Missing: " + attr

    def test_reset(self, modular_agent):
        modular_agent.messages = [{"role": "user", "content": "hi"}]
        modular_agent.turn_count = 5
        modular_agent.reset()
        assert modular_agent.messages == []
        assert modular_agent.turn_count == 0


class TestModularAgentRun:
    @pytest.mark.asyncio
    async def test_simple_reply(self, modular_agent):
        modular_agent.llm_client = AsyncMock()
        modular_agent.llm_client.chat_stream = AsyncMock(
            return_value=mod_response(text="Hello from modular agent!")
        )
        result = await modular_agent.run("Hi!")
        assert "modular agent" in result
        assert modular_agent.turn_count >= 1

    @pytest.mark.asyncio
    async def test_api_error(self, modular_agent):
        modular_agent.llm_client = AsyncMock()
        modular_agent.llm_client.chat_stream = AsyncMock(side_effect=Exception("API error"))
        result = await modular_agent.run("Hello")
        assert result is not None
