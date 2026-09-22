"""Tests for main.py."""

from __future__ import annotations

import pytest
from unittest.mock import patch

import main


@pytest.fixture
def agent():
    """Create a CodingAgent with mocked dependencies."""
    import agent as agent_mod
    from project.types import ProjectInfo

    agent_mod._project_info = ProjectInfo(name="test", language="Python")
    with patch("llm.ProviderRouter"):
        from agent import CodingAgent

        ag = CodingAgent(
            api_key="test-api-key", model="test-model", temperature=0.0, max_tokens=100
        )
        return ag


@pytest.mark.asyncio
async def test_handle_command_empty_string(agent):
    """Test that empty or whitespace-only commands are ignored (return False)."""
    # Empty string (should have already been caught before, but just in case)
    assert await main.handle_command(agent, "") is False
    # Just a slash
    assert await main.handle_command(agent, "/") is False
    # Slash and whitespace
    assert await main.handle_command(agent, " / ") is False
    # Slash and multiple whitespace
    assert await main.handle_command(agent, "/  ") is False
