"""A4: task-scoped tool pruning.

Every turn shipped all ~114 tool schemas (~14K tokens) plus a full
tool menu in the system prompt. Per-turn schemas are now scoped to the
task: an always-advertised core, keyword-matched families, recently
used tools, and anything unmapped (dynamic MCP tools stay safe).
``CODING_AGENT_NO_PRUNE=1`` restores the full set. Execution is
unchanged — pruning only affects what is advertised.
"""

from __future__ import annotations

from unittest.mock import patch

import agent  # noqa: F401  (register the full production tool set)


class FakeLLMClient:
    def __init__(self, script):
        self._script = list(script)
        self.model = "fake-model"
        self.calls = []

    async def chat_stream(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        if not self._script:
            return {"content": "default final answer"}
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _make_agent(**kwargs):
    with patch("llm.ProviderRouter"):
        from core.agent_loop import CodingAgent

        ag = CodingAgent(api_key="test-key-not-a-secret", model="test-model", **kwargs)

    async def _noop_extract(user_message):
        return None

    async def _noop_refresh(query):
        return False

    ag._extract_session_memories = _noop_extract
    ag._refresh_memory_context = _noop_refresh
    return ag


NAMES = [
    "read",
    "write",
    "edit",
    "glob",
    "grep",
    "bash",
    "askuserquestion",
    "gitcommit",
    "gitpush",
    "gitstatus",
    "websearch",
    "webfetch",
    "browserscreenshot",
    "browsernavigate",
    "desktopscreenshot",
    "schedulecreate",
    "skillinstall",
    "lspdefinition",
    "memoryrecall",
    "datetime",
    "subagent",
]


class TestSelection:
    def test_core_always_advertised(self):
        ag = _make_agent()
        ag._current_task_text = "fix the bug"
        selected = ag._select_tool_names(NAMES)
        for must in ("read", "write", "edit", "glob", "grep", "bash", "subagent"):
            assert must in selected

    def test_keywords_add_families(self):
        ag = _make_agent()
        ag._current_task_text = "commit and push the branch"
        selected = ag._select_tool_names(NAMES)
        assert "gitcommit" in selected
        assert "gitpush" in selected

        ag._current_task_text = "what time is it"
        assert "datetime" in ag._select_tool_names(NAMES)

    def test_unrelated_families_dropped(self):
        ag = _make_agent()
        ag._current_task_text = "fix the parser bug"
        selected = ag._select_tool_names(NAMES)
        for dropped in (
            "browserscreenshot",
            "browsernavigate",
            "desktopscreenshot",
            "schedulecreate",
            "skillinstall",
            "lspdefinition",
            "memoryrecall",
        ):
            assert dropped not in selected

    def test_recent_tools_retained(self):
        ag = _make_agent()
        ag._current_task_text = "fix the parser bug"
        ag._recent_tools = ["gitstatus"]
        assert "gitstatus" in ag._select_tool_names(NAMES)

    def test_unknown_tools_preserved(self):
        ag = _make_agent()
        ag._current_task_text = "fix the parser bug"
        assert "mcp_custom_thing" in ag._select_tool_names([*NAMES, "mcp_custom_thing"])

    def test_goal_text_scopes_tools(self):
        ag = _make_agent()
        ag._current_task_text = "go"
        ag.set_goal("migrate the sqlite database")
        selected = ag._select_tool_names([*NAMES, "sqlite"])
        assert "sqlite" in selected

    def test_kill_switch_restores_full_set(self, monkeypatch):
        from tools.registry import registry

        ag = _make_agent()
        monkeypatch.setenv("CODING_AGENT_NO_PRUNE", "1")
        assert ag._select_tool_names(NAMES) is None
        assert registry.openai_tools(None) == registry.openai_tools()


class TestRegistryFilter:
    def test_openai_tools_subset(self):
        from tools.registry import registry

        schemas = registry.openai_tools(["read", "bash"])
        assert {s["function"]["name"].lower() for s in schemas} == {"read", "bash"}


class TestRunIntegration:
    async def test_run_sends_pruned_schemas(self):
        from tools.registry import registry

        ag = _make_agent()
        ag._current_task_text = ""  # run() overwrites from the message
        client = FakeLLMClient([{"content": "done and complete."}])
        ag.llm_client = client
        await ag.run("fix the parser bug", max_turns=2)
        sent = client.calls[0]["kwargs"]["tools"]
        names = {s["function"]["name"].lower() for s in sent}
        assert "read" in names
        assert "browserscreenshot" not in names
        assert len(sent) < len(registry.openai_tools())
