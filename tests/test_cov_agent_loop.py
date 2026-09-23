"""Coverage tests for core/agent_loop.py.

Drives the agent loop, tool execution, retry/fallback paths, memory
extraction, and session persistence with hand-written fakes — no mocks of
the units under test. Complements tests/test_agent_loop_hardening.py and
tests/test_night4_agent_core.py.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from core.agent_loop import CodingAgent
from core.hooks import reset_hooks
from core.types import AgentEventType
from tools.base import ToolBase, ToolOutput
from tools.registry import registry

# Intentionally fake API key for tests (not a real secret).
TEST_API_KEY = "test-key-12345"


# ── Hand-written fakes ──────────────────────────────────────────────────


class FakeLLMClient:
    """Fake LLM client with a scripted response queue.

    Queue items may be dicts, LLMResult objects, None, or exception
    instances (which are raised when dequeued).
    """

    def __init__(self, script):
        self._script = list(script)
        self.calls: list[dict] = []
        self.model = "fake-model"

    async def chat_stream(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        if not self._script:
            return {"content": "default final answer"}
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FakeMemory:
    """Fake memory store recording add() calls."""

    def __init__(self, context: str = ""):
        self._context = context
        self.added: list[dict] = []

    def get_context(self, query, limit=3):
        return self._context

    def add(self, content="", tags=None, source=None):
        self.added.append({"content": content, "tags": tags or [], "source": source})


class FakeSessionStore:
    """Fake session store recording save() calls."""

    def __init__(self, path: str = "/tmp/cov-agent-loop-session.json", fail: bool = False):
        self._path = Path(path)
        self._fail = fail
        self.saved: list[dict] = []

    def save(self, conversation_id, messages, model, provider, meta):
        if self._fail:
            raise RuntimeError("disk full")
        self.saved.append(
            {
                "conversation_id": conversation_id,
                "messages": messages,
                "model": model,
                "provider": provider,
                "meta": meta,
            }
        )
        return self._path


class _OkTool(ToolBase):
    name = "cov_ok_tool"
    description = "test tool that always succeeds"
    parameters = {}

    async def execute(self, **kwargs) -> ToolOutput:
        return ToolOutput(text="ok-result")


class _OkToolWithTimeout(ToolBase):
    name = "cov_ok_timeout_tool"
    description = "test tool with a per-tool timeout override"
    parameters = {}
    timeout_sec = 60.0

    async def execute(self, **kwargs) -> ToolOutput:
        return ToolOutput(text="timeout-override-ok")


class _SlowTool(ToolBase):
    name = "cov_slow_tool"
    description = "test tool that hangs until cancelled"
    parameters = {}

    async def execute(self, **kwargs) -> ToolOutput:
        await asyncio.sleep(30)
        return ToolOutput(text="never reached")


class _BoomTool(ToolBase):
    name = "cov_boom_tool"
    description = "test tool that raises"
    parameters = {}

    async def execute(self, **kwargs) -> ToolOutput:
        raise RuntimeError("kaboom")


class _ErrTool(ToolBase):
    name = "cov_err_tool"
    description = "test tool returning an unprefixed error result"
    parameters = {}

    async def execute(self, **kwargs) -> ToolOutput:
        return ToolOutput(text="plain failure", error=True)


class _PrefixedErrTool(ToolBase):
    name = "cov_prefixed_err_tool"
    description = "test tool returning an already-prefixed error result"
    parameters = {}

    async def execute(self, **kwargs) -> ToolOutput:
        return ToolOutput(text="Error: already prefixed", error=True)


class _BigTool(ToolBase):
    name = "cov_big_tool"
    description = "test tool returning oversized output"
    parameters = {}

    async def execute(self, **kwargs) -> ToolOutput:
        return ToolOutput(text="x" * 5000)


class _NeedsArgTool(ToolBase):
    name = "cov_needs_arg_tool"
    description = "test tool requiring an argument"
    parameters = {}

    async def execute(self, required_param: str) -> ToolOutput:
        return ToolOutput(text="ok")


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_agent(**kwargs) -> CodingAgent:
    """Build a CodingAgent with network/LLM/memory side effects neutralized."""
    with patch("llm.ProviderRouter"):
        ag = CodingAgent(api_key=TEST_API_KEY, model="test-model", temperature=0.0, **kwargs)

    async def _noop_extract(user_message):
        return None

    async def _noop_refresh(query):
        return False

    ag._extract_session_memories = _noop_extract
    ag._refresh_memory_context = _noop_refresh
    ag.llm_client = FakeLLMClient([{"content": "done"}])
    # The tool-result backpressure rate limiter is process-global; keep it
    # from firing between fast successive test executions.
    if ag._result_handler is not None:
        ag._result_handler.backpressure._last_result_time = 0.0
    return ag


def _tool_call(name, call_id="c1", arguments="{}"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _seeded_messages():
    return [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "hello"},
    ]


@pytest.fixture
def tool_registry():
    """Register fake tools; unregister them and reset hooks afterwards."""
    reset_hooks()
    names: list[str] = []

    def _register(tool):
        registry.register(tool)
        names.append(tool.name)
        return tool

    yield _register
    for name in names:
        registry._tools.pop(name, None)
    reset_hooks()


@pytest.fixture
def clean_hooks():
    reset_hooks()
    yield
    reset_hooks()


# ── __init__ paths ──────────────────────────────────────────────────────


class TestInitPaths:
    def test_project_detector_failure_leaves_project_info_none(self, monkeypatch):
        class _BoomDetector:
            def detect(self, path):
                raise RuntimeError("no project here")

        monkeypatch.setattr("project.ProjectDetector", _BoomDetector)
        ag = _make_agent()
        assert ag._project_info is None

    def test_result_handler_failure_falls_back_to_none(self, monkeypatch):
        def _boom(**kwargs):
            raise RuntimeError("no handler")

        monkeypatch.setattr("core.tool_result_handler.get_tool_result_handler", _boom)
        ag = _make_agent()
        assert ag._result_handler is None

    def test_cost_tracker_property(self):
        ag = _make_agent()
        assert ag.cost_tracker is ag._router.cost_tracker

    def test_stream_callbacks_get_set(self):
        ag = _make_agent()
        seen: list = []
        stream_cb = seen.append
        ag.stream_callback = stream_cb
        assert ag.callbacks.stream_token is stream_cb
        assert ag.stream_callback is stream_cb
        think_cb = seen.append
        ag.think_callback = think_cb
        assert ag.callbacks.stream_think_token is think_cb
        assert ag.think_callback is think_cb

    def test_provider_name_known_and_unknown(self):
        ag = _make_agent()
        ag._provider_config.provider = "openai"
        assert ag.provider_name == "OpenAI"
        ag._provider_config.provider = "weird-provider"
        assert ag.provider_name == "weird-provider"

    def test_switch_provider_rebinds_client(self):
        from llm import LLMConfig

        ag = _make_agent()
        new = LLMConfig(
            api_key="k2",
            base_url="https://example.com/v1",
            model="gpt-x",
            provider="openai",
            temperature=0.5,
            max_tokens=100,
        )
        ag.switch_provider(new)
        assert ag._provider_config is new
        assert ag.model == "gpt-x"
        assert ag.api_key == "k2"
        assert ag.base_url == "https://example.com/v1"
        assert ag.llm_client.model == "gpt-x"
        assert ag.provider_name == "OpenAI"


class TestTokenResolver:
    def test_non_clinepass_returns_none(self):
        ag = _make_agent()
        assert ag._make_token_resolver() is None

    def test_provider_check_exception_returns_none(self):
        ag = _make_agent()

        class _RaisingConfig:
            @property
            def provider(self):
                raise RuntimeError("config exploded")

        ag._provider_config = _RaisingConfig()
        assert ag._make_token_resolver() is None

    def test_clinepass_returns_working_resolver(self, monkeypatch):
        from config import get_config

        cfg = dict(get_config())
        cfg["provider"] = "clinepass"
        monkeypatch.setattr("core.agent_loop.get_config", lambda: cfg)
        ag = _make_agent()
        resolver = ag._make_token_resolver()
        assert callable(resolver)
        # The real get_cline_token never raises; it returns a token or None.
        token = resolver()
        assert token is None or isinstance(token, str)

    def test_clinepass_resolver_exception_returns_none(self, monkeypatch):
        from config import get_config

        cfg = dict(get_config())
        cfg["provider"] = "clinepass"
        monkeypatch.setattr("core.agent_loop.get_config", lambda: cfg)

        def _raise():
            raise RuntimeError("token store exploded")

        import llm.providers.clinepass as clinepass_mod

        monkeypatch.setattr(
            clinepass_mod.ClinePassProvider, "get_cline_token", staticmethod(_raise)
        )
        ag = _make_agent()
        resolver = ag._make_token_resolver()
        assert resolver() is None


class TestEmitEvent:
    def test_emit_event_calls_callback(self):
        ag = _make_agent()
        events = []
        ag.callbacks.on_event = events.append
        ag._emit_event(AgentEventType.TURN_START, {"turn": 3})
        assert len(events) == 1
        assert events[0].type is AgentEventType.TURN_START
        assert events[0].payload == {"turn": 3}
        assert events[0].turn == ag.turn_count

    def test_emit_event_without_callback_is_noop(self):
        ag = _make_agent()
        ag.callbacks.on_event = None
        ag._emit_event(AgentEventType.TURN_START, {"turn": 1})  # must not raise


# ── _execute_tool paths ─────────────────────────────────────────────────


class TestExecuteTool:
    @pytest.mark.asyncio
    async def test_before_tool_hook_can_block(self, tool_registry):
        tool_registry(_OkTool())
        ag = _make_agent()

        def _blocker(name, args, ctx):
            assert ctx.turn == ag.turn_count
            return {
                "role": "tool",
                "tool_call_id": args.get("_id", "?"),
                "content": "blocked by policy",
            }

        ag.hooks.register_before_tool(_blocker)
        msg = await ag._execute_tool("cov_ok_tool", {"_id": "c9"})
        assert msg["content"] == "blocked by policy"
        assert msg["tool_call_id"] == "c9"

    @pytest.mark.asyncio
    async def test_before_tool_hook_exception_does_not_stop_tool(self, tool_registry, capsys):
        tool_registry(_OkTool())
        ag = _make_agent()

        def _raiser(name, args, ctx):
            raise RuntimeError("hook exploded")

        ag.hooks.register_before_tool(_raiser)
        msg = await ag._execute_tool("cov_ok_tool", {"_id": "c1"})
        assert msg["content"] == "ok-result"
        assert "[HOOK ERR]" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_before_tool_hook_returning_none_continues(self, tool_registry):
        tool_registry(_OkTool())
        ag = _make_agent()

        def _pass_through(name, args, ctx):
            return None

        ag.hooks.register_before_tool(_pass_through)
        msg = await ag._execute_tool("cov_ok_tool", {"_id": "c1"})
        assert msg["content"] == "ok-result"

    @pytest.mark.asyncio
    async def test_tool_argument_error(self, tool_registry):
        tool_registry(_NeedsArgTool())
        ag = _make_agent()
        msg = await ag._execute_tool("cov_needs_arg_tool", {"_id": "c1"})
        assert msg["content"].startswith("Error: Tool argument error:")
        assert "required_param" in msg["content"]
        assert "Expected: {}" in msg["content"]

    @pytest.mark.asyncio
    async def test_tool_timeout(self, tool_registry, monkeypatch):
        tool_registry(_SlowTool())
        monkeypatch.setenv("CODING_AGENT_TOOL_TIMEOUT", "0.05")
        ag = _make_agent()
        msg = await ag._execute_tool("cov_slow_tool", {"_id": "c1"})
        assert msg["content"].startswith("Error: Tool execution timed out after 0s.")

    @pytest.mark.asyncio
    async def test_per_tool_timeout_override_used(self, tool_registry):
        tool_registry(_OkToolWithTimeout())
        ag = _make_agent()
        msg = await ag._execute_tool("cov_ok_timeout_tool", {"_id": "c1"})
        assert msg["content"] == "timeout-override-ok"

    @pytest.mark.asyncio
    async def test_tool_generic_exception(self, tool_registry):
        tool_registry(_BoomTool())
        ag = _make_agent()
        msg = await ag._execute_tool("cov_boom_tool", {"_id": "c1"})
        assert msg["content"] == "Error: Tool execution error: kaboom"

    @pytest.mark.asyncio
    async def test_error_result_gets_error_prefix(self, tool_registry):
        tool_registry(_ErrTool())
        ag = _make_agent()
        msg = await ag._execute_tool("cov_err_tool", {"_id": "c1"})
        assert msg["content"] == "Error: plain failure"

    @pytest.mark.asyncio
    async def test_error_result_already_prefixed_untouched(self, tool_registry):
        tool_registry(_PrefixedErrTool())
        ag = _make_agent()
        msg = await ag._execute_tool("cov_prefixed_err_tool", {"_id": "c1"})
        assert msg["content"] == "Error: already prefixed"

    @pytest.mark.asyncio
    async def test_result_handler_truncation_event(self, tool_registry):
        from core.tool_result_handler import reset_tool_result_handler

        reset_tool_result_handler()  # fresh handler: deterministic rate limits
        tool_registry(_BigTool())
        ag = _make_agent()
        assert ag._result_handler is not None
        # Keep the token-budget backpressure stage from swallowing the
        # result before the truncation stage can fire its event.
        bp = ag._result_handler.backpressure
        old_budget = bp.token_budget
        bp.token_budget = 10_000_000
        bp._last_result_time = 0.0
        events = []
        ag.callbacks.on_event = events.append
        try:
            msg = await ag._execute_tool("cov_big_tool", {"_id": "ctrunc1"})
        finally:
            bp.token_budget = old_budget
        assert len(msg["content"]) < 5000
        truncated = [e for e in events if e.type is AgentEventType.TOOL_RESULT_TRUNCATED]
        assert len(truncated) == 1
        payload = truncated[0].payload
        assert payload["tool"] == "cov_big_tool"
        assert payload["call_id"] == "ctrunc1"
        assert payload["original_size"] == 5000
        assert payload["truncated_size"] == len(msg["content"])

    @pytest.mark.asyncio
    async def test_legacy_truncation_without_result_handler(self, tool_registry, monkeypatch):
        def _boom(**kwargs):
            raise RuntimeError("no handler")

        monkeypatch.setattr("core.tool_result_handler.get_tool_result_handler", _boom)
        tool_registry(_BigTool())
        ag = _make_agent()
        assert ag._result_handler is None
        msg = await ag._execute_tool("cov_big_tool", {"_id": "c1"})
        assert msg["content"] == "x" * ag.max_output_chars + "\n... [output truncated]"

    @pytest.mark.asyncio
    async def test_legacy_path_short_content_untouched(self, tool_registry, monkeypatch):
        def _boom(**kwargs):
            raise RuntimeError("no handler")

        monkeypatch.setattr("core.tool_result_handler.get_tool_result_handler", _boom)
        tool_registry(_OkTool())
        ag = _make_agent()
        assert ag._result_handler is None
        msg = await ag._execute_tool("cov_ok_tool", {"_id": "c1"})
        assert msg["content"] == "ok-result"

    @pytest.mark.asyncio
    async def test_after_tool_hook_can_modify_result(self, tool_registry):
        tool_registry(_OkTool())
        ag = _make_agent()

        def _modifier(name, args, result, ctx):
            result["content"] = result["content"] + " [via-hook]"
            return result

        ag.hooks.register_after_tool(_modifier)
        msg = await ag._execute_tool("cov_ok_tool", {"_id": "c1"})
        assert msg["content"] == "ok-result [via-hook]"

    @pytest.mark.asyncio
    async def test_after_tool_hook_exception_keeps_result(self, tool_registry, capsys):
        tool_registry(_OkTool())
        ag = _make_agent()

        def _raiser(name, args, result, ctx):
            raise RuntimeError("after hook exploded")

        ag.hooks.register_after_tool(_raiser)
        msg = await ag._execute_tool("cov_ok_tool", {"_id": "c1"})
        assert msg["content"] == "ok-result"
        assert "[HOOK ERR]" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_after_tool_hook_returning_none_keeps_result(self, tool_registry):
        tool_registry(_OkTool())
        ag = _make_agent()

        def _pass_through(name, args, result, ctx):
            return None

        ag.hooks.register_after_tool(_pass_through)
        msg = await ag._execute_tool("cov_ok_tool", {"_id": "c1"})
        assert msg["content"] == "ok-result"


# ── _should_continue / _looks_incomplete ────────────────────────────────


class TestShouldContinue:
    def test_length_finish_reason(self):
        ag = _make_agent()
        result = ag._should_continue({"content": "partial output", "_finish_reason": "length"})
        assert result is not None and "token limit" in result

    def test_finish_reason_key_variant(self):
        ag = _make_agent()
        assert ag._should_continue({"content": "partial", "finish_reason": "length"}) is not None

    def test_empty_content_returns_none(self):
        ag = _make_agent()
        assert ag._should_continue({"content": "   "}) is None
        assert ag._should_continue({}) is None

    def test_continuation_phrase(self):
        ag = _make_agent()
        msg = {"content": "I have done the first part. Continuing from where I left off next."}
        assert ag._should_continue(msg) == "Please continue from where you left off."

    def test_incomplete_code_block(self):
        ag = _make_agent()
        msg = {"content": "Here is the code:\n```python\nx = 1"}
        result = ag._should_continue(msg)
        assert result is not None and "incomplete" in result

    def test_incomplete_trailing_colon(self):
        ag = _make_agent()
        msg = {"content": "Here are the results:"}
        result = ag._should_continue(msg)
        assert result is not None and "incomplete" in result

    def test_complete_answer_returns_none(self):
        ag = _make_agent()
        msg = {"content": "Here is your complete answer. Everything is done."}
        assert ag._should_continue(msg) is None


class TestLooksIncomplete:
    def test_unclosed_fence(self):
        assert CodingAgent._looks_incomplete("```python\nx = 1", "```python\nx = 1") is True

    def test_closed_fence(self):
        text = "```python\nx = 1\n```\ndone"
        assert CodingAgent._looks_incomplete(text, text.lower()) is False

    def test_imminent_action(self):
        text = "The plan is solid. Now I'll"
        assert CodingAgent._looks_incomplete(text, text.lower()) is True

    def test_trailing_colon(self):
        text = "Here are the results:"
        assert CodingAgent._looks_incomplete(text, text.lower()) is True

    def test_trailing_connector(self):
        text = "First we fetch the data,"
        assert CodingAgent._looks_incomplete(text, text.lower()) is True

    def test_plain_complete_text(self):
        text = "All done. The task is complete."
        assert CodingAgent._looks_incomplete(text, text.lower()) is False


# ── _extract_session_memories ────────────────────────────────────────────


class TestExtractSessionMemories:
    def _agent_with_messages(self, monkeypatch):
        ag = _make_agent()
        # _make_agent neutralizes extraction for run() tests; these tests
        # exercise the real method, so drop the instance-level override.
        del ag._extract_session_memories
        memory = FakeMemory()
        monkeypatch.setattr("core.agent_loop.get_memory", lambda: memory)
        ag.messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "I like playing chess"},
            {"role": "assistant", "content": "y" * 600},
            {"role": "tool", "content": "t" * 400},
            {"role": "user", "content": ""},
            {"role": "user", "content": "remember that please"},
        ]
        ag._last_extraction_msg_count = 0
        return ag, memory

    @pytest.mark.asyncio
    async def test_extracts_and_stores_memories(self, monkeypatch):
        ag, memory = self._agent_with_messages(monkeypatch)
        payload = {
            "memories": [
                {
                    "content": "Dylan likes playing chess",
                    "category": "preference",
                    "tags": ["chess"],
                    "confidence": 0.9,
                }
            ]
        }
        seen = {}

        async def _fake_extract(messages):
            seen["messages"] = messages
            return {"content": json.dumps(payload)}

        ag._call_llm_for_extraction = _fake_extract
        await ag._extract_session_memories("remember that please")

        assert len(memory.added) == 1
        added = memory.added[0]
        assert added["content"] == "[preference] Dylan likes playing chess"
        assert added["tags"] == ["chess", "extracted", "preference"]
        assert added["source"].startswith("auto-extract-")

        # Transcript building: system skipped, empty skipped, tool capped
        # at 300 chars, assistant capped at 500 chars.
        transcript = seen["messages"][1]["content"]
        lines = transcript.splitlines()
        assert not any(line.startswith("[system]") for line in lines)
        assistant_line = next(line for line in lines if line.startswith("[assistant]"))
        assert len(assistant_line.split("] ", 1)[1]) == 500
        tool_line = next(line for line in lines if line.startswith("[tool]"))
        assert len(tool_line.split("] ", 1)[1]) == 300

    @pytest.mark.asyncio
    async def test_strips_code_fences(self, monkeypatch):
        ag, memory = self._agent_with_messages(monkeypatch)
        inner = json.dumps(
            {"memories": [{"content": "fenced fact", "category": "fact", "tags": []}]}
        )

        async def _fake_extract(messages):
            return {"content": "```json\n" + inner + "\n```"}

        ag._call_llm_for_extraction = _fake_extract
        await ag._extract_session_memories("hi")
        assert [m["content"] for m in memory.added] == ["[fact] fenced fact"]

    @pytest.mark.asyncio
    async def test_strips_opening_fence_without_closing(self, monkeypatch):
        ag, memory = self._agent_with_messages(monkeypatch)
        inner = json.dumps({"memories": [{"content": "half fenced", "category": "fact"}]})

        async def _fake_extract(messages):
            return {"content": "```json\n" + inner}  # no trailing fence

        ag._call_llm_for_extraction = _fake_extract
        await ag._extract_session_memories("hi")
        assert [m["content"] for m in memory.added] == ["[fact] half fenced"]

    @pytest.mark.asyncio
    async def test_empty_memories_list_stores_nothing(self, monkeypatch):
        ag, memory = self._agent_with_messages(monkeypatch)

        async def _fake_extract(messages):
            return {"content": '{"memories": []}'}

        ag._call_llm_for_extraction = _fake_extract
        await ag._extract_session_memories("hi")
        assert memory.added == []

    @pytest.mark.asyncio
    async def test_accepts_bare_list(self, monkeypatch):
        ag, memory = self._agent_with_messages(monkeypatch)

        async def _fake_extract(messages):
            return {"content": '[{"content": "bare list fact", "category": "fact"}]'}

        ag._call_llm_for_extraction = _fake_extract
        await ag._extract_session_memories("hi")
        assert [m["content"] for m in memory.added] == ["[fact] bare list fact"]

    @pytest.mark.asyncio
    async def test_skips_bad_members(self, monkeypatch):
        ag, memory = self._agent_with_messages(monkeypatch)

        async def _fake_extract(messages):
            return {
                "content": json.dumps(
                    {
                        "memories": [
                            "not a dict",
                            {"content": "   ", "category": "fact"},
                            {"content": "real fact", "category": "fact"},
                        ]
                    }
                )
            }

        ag._call_llm_for_extraction = _fake_extract
        await ag._extract_session_memories("hi")
        assert [m["content"] for m in memory.added] == ["[fact] real fact"]

    @pytest.mark.asyncio
    async def test_non_list_payload_ignored(self, monkeypatch):
        ag, memory = self._agent_with_messages(monkeypatch)

        async def _fake_extract(messages):
            return {"content": "42"}

        ag._call_llm_for_extraction = _fake_extract
        await ag._extract_session_memories("hi")
        assert memory.added == []

    @pytest.mark.asyncio
    async def test_invalid_json_ignored(self, monkeypatch):
        ag, memory = self._agent_with_messages(monkeypatch)

        async def _fake_extract(messages):
            return {"content": "not json {{{"}

        ag._call_llm_for_extraction = _fake_extract
        await ag._extract_session_memories("hi")  # must not raise
        assert memory.added == []

    @pytest.mark.asyncio
    async def test_empty_extraction_result_skipped(self, monkeypatch):
        ag, memory = self._agent_with_messages(monkeypatch)
        calls = []

        async def _fake_extract(messages):
            calls.append(messages)
            return None

        ag._call_llm_for_extraction = _fake_extract
        await ag._extract_session_memories("hi")
        assert calls and memory.added == []

    @pytest.mark.asyncio
    async def test_short_transcript_skipped(self, monkeypatch):
        ag = _make_agent()
        del ag._extract_session_memories  # exercise the real method here
        memory = FakeMemory()
        monkeypatch.setattr("core.agent_loop.get_memory", lambda: memory)
        ag.messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "hi"},
        ]
        ag._last_extraction_msg_count = 0
        calls = []

        async def _fake_extract(messages):
            calls.append(messages)
            return {"content": "[]"}

        ag._call_llm_for_extraction = _fake_extract
        await ag._extract_session_memories("hi")
        assert calls == []  # fewer than 2 transcript lines: no LLM call
        assert memory.added == []

    @pytest.mark.asyncio
    async def test_memory_lookup_failure_returns_silently(self, monkeypatch):
        def _boom():
            raise RuntimeError("memory down")

        monkeypatch.setattr("core.agent_loop.get_memory", _boom)
        ag = _make_agent()
        del ag._extract_session_memories  # exercise the real method here
        await ag._extract_session_memories("hi")  # must not raise


# ── _call_llm_for_extraction ────────────────────────────────────────────


class TestCallLlmForExtraction:
    @pytest.mark.asyncio
    async def test_returns_message_dict(self, monkeypatch):
        import core.llm_client as llm_client_mod

        captured = {}

        class _Resp:
            def json(self):
                return {"choices": [{"message": {"content": '{"memories": []}'}}]}

        async def _fake_post(self, url, headers, payload):
            captured["url"] = url
            captured["headers"] = headers
            captured["payload"] = payload
            return _Resp()

        monkeypatch.setattr(llm_client_mod.LLMClient, "_http_post", _fake_post)
        ag = _make_agent()
        result = await ag._call_llm_for_extraction([{"role": "user", "content": "hi"}])
        assert result == {"content": '{"memories": []}'}
        assert captured["url"] == f"{ag.base_url}/chat/completions"
        assert captured["payload"]["response_format"] == {"type": "json_object"}
        assert captured["payload"]["temperature"] == 0.3
        assert captured["payload"]["model"] == ag.model
        assert captured["headers"]["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_http_failure_returns_none(self, monkeypatch):
        import core.llm_client as llm_client_mod

        async def _fake_post(self, url, headers, payload):
            raise RuntimeError("network down")

        monkeypatch.setattr(llm_client_mod.LLMClient, "_http_post", _fake_post)
        ag = _make_agent()
        assert await ag._call_llm_for_extraction([]) is None

    @pytest.mark.asyncio
    async def test_no_auth_header_without_api_key(self, monkeypatch):
        import core.llm_client as llm_client_mod

        captured = {}

        class _Resp:
            def json(self):
                return {"choices": [{"message": {"content": "{}"}}]}

        async def _fake_post(self, url, headers, payload):
            captured["headers"] = headers
            return _Resp()

        monkeypatch.setattr(llm_client_mod.LLMClient, "_http_post", _fake_post)
        ag = _make_agent()
        ag.api_key = ""
        result = await ag._call_llm_for_extraction([{"role": "user", "content": "hi"}])
        assert result == {"content": "{}"}
        assert "Authorization" not in captured["headers"]


# ── run() loop paths ────────────────────────────────────────────────────


class TestRunLoop:
    @pytest.mark.asyncio
    async def test_llm_exceptions_three_times_returns_unreachable(self):
        ag = _make_agent()
        ag.messages = _seeded_messages()
        events = []
        ag.callbacks.on_event = events.append
        ag.llm_client = FakeLLMClient(
            [RuntimeError("boom1"), RuntimeError("boom2"), RuntimeError("boom3")]
        )
        result = await ag.run("hello", max_turns=5)
        assert "Could not reach" in result
        assert "boom3" in result
        assert len(ag.llm_client.calls) == 3
        assert any(e.type is AgentEventType.ERROR for e in events)

    @pytest.mark.asyncio
    async def test_two_llm_errors_then_recovery(self):
        ag = _make_agent()
        ag.messages = _seeded_messages()
        ag.llm_client = FakeLLMClient(
            [RuntimeError("x"), RuntimeError("y"), {"content": "recovered"}]
        )
        result = await ag.run("hello", max_turns=5)
        assert result == "recovered"

    @pytest.mark.asyncio
    async def test_none_responses_three_times(self):
        ag = _make_agent()
        ag.messages = _seeded_messages()
        ag.llm_client = FakeLLMClient([None, None, None])
        result = await ag.run("hello", max_turns=5)
        assert "Could not reach" in result
        assert "API key" in result

    @pytest.mark.asyncio
    async def test_free_model_fallback(self):
        ag = _make_agent()
        ag.messages = _seeded_messages()
        ag.model = "my-model-free"
        ag.llm_client = FakeLLMClient(
            [
                {"content": "[API Error: 429 rate limited]"},
                {"content": "[API Error: still down]"},  # deepseek-chat fails too
                RuntimeError("gateway exploded"),  # minimax-m2.5 raises
                {"content": "recovered via fallback"},  # qwen3-8b works
            ]
        )
        result = await ag.run("hello", max_turns=5)
        assert result == "recovered via fallback"
        assert ag.model == "qwen/qwen3-8b"

    @pytest.mark.asyncio
    async def test_free_model_skips_current_model_in_fallbacks(self):
        ag = _make_agent()
        ag.messages = _seeded_messages()
        # First entry of the fallback list: the loop must skip it via `continue`.
        ag.model = "deepseek/deepseek-chat"
        ag._provider_config.provider = "cline-usage"  # free-tier fallback applies
        ag.llm_client = FakeLLMClient(
            [
                {"content": "[API Error: 429]"},
                {"content": "ok on minimax"},
            ]
        )
        result = await ag.run("hello", max_turns=5)
        assert result == "ok on minimax"
        assert ag.model == "minimax/minimax-m2.5"

    @pytest.mark.asyncio
    async def test_free_model_all_fallbacks_fail(self):
        ag = _make_agent()
        ag.messages = _seeded_messages()
        ag.model = "my-model-free"
        ag.llm_client = FakeLLMClient(
            [{"content": "[API Error: 429]"}] + [{"content": "[API Error: down]"}] * 8
        )
        result = await ag.run("hello", max_turns=3)
        assert result == "[API Error: 429]"
        # Exhausted rotation restores the original model instead of pinning a
        # known-bad fallback.
        assert ag.model == "my-model-free"

    @pytest.mark.asyncio
    async def test_llm_result_object_converted_via_to_dict(self, tool_registry):
        from llm import LLMResult

        tool_registry(_OkTool())
        ag = _make_agent()
        ag.messages = _seeded_messages()
        ag.llm_client = FakeLLMClient(
            [
                LLMResult(content="", tool_calls=[_tool_call("cov_ok_tool", "c1")]),
                {"content": "all done"},
            ]
        )
        result = await ag.run("go", max_turns=5)
        assert result == "all done"
        assistant_msgs = [m for m in ag.messages if m.get("role") == "assistant"]
        assert assistant_msgs and assistant_msgs[0]["tool_calls"]

    @pytest.mark.asyncio
    async def test_token_usage_tracked(self):
        ag = _make_agent()
        ag.messages = _seeded_messages()
        ag.llm_client = FakeLLMClient(
            [{"content": "done", "_usage": {"input_tokens": 10, "output_tokens": 5}}]
        )
        result = await ag.run("hi", max_turns=3)
        assert result == "done"
        assert ag._cost_tracker.total_input_tokens == 10
        assert ag._cost_tracker.total_output_tokens == 5

    @pytest.mark.asyncio
    async def test_context_truncation_kicks_in(self):
        ag = _make_agent()
        ag.messages = [{"role": "system", "content": "s"}] + [
            {"role": "user", "content": f"filler {i}"} for i in range(50)
        ]
        events = []
        ag.callbacks.on_event = events.append
        ag.llm_client = FakeLLMClient([{"content": "done"}])
        result = await ag.run("hi", max_turns=2)
        assert result == "done"
        assert len(ag.messages) <= 40
        assert any(e.type is AgentEventType.CONTEXT_TRUNCATED for e in events)

    @pytest.mark.asyncio
    async def test_memory_refresh_in_run(self):
        ag = _make_agent()
        ag.messages = _seeded_messages()
        ag._last_memory_refresh_turn = -2  # force refresh on the first turn
        refreshed_calls = []

        async def _fake_refresh(query):
            refreshed_calls.append(query)
            return True

        ag._refresh_memory_context = _fake_refresh
        events = []
        ag.callbacks.on_event = events.append
        ag.llm_client = FakeLLMClient([{"content": "done"}])
        result = await ag.run("hi", max_turns=2)
        assert result == "done"
        assert refreshed_calls
        assert any(e.type is AgentEventType.MEMORY_REFRESH for e in events)
        assert any(
            m.get("content", "").startswith("[Updated relevant memories]") for m in ag.messages
        )

    @pytest.mark.asyncio
    async def test_continuation_on_incomplete_response(self):
        ag = _make_agent()
        ag.messages = _seeded_messages()
        events = []
        ag.callbacks.on_event = events.append
        ag.llm_client = FakeLLMClient(
            [
                {"content": "```python\nx = 1"},  # unclosed fence -> incomplete
                {"content": "finished for real"},
            ]
        )
        result = await ag.run("do it", max_turns=5)
        assert result == "finished for real"
        assert ag._no_tool_call_turns == 0
        cont_events = [
            e for e in events if e.type is AgentEventType.TURN_END and e.payload.get("continuation")
        ]
        assert len(cont_events) == 1
        assert any("appears incomplete" in (m.get("content") or "") for m in ag.messages)

    @pytest.mark.asyncio
    async def test_no_continuation_after_max_retries(self):
        ag = _make_agent()
        ag.messages = _seeded_messages()
        ag._no_tool_call_turns = ag._max_no_tool_call_turns
        ag.llm_client = FakeLLMClient([{"content": "```python\nx = 1"}])
        result = await ag.run("do it", max_turns=3)
        assert result == "```python\nx = 1"
        assert not any("appears incomplete" in (m.get("content") or "") for m in ag.messages)

    @pytest.mark.asyncio
    async def test_max_turns_summary(self, tool_registry):
        tool_registry(_OkTool())
        ag = _make_agent()
        ag.messages = _seeded_messages()
        ag.llm_client = FakeLLMClient(
            [
                {"content": "", "tool_calls": [_tool_call("cov_ok_tool", "c1")]},
                {"content": "summary of work"},
            ]
        )
        result = await ag.run("work", max_turns=1)
        assert result == "summary of work"
        assert any("maximum number of turns" in (m.get("content") or "") for m in ag.messages)

    @pytest.mark.asyncio
    async def test_max_turns_empty_summary(self, tool_registry):
        tool_registry(_OkTool())
        ag = _make_agent()
        ag.messages = _seeded_messages()
        ag.llm_client = FakeLLMClient(
            [
                {"content": "", "tool_calls": [_tool_call("cov_ok_tool", "c1")]},
                None,
            ]
        )
        result = await ag.run("work", max_turns=1)
        assert result == ""

    @pytest.mark.asyncio
    async def test_tool_call_with_invalid_json_args(self, tool_registry):
        tool_registry(_OkTool())
        ag = _make_agent()
        ag.messages = _seeded_messages()
        ag.llm_client = FakeLLMClient(
            [
                {"content": "", "tool_calls": [_tool_call("cov_ok_tool", "c1", "{bad json")]},
                {"content": "done"},
            ]
        )
        result = await ag.run("go", max_turns=3)
        assert result == "done"

    @pytest.mark.asyncio
    async def test_before_model_hook_modifies_messages(self, clean_hooks):
        ag = _make_agent()
        ag.messages = _seeded_messages()

        def _inject(messages, ctx):
            return [*messages, {"role": "system", "content": "INJECTED-MARKER"}]

        ag.hooks.register_before_model(_inject)
        ag.llm_client = FakeLLMClient([{"content": "done"}])
        await ag.run("hi", max_turns=1)
        sent = ag.llm_client.calls[0]["kwargs"]["messages"]
        assert any(m.get("content") == "INJECTED-MARKER" for m in sent)

    @pytest.mark.asyncio
    async def test_before_model_hook_exception_ignored(self, clean_hooks, capsys):
        ag = _make_agent()
        ag.messages = _seeded_messages()

        def _raiser(messages, ctx):
            raise RuntimeError("before-model exploded")

        ag.hooks.register_before_model(_raiser)
        ag.llm_client = FakeLLMClient([{"content": "done"}])
        result = await ag.run("hi", max_turns=1)
        assert result == "done"
        assert "[HOOK ERR]" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_after_model_hook_rewrites_response(self, clean_hooks):
        ag = _make_agent()
        ag.messages = _seeded_messages()

        def _rewrite(msg, ctx):
            return {"content": "rewritten by hook"}

        ag.hooks.register_after_model(_rewrite)
        ag.llm_client = FakeLLMClient([{"content": "original"}])
        result = await ag.run("hi", max_turns=1)
        assert result == "rewritten by hook"

    @pytest.mark.asyncio
    async def test_after_model_hook_exception_ignored(self, clean_hooks):
        ag = _make_agent()
        ag.messages = _seeded_messages()

        def _raiser(msg, ctx):
            raise RuntimeError("after-model exploded")

        ag.hooks.register_after_model(_raiser)
        ag.llm_client = FakeLLMClient([{"content": "original"}])
        result = await ag.run("hi", max_turns=1)
        assert result == "original"

    @pytest.mark.asyncio
    async def test_hint_added_when_tool_errors_repeat(self, tool_registry):
        tool_registry(_OkTool())
        ag = _make_agent()
        ag.messages = _seeded_messages()
        ag.llm_client = FakeLLMClient(
            [
                # Turn 1: failing calls (no previous errors -> no hint yet).
                {
                    "content": "",
                    "tool_calls": [
                        _tool_call("nope_tool_xyz", "c1"),
                        _tool_call("nope_tool_xyz", "c2"),
                    ],
                },
                # Turn 2: failing calls again (previous errors exist -> hint).
                {
                    "content": "",
                    "tool_calls": [
                        _tool_call("nope_tool_xyz", "c3"),
                        _tool_call("nope_tool_xyz", "c4"),
                    ],
                },
                {"content": "giving up"},
            ]
        )
        result = await ag.run("do the thing", max_turns=10)
        assert result == "giving up"
        hints = [m for m in ag.messages if (m.get("content") or "").startswith("HINT:")]
        assert len(hints) == 1


# ── _refresh_memory_context ─────────────────────────────────────────────


class TestRefreshMemoryContext:
    @pytest.mark.asyncio
    async def test_returns_false_when_never_refreshed(self):
        ag = _make_agent()
        del ag._refresh_memory_context  # exercise the real method here
        ag._last_memory_refresh_turn = 0
        assert await ag._refresh_memory_context("query") is False

    @pytest.mark.asyncio
    async def test_returns_true_on_new_context(self, monkeypatch):
        ag = _make_agent()
        del ag._refresh_memory_context  # exercise the real method here
        ag._last_memory_refresh_turn = 5
        ag._memory_context = "old context"
        ag.messages = [{"role": "user", "content": "tell me about chess"}]
        monkeypatch.setattr(
            "core.agent_loop.get_memory", lambda: FakeMemory(context="fresh chess context")
        )
        assert await ag._refresh_memory_context("chess") is True
        assert ag._memory_context == "fresh chess context"

    @pytest.mark.asyncio
    async def test_returns_false_when_context_unchanged(self, monkeypatch):
        ag = _make_agent()
        del ag._refresh_memory_context  # exercise the real method here
        ag._last_memory_refresh_turn = 5
        ag._memory_context = "same context"
        ag.messages = [{"role": "user", "content": "hi"}]
        monkeypatch.setattr(
            "core.agent_loop.get_memory", lambda: FakeMemory(context="same context")
        )
        assert await ag._refresh_memory_context("hi") is False

    @pytest.mark.asyncio
    async def test_returns_false_on_no_memories_marker(self, monkeypatch):
        ag = _make_agent()
        del ag._refresh_memory_context  # exercise the real method here
        ag._last_memory_refresh_turn = 5
        ag._memory_context = "old"
        ag.messages = [{"role": "user", "content": "hi"}]
        monkeypatch.setattr(
            "core.agent_loop.get_memory", lambda: FakeMemory(context="(no relevant memories)")
        )
        assert await ag._refresh_memory_context("hi") is False

    @pytest.mark.asyncio
    async def test_returns_false_when_memory_raises(self, monkeypatch):
        ag = _make_agent()
        del ag._refresh_memory_context  # exercise the real method here
        ag._last_memory_refresh_turn = 5
        ag.messages = [{"role": "user", "content": "hi"}]

        def _boom():
            raise RuntimeError("memory down")

        monkeypatch.setattr("core.agent_loop.get_memory", _boom)
        assert await ag._refresh_memory_context("hi") is False


# ── session persistence ─────────────────────────────────────────────────


class TestSessionPersistence:
    def test_save_session_no_messages_returns_none(self):
        ag = _make_agent()
        assert ag.messages == []
        assert ag.save_session() is None

    def test_save_session_persists(self, monkeypatch):
        ag = _make_agent()
        ag.messages = [{"role": "user", "content": "hi"}]
        ag.turn_count = 2
        store = FakeSessionStore()
        monkeypatch.setattr("core.agent_loop.get_session_store", lambda: store)
        path = ag.save_session()
        assert path == str(store._path)
        assert len(store.saved) == 1
        saved = store.saved[0]
        assert saved["conversation_id"] == ag.conversation_id
        assert saved["messages"] == ag.messages
        assert saved["model"] == "test-model"
        assert saved["provider"] == ag.provider_name
        assert saved["meta"] == {"turn_count": 2}

    def test_save_session_store_failure_returns_none(self, monkeypatch):
        ag = _make_agent()
        ag.messages = [{"role": "user", "content": "hi"}]
        monkeypatch.setattr(
            "core.agent_loop.get_session_store", lambda: FakeSessionStore(fail=True)
        )
        assert ag.save_session() is None

    def test_restore_session(self):
        ag = _make_agent()
        session = {
            "messages": [{"role": "user", "content": "old"}],
            "conversation_id": "conv_old",
            "meta": {"turn_count": 7},
            "model": "old-model",
        }
        ag.restore_session(session)
        assert ag.messages == session["messages"]
        assert ag.conversation_id == "conv_old"
        assert ag.turn_count == 7
        assert ag.model == "old-model"

    def test_restore_session_without_model_keeps_current(self):
        ag = _make_agent()
        ag.restore_session({"messages": [{"role": "user", "content": "x"}]})
        assert ag.model == "test-model"
        assert ag.messages == [{"role": "user", "content": "x"}]

    def test_restore_empty_session_is_noop(self):
        ag = _make_agent()
        ag.restore_session({})
        assert ag.messages == []
        assert ag.model == "test-model"


# ── run(): fresh conversation, refresh edges, hook variants, breaker ────


class TestRunFreshConversation:
    @pytest.mark.asyncio
    async def test_builds_system_and_injects_memories(self, monkeypatch):
        ag = _make_agent()
        assert ag.messages == []
        monkeypatch.setattr(
            "core.agent_loop.get_memory", lambda: FakeMemory(context="Dylan likes chess")
        )
        ag.llm_client = FakeLLMClient([{"content": "done"}])
        result = await ag.run("hi", max_turns=1)
        assert result == "done"
        assert ag._memory_context == "Dylan likes chess"
        mem_msgs = [
            m
            for m in ag.messages
            if m.get("role") == "system" and "Relevant memories" in m.get("content", "")
        ]
        assert len(mem_msgs) == 1
        # The system prompt was built from the real registry + message builder.
        system_msgs = [
            m
            for m in ag.messages
            if m.get("role") == "system" and "Relevant" not in m.get("content", "")
        ]
        assert system_msgs and isinstance(system_msgs[0]["content"], str)

    @pytest.mark.asyncio
    async def test_empty_memory_context_skips_injection(self, monkeypatch):
        ag = _make_agent()
        monkeypatch.setattr("core.agent_loop.get_memory", lambda: FakeMemory(context=""))
        ag.llm_client = FakeLLMClient([{"content": "done"}])
        await ag.run("hi", max_turns=1)
        assert ag._memory_context == ""
        assert not any("Relevant memories" in m.get("content", "") for m in ag.messages)

    @pytest.mark.asyncio
    async def test_no_memories_marker_skips_injection(self, monkeypatch):
        ag = _make_agent()
        monkeypatch.setattr(
            "core.agent_loop.get_memory", lambda: FakeMemory(context="(no relevant memories)")
        )
        ag.llm_client = FakeLLMClient([{"content": "done"}])
        await ag.run("hi", max_turns=1)
        assert not any("Relevant memories" in m.get("content", "") for m in ag.messages)

    @pytest.mark.asyncio
    async def test_memory_lookup_failure_ignored(self, monkeypatch):
        def _boom():
            raise RuntimeError("memory down")

        monkeypatch.setattr("core.agent_loop.get_memory", _boom)
        ag = _make_agent()
        ag.llm_client = FakeLLMClient([{"content": "done"}])
        result = await ag.run("hi", max_turns=1)
        assert result == "done"


class TestRunMemoryRefreshEdges:
    @pytest.mark.asyncio
    async def test_empty_recent_skips_refresh_call(self):
        ag = _make_agent()
        ag.messages = [{"role": "system", "content": "s"}]
        ag._last_memory_refresh_turn = -2  # force the interval check to pass
        calls = []

        async def _fake_refresh(query):
            calls.append(query)
            return True

        ag._refresh_memory_context = _fake_refresh
        ag.llm_client = FakeLLMClient([{"content": "done"}])
        result = await ag.run("", max_turns=1)  # empty user message -> recent == ""
        assert result == "done"
        assert calls == []

    @pytest.mark.asyncio
    async def test_refresh_exception_ignored(self):
        ag = _make_agent()
        ag.messages = _seeded_messages()
        ag._last_memory_refresh_turn = -2

        async def _boom_refresh(query):
            raise RuntimeError("refresh exploded")

        ag._refresh_memory_context = _boom_refresh
        ag.llm_client = FakeLLMClient([{"content": "done"}])
        result = await ag.run("hi", max_turns=1)
        assert result == "done"


class TestRunHookVariants:
    @pytest.mark.asyncio
    async def test_before_model_hook_returning_none(self, clean_hooks):
        ag = _make_agent()
        ag.messages = _seeded_messages()

        def _pass_through(messages, ctx):
            return None

        ag.hooks.register_before_model(_pass_through)
        ag.llm_client = FakeLLMClient([{"content": "done"}])
        result = await ag.run("hi", max_turns=1)
        assert result == "done"
        sent = ag.llm_client.calls[0]["kwargs"]["messages"]
        assert sent is ag.messages or sent == ag.messages

    @pytest.mark.asyncio
    async def test_after_model_hook_returning_none(self, clean_hooks):
        ag = _make_agent()
        ag.messages = _seeded_messages()

        def _pass_through(msg, ctx):
            return None

        ag.hooks.register_after_model(_pass_through)
        ag.llm_client = FakeLLMClient([{"content": "original"}])
        result = await ag.run("hi", max_turns=1)
        assert result == "original"


class TestRunHintsAndBreaker:
    @pytest.mark.asyncio
    async def test_hint_search_misses_when_previous_turn_ok(self, tool_registry):
        tool_registry(_OkTool())
        ag = _make_agent()
        ag.messages = _seeded_messages()
        ag.llm_client = FakeLLMClient(
            [
                {"content": "", "tool_calls": [_tool_call("cov_ok_tool", "c1")]},
                {"content": "", "tool_calls": [_tool_call("nope_tool_xyz", "c2")]},
                {"content": "done"},
            ]
        )
        result = await ag.run("go", max_turns=5)
        assert result == "done"
        # Previous turn had no tool errors in the last-3 window: no HINT added.
        hints = [m for m in ag.messages if (m.get("content") or "").startswith("HINT:")]
        assert hints == []

    @pytest.mark.asyncio
    async def test_circuit_breaker_stops_stuck_loop(self):
        ag = _make_agent()
        ag.messages = _seeded_messages()
        events = []
        ag.callbacks.on_event = events.append
        ag.llm_client = FakeLLMClient(
            [{"content": "", "tool_calls": [_tool_call("nope_tool_xyz", "c1")]}] * 6
        )
        result = await ag.run("do it", max_turns=20)
        assert "consecutive turns" in result
        assert "every tool call failed" in result
        assert ag.turn_count == ag._max_consecutive_failed_tool_turns == 4
        assert any(e.type is AgentEventType.ERROR and e.payload.get("stuck_loop") for e in events)


class TestReset:
    def test_reset_clears_state(self):
        ag = _make_agent()
        ag.messages = [{"role": "user", "content": "x"}]
        ag.turn_count = 3
        ag._no_tool_call_turns = 2
        ag._consecutive_failed_tool_turns = 3
        ag.reset()
        assert ag.messages == []
        assert ag.turn_count == 0
        assert ag._no_tool_call_turns == 0
        assert ag._consecutive_failed_tool_turns == 0
        assert ag._memory_context == ""
