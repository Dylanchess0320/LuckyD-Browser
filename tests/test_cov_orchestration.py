"""Coverage tests for tools/agent_orchestration.py.

Exercises the in-memory agent/team registry, message routing, and the
AgentHandoff / TeamCreate / SendMessage / ReceiveMessage / ListAgents tools.
CodingAgent and config are faked via sys.modules injection (hand-written
fakes, no network, no real LLM calls).
"""

from __future__ import annotations

import sys
import types

import pytest

import tools.agent_orchestration as orch
from tools.agent_orchestration import (
    AgentHandoffTool,
    ListAgentsTool,
    ReceiveMessageTool,
    SendMessageTool,
    TeamCreateTool,
)


@pytest.fixture()
def clean_state():
    orch._agents.clear()
    orch._teams.clear()
    orch._message_inboxes.clear()
    yield
    orch._agents.clear()
    orch._teams.clear()
    orch._message_inboxes.clear()


def _install_fake_agent(monkeypatch, fail: bool = False):
    """Install fake `agent.CodingAgent` and `config.get_config` modules."""
    cfg = {
        "api_key": "key",
        "base_url": "http://base",
        "model": "base-model",
        "temperature": 0.5,
        "max_tokens": 100,
    }

    class FakeCodingAgent:
        instances: list = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.turn_count = 7
            self.max_turns = 30
            FakeCodingAgent.instances.append(self)

        async def run(self, prompt, max_turns=None):
            self.prompt = prompt
            self.max_turns_used = max_turns
            if fail:
                raise RuntimeError("boom")
            return f"RESULT[{prompt[:24]}]"

    FakeCodingAgent.instances = []
    agent_mod = types.ModuleType("agent")
    agent_mod.CodingAgent = FakeCodingAgent
    config_mod = types.ModuleType("config")
    config_mod.get_config = lambda: dict(cfg)
    monkeypatch.setitem(sys.modules, "agent", agent_mod)
    monkeypatch.setitem(sys.modules, "config", config_mod)
    return FakeCodingAgent, cfg


# ── _register_agent ───────────────────────────────────────────────────────


def test_register_agent_named(clean_state):
    aid = orch._register_agent("alice", "coder")
    assert aid == "alice"
    assert orch._agents["alice"]["role"] == "coder"
    assert orch._agents["alice"]["name"] == "alice"
    assert "created_at" in orch._agents["alice"]
    assert orch._message_inboxes["alice"] == []


def test_register_agent_empty_name_uses_uuid(clean_state):
    aid = orch._register_agent("", "tester")
    assert len(aid) == 8
    assert aid in orch._agents


def test_register_agent_twice_keeps_single_inbox(clean_state):
    orch._register_agent("bob", "reviewer")
    orch._send_message("bob", "x", "hello")
    orch._register_agent("bob", "reviewer")  # re-register: inbox already exists
    assert len(orch._message_inboxes["bob"]) == 1


# ── _send_message ─────────────────────────────────────────────────────────


def test_send_message_direct(clean_state):
    orch._register_agent("alice", "coder")
    assert orch._send_message("alice", "main_agent", "hi there") is True
    inbox = orch._message_inboxes["alice"]
    assert len(inbox) == 1
    assert inbox[0]["from"] == "main_agent"
    assert inbox[0]["message"] == "hi there"
    assert inbox[0]["type"] == "text"
    assert "timestamp" in inbox[0]


def test_send_message_unknown_recipient_fails(clean_state):
    assert orch._send_message("ghost", "main_agent", "hi") is False


def test_send_message_broadcast_skips_sender(clean_state):
    orch._register_agent("a", "coder")
    orch._register_agent("b", "tester")
    assert orch._send_message("*", "a", "broadcast!", message_type="status_update") is True
    assert orch._message_inboxes["a"] == []
    assert len(orch._message_inboxes["b"]) == 1
    assert orch._message_inboxes["b"][0]["type"] == "status_update"


def test_send_message_broadcast_to_unregistered_sender(clean_state):
    orch._register_agent("a", "coder")
    assert orch._send_message("*", "main_agent", "hello all") is True
    assert len(orch._message_inboxes["a"]) == 1


# ── AgentHandoffTool ──────────────────────────────────────────────────────


async def test_handoff_success_uses_config_defaults(clean_state, monkeypatch):
    fake_cls, cfg = _install_fake_agent(monkeypatch)
    out = await AgentHandoffTool().execute(role="coder", task="write a parser")
    assert not out.error
    assert out.title == "Coder Result"
    assert out.metadata["role"] == "coder"
    assert out.metadata["agent"].startswith("coder-")
    assert out.metadata["turns"] == 7
    assert "RESULT[" in out.text
    inst = fake_cls.instances[-1]
    assert inst.kwargs["model"] == cfg["model"]
    assert inst.kwargs["base_url"] == cfg["base_url"]
    assert inst.kwargs["api_key"] == cfg["api_key"]
    assert inst.max_turns_used == 15  # min(30, 15)
    assert "You are a CODER." in inst.prompt
    assert "write a parser" in inst.prompt


