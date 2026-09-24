"""A3: token-aware compaction that actually fires.

- Thresholds were fixed (30 turns / 60 msgs) for every model and the
  60-message path was unreachable anyway: the run loop's dumb 40-message
  truncate always fired first, destroying middle context without a
  summary. Compaction now scales with the model's context window and
  runs before truncation; truncation stays as the fallback cap.
- ``compact()`` sliced blindly, so the kept window could start with a
  bare ``role=tool`` message (providers 400 on orphaned tool
  messages). The split now keeps tool_calls/tool pairs together.
- The summarizer transcript dropped tool-call structure; tool names are
  now included so summaries keep what the agent did.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class FakeLLMClient:
    def __init__(self, script, summary="SUMMARY!"):
        self._script = list(script)
        self.model = "fake-model"
        self.chat_nonstreaming = AsyncMock(return_value={"content": summary})

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

        ag = CodingAgent(api_key="test-key-not-a-secret", model="test-model", **kwargs)

    async def _noop_extract(user_message):
        return None

    async def _noop_refresh(query):
        return False

    ag._extract_session_memories = _noop_extract
    ag._refresh_memory_context = _noop_refresh
    return ag


class TestModelWindows:
    def test_known_windows_and_default(self):
        from core.context_manager import context_window_for

        assert context_window_for("gemini-2.5-flash") == 1_000_000
        assert context_window_for("gpt-4o") == 128_000
        assert context_window_for("claude-sonnet-4-20250514") == 200_000
        assert context_window_for("deepseek-chat") == 64_000
        assert context_window_for("some-future-model-99") == 32_000
        assert context_window_for("") == 32_000

    def test_threshold_tiers(self):
        from core.context_manager import compaction_thresholds

        assert compaction_thresholds("test-model") == (30, 60, 6)
        assert compaction_thresholds("gpt-4o") == (45, 100, 10)
        assert compaction_thresholds("gemini-2.5-flash") == (60, 150, 12)

    def test_should_compact_uses_model_window(self):
        from core.compaction import should_compact

        assert should_compact(SimpleNamespace(turn_count=30, messages=[], model="gemini")) is False
        assert should_compact(SimpleNamespace(turn_count=60, messages=[], model="gemini")) is True
        # Explicit thresholds still win over the model default.
        assert (
            should_compact(
                SimpleNamespace(turn_count=5, messages=[], model="gemini"),
                max_turns=5,
            )
            is True
        )


class TestCompactPairs:
    async def test_split_keeps_tool_pair_together(self):
        from core.compaction import compact

        assistant = {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "Write"}}],
        }
        agent = SimpleNamespace(
            turn_count=99,
            messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "old"},
                assistant,
                {"role": "tool", "content": "out1"},
                {"role": "tool", "content": "out2"},
            ],
        )
        # Naive keep=2 would start the window with a bare tool message.
        assert await compact(agent, keep_recent_turns=2, summarizer=lambda m: "S") is True
        assert agent.messages[2]["role"] == "assistant"
        assert agent.messages[2].get("tool_calls")

    async def test_transcript_includes_tool_names(self):
        from core.compaction import _summarize_via_llm

        seen = {}

        async def _fake_chat_nonstreaming(messages=None, **kwargs):
            seen["prompt"] = messages[1]["content"]
            return {"content": "ok"}

        agent = SimpleNamespace(
            llm_client=SimpleNamespace(chat_nonstreaming=_fake_chat_nonstreaming)
        )
        middle = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": "Write"}}],
            },
            {"role": "tool", "content": "wrote it"},
        ]
        assert await _summarize_via_llm(agent, middle) == "ok"
        assert "[tools called: Write]" in seen["prompt"]


def _seeded_history(n):
    msgs = [{"role": "system", "content": "sys"}]
    msgs += [{"role": "user", "content": f"old {i}"} for i in range(n)]
    return msgs


class TestCompactionBeforeTruncation:
    async def test_long_history_compacts_with_summary(self):
        ag = _make_agent()
        ag.messages = _seeded_history(65)
        ag.llm_client = FakeLLMClient([{"content": "done and complete."}])
        assert await ag.run("go", max_turns=2) == "done and complete."
        bodies = [str(m.get("content", "")) for m in ag.messages]
        assert any("CONVERSATION SUMMARY" in b for b in bodies)
        assert not any("[Context truncated" in b for b in bodies)

    async def test_truncate_fallback_when_compaction_fails(self):
        ag = _make_agent()
        ag.messages = _seeded_history(65)
        client = FakeLLMClient([{"content": "done and complete."}])
        client.chat_nonstreaming = AsyncMock(side_effect=RuntimeError("no net"))
        ag.llm_client = client
        assert await ag.run("go", max_turns=2) == "done and complete."
        bodies = [str(m.get("content", "")) for m in ag.messages]
        assert any("[Context truncated" in b for b in bodies)
