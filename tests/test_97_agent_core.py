"""LuckyD 9.7 agent-core ports: permission modes, context compaction,
classified retry, and session autosave.

Covers core/agent_loop.py (permission_mode gate, _is_retryable/_with_retry,
run()-loop autosave) and core/compaction.py (should_compact/compact/
maybe_compact).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# Intentionally fake API key for tests (not a real secret).
TEST_API_KEY = "test-key-not-a-secret"


def _make_agent(**kwargs):
    with patch("llm.ProviderRouter"):
        from core.agent_loop import CodingAgent

        ag = CodingAgent(api_key=TEST_API_KEY, model="test-model", **kwargs)
        # Memory extraction spawns a real background LLM call; neutralize it.
        ag._extract_session_memories = AsyncMock()
        # Legacy truncation path; keep _execute_tool tests focused.
        ag._result_handler = None
        return ag


@pytest.fixture
def agent():
    """Agent isolated from global hook state (restored after the test)."""
    ag = _make_agent()
    saved_before = list(ag.hooks.before_tool)
    saved_after = list(ag.hooks.after_tool)
    ag.hooks.before_tool = []
    ag.hooks.after_tool = []
    yield ag
    ag.hooks.before_tool = saved_before
    ag.hooks.after_tool = saved_after


class _FakeTool:
    """Minimal ToolBase stand-in with a configurable permission level."""

    def __init__(self, name, permission_level):
        self.name = name
        self.permission_level = permission_level
        self.timeout_sec = None
        self.parameters = {}
        self.executed = False

    async def execute(self, **kwargs):
        self.executed = True
        return SimpleNamespace(text="ok", error=False)


class _DenyingApprovalHook:
    """Stand-in for the ApprovalHook: denies everything, like a user saying no."""

    name = "approval"

    def __call__(self, tool_name, tool_args, ctx):
        return {
            "role": "tool",
            "tool_call_id": tool_args.get("_id", "unknown"),
            "content": "Error: denied by test approval hook",
        }


async def _run_tool(agent, tool_name, permission_level):
    """Run _execute_tool against a fake registered tool; return (tool, result)."""
    from tools.registry import registry

    tool = _FakeTool(tool_name, permission_level)
    with patch.object(registry, "get", return_value=tool):
        result = await agent._execute_tool(tool_name, {"_id": "c1"})
    return tool, result


class TestPermissionModes:
    def test_default_mode_is_auto(self):
        assert _make_agent().permission_mode == "auto"

    def test_mode_is_stored(self):
        assert _make_agent(permission_mode="off").permission_mode == "off"

    def test_invalid_mode_rejected(self):
        with pytest.raises(ValueError):
            _make_agent(permission_mode="yolo")

    async def test_bypass_permissions_allows_all_but_blocked(self, agent):
        agent.permission_mode = "bypassPermissions"
        for level, expect_ok in [
            ("ALWAYS_ALLOW", True),
            ("NORMAL", True),
            ("REQUIRES_APPROVAL", True),
            ("BLOCKED", False),
        ]:
            tool, result = await _run_tool(agent, "Bash", level)
            assert tool.executed is expect_ok, level
            assert result["content"].startswith("Error") is not expect_ok

    async def test_bypass_permissions_skips_approval_hook(self, agent):
        """The mode gate runs BEFORE the approval hook: pre-allowed calls never ask."""
        agent.permission_mode = "bypassPermissions"
        agent.hooks.before_tool.append(_DenyingApprovalHook())
        tool, result = await _run_tool(agent, "Bash", "REQUIRES_APPROVAL")
        assert tool.executed is True
        assert result["content"] == "ok"

    async def test_accept_edits(self, agent):
        agent.permission_mode = "acceptEdits"
        cases = [
            ("Write", "REQUIRES_APPROVAL", True),  # file-editing tool, auto-allowed
            ("Edit", "NORMAL", True),  # NORMAL always auto-allowed
            ("Read", "ALWAYS_ALLOW", True),
            ("Bash", "REQUIRES_APPROVAL", False),  # not an edit tool -> blocked
            ("Evil", "BLOCKED", False),
        ]
        for name, level, expect_ok in cases:
            tool, result = await _run_tool(agent, name, level)
            assert tool.executed is expect_ok, (name, level)
            if not expect_ok:
                assert "blocked by permission mode" in result["content"]

    async def test_auto_allows_safe_levels_and_defers_rest(self, agent):
        agent.permission_mode = "auto"
        # Harmless levels skip the hook entirely.
        tool, _ = await _run_tool(agent, "Read", "ALWAYS_ALLOW")
        assert tool.executed is True
        tool, _ = await _run_tool(agent, "Edit", "NORMAL")
        assert tool.executed is True
        # REQUIRES_APPROVAL reaches the hook: denied when the hook denies...
        agent.hooks.before_tool.append(_DenyingApprovalHook())
        tool, result = await _run_tool(agent, "Bash", "REQUIRES_APPROVAL")
        assert tool.executed is False
        assert "denied by test approval hook" in result["content"]
        # ...and BLOCKED never runs even with no hook to stop it.
        agent.hooks.before_tool.clear()
        tool, result = await _run_tool(agent, "Evil", "BLOCKED")
        assert tool.executed is False
        assert "blocked by permission mode" in result["content"]

    async def test_default_defers_everything_to_hook(self, agent):
        """'default' preserves current behavior: the approval hook decides it all."""
        agent.permission_mode = "default"
        agent.hooks.before_tool.append(_DenyingApprovalHook())
        for name, level in [
            ("Read", "ALWAYS_ALLOW"),
            ("Edit", "NORMAL"),
            ("Bash", "REQUIRES_APPROVAL"),
        ]:
            tool, result = await _run_tool(agent, name, level)
            assert tool.executed is False, name
            assert "denied by test approval hook" in result["content"]

    async def test_off_allows_only_readonly(self, agent):
        agent.permission_mode = "off"
        cases = [
            ("Read", "ALWAYS_ALLOW", True),
            ("GetStatus", "NORMAL", True),  # read-like name
            ("ListTasks", "NORMAL", True),  # read-like name
            ("SearchWeb", "NORMAL", True),  # read-like name
            ("Write", "REQUIRES_APPROVAL", False),
            ("DeleteAll", "NORMAL", False),  # mutating name, even at NORMAL level
            ("Evil", "BLOCKED", False),
        ]
        for name, level, expect_ok in cases:
            tool, result = await _run_tool(agent, name, level)
            assert tool.executed is expect_ok, (name, level)
            if not expect_ok:
                assert "blocked by permission mode" in result["content"]


class TestCompaction:
    @staticmethod
    def _messages(n_middle=12, n_recent=2):
        msgs = [{"role": "system", "content": "system prompt"}]
        msgs += [{"role": "user", "content": f"old message {i}"} for i in range(n_middle)]
        msgs += [{"role": "user", "content": f"recent {i}"} for i in range(n_recent)]
        return msgs

    def test_should_compact_thresholds(self):
        from core.compaction import should_compact

        assert should_compact(SimpleNamespace(turn_count=30, messages=[])) is True
        assert should_compact(SimpleNamespace(turn_count=29, messages=[])) is False
        many = [{"role": "user", "content": "x"}] * 60
        assert should_compact(SimpleNamespace(turn_count=1, messages=many)) is True
        few = [{"role": "user", "content": "x"}] * 59
        assert should_compact(SimpleNamespace(turn_count=1, messages=few)) is False
        assert should_compact(SimpleNamespace(turn_count=1, messages=[])) is False

    def test_should_compact_custom_thresholds(self):
        from core.compaction import should_compact

        ag = SimpleNamespace(turn_count=5, messages=[])
        assert should_compact(ag, max_turns=5) is True
        assert should_compact(ag, max_turns=6) is False

    async def test_compact_with_fake_summarizer(self):
        from core.compaction import compact

        agent = SimpleNamespace(turn_count=99, messages=self._messages())
        seen = {}

        def fake_summarizer(middle):
            seen["middle"] = middle
            return "SUMMARY TEXT"

        assert await compact(agent, keep_recent_turns=2, summarizer=fake_summarizer) is True
        # System prompt and recent turns excluded from summarization.
        assert len(seen["middle"]) == 12
        # Result shape: [system, summary system msg, *recent].
        assert agent.messages[0] == {"role": "system", "content": "system prompt"}
        assert agent.messages[1] == {
            "role": "system",
            "content": "CONVERSATION SUMMARY SO FAR:\nSUMMARY TEXT",
        }
        assert [m["content"] for m in agent.messages[2:]] == ["recent 0", "recent 1"]

    async def test_compact_supports_async_summarizer(self):
        from core.compaction import compact

        agent = SimpleNamespace(turn_count=99, messages=self._messages())

        async def fake_summarizer(middle):
            return "ASYNC SUMMARY"

        assert await compact(agent, summarizer=fake_summarizer) is True
        assert agent.messages[1]["content"].endswith("ASYNC SUMMARY")

    async def test_compact_failure_leaves_messages_untouched(self):
        from core.compaction import compact

        agent = SimpleNamespace(turn_count=99, messages=self._messages())
        before = list(agent.messages)

        def bad_summarizer(middle):
            raise RuntimeError("boom")

        assert await compact(agent, summarizer=bad_summarizer) is False
        assert agent.messages == before

    async def test_compact_llm_failure_leaves_messages_untouched(self):
        from core.compaction import compact

        agent = SimpleNamespace(
            turn_count=99,
            messages=self._messages(),
            llm_client=AsyncMock(),
        )
        agent.llm_client.chat_nonstreaming = AsyncMock(side_effect=RuntimeError("no net"))
        before = list(agent.messages)
        assert await compact(agent) is False
        assert agent.messages == before

    async def test_compact_uses_llm_client_when_no_summarizer(self):
        from core.compaction import compact

        agent = SimpleNamespace(
            turn_count=99,
            messages=self._messages(),
            llm_client=AsyncMock(),
        )
        agent.llm_client.chat_nonstreaming = AsyncMock(return_value={"content": "LLM SUMMARY"})
        assert await compact(agent, keep_recent_turns=2) is True
        assert agent.messages[1]["content"] == "CONVERSATION SUMMARY SO FAR:\nLLM SUMMARY"
        # The summarization prompt went out as a non-streaming chat call.
        sent = agent.llm_client.chat_nonstreaming.await_args.kwargs["messages"]
        assert sent[0]["role"] == "system"
        assert "old message 0" in sent[1]["content"]
        assert "recent 0" not in sent[1]["content"]  # recent turns not summarized

    async def test_compact_too_short_is_noop(self):
        from core.compaction import compact

        agent = SimpleNamespace(turn_count=99, messages=[{"role": "system", "content": "s"}])
        assert await compact(agent, summarizer=lambda m: "x") is False

    async def test_maybe_compact_never_raises(self):
        from core.compaction import maybe_compact

        class Broken:
            @property
            def turn_count(self):
                raise RuntimeError("boom")

        assert await maybe_compact(Broken()) is False
        # Below threshold -> False, summarizer never invoked.
        agent = SimpleNamespace(turn_count=2, messages=[{"role": "system", "content": "s"}])
        assert await maybe_compact(agent, summarizer=lambda m: 1 / 0) is False


def _http_status_error(status):
    req = httpx.Request("POST", "https://example.com/v1/chat/completions")
    resp = httpx.Response(status, request=req)
    return httpx.HTTPStatusError(f"HTTP {status}", request=req, response=resp)


class TestRetryClassification:
    def test_is_retryable(self):
        from core.agent_loop import _is_retryable

        # Retryable: 429, 5xx, timeouts, connection errors.
        assert _is_retryable(_http_status_error(429)) is True
        assert _is_retryable(_http_status_error(500)) is True
        assert _is_retryable(_http_status_error(502)) is True
        assert _is_retryable(_http_status_error(503)) is True
        assert _is_retryable(httpx.ReadTimeout("timed out")) is True
        assert _is_retryable(httpx.ConnectTimeout("timed out")) is True
        assert _is_retryable(httpx.WriteTimeout("timed out")) is True
        assert _is_retryable(httpx.ConnectError("refused")) is True
        # Fatal: 400/401/403 and other client errors fail fast.
        assert _is_retryable(_http_status_error(400)) is False
        assert _is_retryable(_http_status_error(401)) is False
        assert _is_retryable(_http_status_error(403)) is False
        assert _is_retryable(_http_status_error(404)) is False
        assert _is_retryable(ValueError("nope")) is False

    async def test_with_retry_succeeds_after_transient_failures(self, monkeypatch):
        from core.agent_loop import _with_retry

        sleeps = []

        async def fake_sleep(d):
            sleeps.append(d)

        monkeypatch.setattr("asyncio.sleep", fake_sleep)
        calls = {"n": 0}

        async def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise _http_status_error(503)
            return "ok"

        assert await _with_retry(flaky)() == "ok"
        assert calls["n"] == 3
        assert sleeps == [1.0, 2.0]  # exponential backoff 1, 2, ...

    async def test_with_retry_fails_fast_on_fatal(self, monkeypatch):
        from core.agent_loop import _with_retry

        sleeps = []

        async def fake_sleep(d):
            sleeps.append(d)

        monkeypatch.setattr("asyncio.sleep", fake_sleep)

        async def fatal():
            raise _http_status_error(401)

        with pytest.raises(httpx.HTTPStatusError):
            await _with_retry(fatal)()
        assert sleeps == []

    async def test_with_retry_gives_up_after_max_retries(self, monkeypatch):
        from core.agent_loop import _with_retry

        sleeps = []

        async def fake_sleep(d):
            sleeps.append(d)

        monkeypatch.setattr("asyncio.sleep", fake_sleep)
        calls = {"n": 0}

        async def always_down():
            calls["n"] += 1
            raise _http_status_error(500)

        with pytest.raises(httpx.HTTPStatusError):
            await _with_retry(always_down)()
        assert calls["n"] == 6  # initial attempt + 5 retries
        assert sleeps == [1.0, 2.0, 4.0, 8.0, 16.0]

    async def test_with_retry_respects_elapsed_cap(self, monkeypatch):
        from core.agent_loop import _with_retry

        sleeps = []

        async def fake_sleep(d):
            sleeps.append(d)

        monkeypatch.setattr("asyncio.sleep", fake_sleep)
        # start=0.0, then the clock jumps past the 120s budget on retry #2.
        monotonic = MagicMock(side_effect=[0.0, 0.0, 200.0])
        monkeypatch.setattr("core.agent_loop._monotonic", monotonic)

        async def always_down():
            raise _http_status_error(503)

        with pytest.raises(httpx.HTTPStatusError):
            await _with_retry(always_down)()
        assert sleeps == [1.0]

    def test_with_retry_preserves_signature(self):
        from core.agent_loop import _with_retry

        async def post(url, payload):
            """Post something."""
            return url

        wrapped = _with_retry(post)
        assert wrapped.__name__ == "post"
        assert wrapped.__doc__ == "Post something."


class TestSessionAutosave:
    @staticmethod
    def _tool_call(name, call_id, arguments="{}"):
        return {
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        }

    async def test_autosave_every_five_turns(self, agent):
        from llm import LLMResult

        agent.save_session = MagicMock(return_value="/tmp/x.json")
        agent.llm_client = AsyncMock()
        turns = [
            LLMResult(content="", tool_calls=[self._tool_call("Read", f"c{i}")]) for i in range(5)
        ]
        turns.append(LLMResult(content="done"))
        agent.llm_client.chat_stream = AsyncMock(side_effect=turns)
        agent._execute_tool = AsyncMock(
            return_value={"role": "tool", "tool_call_id": "c", "content": "ok"}
        )
        result = await agent.run("do the thing", max_turns=10)
        assert result == "done"
        assert agent.turn_count == 6
        # Exactly one autosave: at turn 5 (turns 1-4 and 6 are not multiples of 5).
        assert agent.save_session.call_count == 1

    async def test_autosave_failure_does_not_break_loop(self, agent):
        from llm import LLMResult

        agent.save_session = MagicMock(side_effect=RuntimeError("disk full"))
        agent.llm_client = AsyncMock()
        turns = [
            LLMResult(content="", tool_calls=[self._tool_call("Read", f"c{i}")]) for i in range(5)
        ]
        turns.append(LLMResult(content="done"))
        agent.llm_client.chat_stream = AsyncMock(side_effect=turns)
        agent._execute_tool = AsyncMock(
            return_value={"role": "tool", "tool_call_id": "c", "content": "ok"}
        )
        result = await agent.run("do the thing", max_turns=10)
        assert result == "done"