async def test_handoff_model_and_base_url_override(clean_state, monkeypatch):
    fake_cls, _cfg = _install_fake_agent(monkeypatch)
    out = await AgentHandoffTool().execute(
        role="researcher", task="t", model="  phi3  ", base_url=" http://other "
    )
    assert not out.error
    inst = fake_cls.instances[-1]
    assert inst.kwargs["model"] == "phi3"
    assert inst.kwargs["base_url"] == "http://other"
    assert "You are a RESEARCHER." in inst.prompt


async def test_handoff_unknown_role_has_empty_prompt_prefix(clean_state, monkeypatch):
    fake_cls, _ = _install_fake_agent(monkeypatch)
    await AgentHandoffTool().execute(role="wizard", task="do magic")
    inst = fake_cls.instances[-1]
    assert inst.prompt.startswith("\n\nTask: do magic")


async def test_handoff_run_failure_returns_error(clean_state, monkeypatch):
    _install_fake_agent(monkeypatch, fail=True)
    out = await AgentHandoffTool().execute(role="tester", task="test it")
    assert out.error
    assert "Tester error: boom" in out.text


# ── TeamCreateTool ────────────────────────────────────────────────────────


async def test_team_create_requires_agents():
    out = await TeamCreateTool().execute(team_name="t", description="d", agents=None)
    assert out.error
    assert "at least one agent" in out.text


async def test_team_create_runs_agents_in_parallel(clean_state, monkeypatch):
    fake_cls, cfg = _install_fake_agent(monkeypatch)
    agents = [
        {
            "name": "dev",
            "role": "coder",
            "task": "build it",
            "max_turns": 50,
            "model": "phi3",
            "base_url": "http://x",
        },
        {"name": "qa", "role": "tester", "task": "test it"},
    ]
    out = await TeamCreateTool().execute(team_name="squad", description="d", agents=agents)
    assert not out.error
    assert out.title == "Team: squad (2 agents)"
    assert "## dev (coder)" in out.text
    assert "## qa (tester)" in out.text
    assert "---" in out.text
    assert out.metadata["agent_count"] == 2
    assert len(orch._teams) == 1
    team = next(iter(orch._teams.values()))
    assert team["name"] == "squad"
    assert "dev" in orch._agents and "qa" in orch._agents
    dev = fake_cls.instances[0]
    assert dev.kwargs["model"] == "phi3"
    assert dev.kwargs["base_url"] == "http://x"
    assert dev.max_turns_used == 20  # capped at 20
    qa = fake_cls.instances[1]
    assert qa.kwargs["model"] == cfg["model"]
    assert qa.max_turns_used == 10  # default


async def test_team_create_agent_failure_isolated(clean_state, monkeypatch):
    _install_fake_agent(monkeypatch, fail=True)
    agents = [{"name": "dev", "role": "coder", "task": "build it"}]
    out = await TeamCreateTool().execute(team_name="squad", agents=agents)
    assert not out.error
    assert "## dev (coder)" in out.text
    assert "Error: boom" in out.text


# ── SendMessageTool / ReceiveMessageTool / ListAgentsTool ────────────────


async def test_send_message_tool_direct_broadcast_and_unknown(clean_state):
    tool = SendMessageTool()
    orch._register_agent("alice", "coder")
    out = await tool.execute(to="alice", message="ping")
    assert not out.error
    assert out.text == "Message sent to alice"
    assert out.title == "Message → alice"
    assert out.metadata == {"to": "alice", "type": "text"}

    out = await tool.execute(to="*", message="all", message_type="shutdown_request")
    assert not out.error
    assert len(orch._message_inboxes["alice"]) == 2

    out = await tool.execute(to="ghost", message="ping")
    assert out.error
    assert "unknown agent" in out.text


async def test_receive_message_tool_empty(clean_state):
    out = await ReceiveMessageTool().execute(agent_name="main_agent")
    assert not out.error
    assert out.text == "No pending messages."
    assert out.title == "Inbox (0)"


async def test_receive_message_tool_with_messages(clean_state):
    orch._register_agent("alice", "coder")
    orch._send_message("alice", "bob", "first")
    orch._send_message("alice", "bob", "second", message_type="status_update")
    out = await ReceiveMessageTool().execute(agent_name="alice")
    assert not out.error
    assert out.title == "Inbox (2 messages)"
    assert out.metadata == {"count": 2}
    assert "[0] From: bob | Type: text" in out.text
    assert "first" in out.text
    assert "[1] From: bob | Type: status_update" in out.text
    assert "second" in out.text
    # inbox is drained
    assert orch._message_inboxes.get("alice", []) == []


async def test_list_agents_tool(clean_state):
    orch._register_agent("alice", "coder")
    orch._register_agent("bob", "tester")
    orch._send_message("bob", "alice", "pending msg")
    orch._teams["team-1"] = {"name": "squad", "agents": ["alice", "bob"]}
    out = await ListAgentsTool().execute()
    assert not out.error
    assert out.title == "Known Agents & Teams"
    assert "- alice (coder) | 0 pending msgs" in out.text
    assert "- bob (tester) | 1 pending msgs" in out.text
    assert "- team-1: squad (2 agents)" in out.text
    assert out.metadata == {"agents": 2, "teams": 1}


async def test_list_agents_tool_empty(clean_state):
    out = await ListAgentsTool().execute()
    assert "## Agents:" in out.text
    assert "## Teams:" in out.text
    assert out.metadata == {"agents": 0, "teams": 0}
