"""Night-3 tests: core/context_manager.py.

Covers token estimation, truncate_messages (incl. never splitting a
tool_calls/tool pair and the mid-history system-message handling),
summarize_messages (LLM path + fallbacks), and the microcompact strategy.
"""

from __future__ import annotations

import pytest

from core.context_manager import (
    ContextManager,
    estimate_messages_tokens,
    estimate_tokens,
    summarize_messages,
    truncate_messages,
)
from core.types import CompactionStrategy


def _msg(role: str, content: str = "", **kw) -> dict:
    m = {"role": role, "content": content}
    m.update(kw)
    return m


def _tool_pair(call_id: str, output: str) -> list[dict]:
    return [
        _msg(
            "assistant",
            "",
            tool_calls=[{"id": call_id, "function": {"name": "Read", "arguments": "{}"}}],
        ),
        _msg("tool", output, tool_call_id=call_id),
    ]


# ── estimation ─────────────────────────────────────────────────────────


def test_estimate_tokens_empty_and_code():
    assert estimate_tokens("") == 0
    assert estimate_tokens("hello world") >= 1
    assert estimate_tokens("x" * 40, is_code=True) > estimate_tokens("x" * 40)


def test_estimate_messages_tokens_counts_tool_calls():
    msgs = [
        _msg("user", "hi"),
        _msg(
            "assistant",
            "",
            tool_calls=[{"id": "1", "function": {"name": "Read", "arguments": '{"p":1}'}}],
        ),
    ]
    total = estimate_messages_tokens(msgs)
    plain = estimate_messages_tokens([_msg("user", "hi")])
    assert total > plain


# ── truncate_messages ────────────────────────────────────────────────


def test_truncate_short_history_truncates_early_in_place():
    # Under max_messages: old assistant/tool messages truncated in place,
    # last 6 kept intact. (User messages are deliberately never truncated.)
    msgs = [_msg("system", "sys")]
    msgs += [_msg("assistant", "a" * 1000) for _ in range(4)]
    msgs += [_msg("assistant", "b" * 1000) for _ in range(6)]
    out = truncate_messages(msgs, max_messages=40, keep_recent=20)
    assert out is msgs  # same list object, truncated in place
    for m in msgs[1:5]:
        assert m["content"].endswith("... [truncated]")
    for m in msgs[5:]:
        assert len(m["content"]) == 1000  # last 6 intact


def test_truncate_user_messages_never_truncated_in_place():
    msgs = [_msg("system", "sys")]
    msgs += [_msg("user", "u" * 5000) for _ in range(8)]
    out = truncate_messages(msgs, max_messages=40, keep_recent=20)
    assert out is msgs
    assert all(len(m["content"]) == 5000 for m in msgs[1:])


def test_truncate_tiny_history_kept_whole():
    # Fewer messages than keep_intact (6): nothing is touched.
    msgs = [_msg("system", "sys"), _msg("user", "u" * 10), _msg("assistant", "a" * 1000)]
    out = truncate_messages(msgs, max_messages=40, keep_recent=20)
    assert out is msgs
    assert len(msgs[2]["content"]) == 1000


def test_truncate_over_limit_inserts_marker():
    msgs = [_msg("system", "sys")]
    msgs += [_msg("user", f"old-{i}") for i in range(30)]
    msgs += [_msg("assistant", "recent")]
    out = truncate_messages(msgs, max_messages=20, keep_recent=5)
    # 1 system + marker + 5 recent
    assert out[0]["role"] == "system" and out[0]["content"] == "sys"
    assert out[1]["content"].startswith("[Context truncated")
    assert out[-1]["content"] == "recent"
    assert len(out) == 1 + 1 + 5


def test_truncate_under_limit_truncates_in_place_no_marker():
    msgs = [_msg("system", "sys")]
    msgs += [_msg("assistant", "a" * 1000) for _ in range(10)]
    msgs += [_msg("assistant", "recent")]
    out = truncate_messages(msgs, max_messages=40, keep_recent=5)
    assert not any("[Context truncated" in m.get("content", "") for m in out)
    assert out[-1]["content"] == "recent"  # recent intact
    assert out[1]["content"].endswith("... [truncated]")  # old truncated


def test_truncate_never_splits_tool_pair():
    # Arrange so the keep_recent boundary would land on a tool message.
    msgs = [_msg("system", "sys")]
    msgs += [_msg("user", f"filler-{i}") for i in range(10)]
    msgs += _tool_pair("c1", "output-1")
    msgs += [_msg("user", f"tail-{i}") for i in range(3)]
    # keep_recent=4 → boundary lands right at the assistant/tool pair.
    out = truncate_messages(msgs, max_messages=10, keep_recent=4)
    roles = [m["role"] for m in out]
    # The tool message is present only together with its assistant caller.
    if "tool" in roles:
        ti = roles.index("tool")
        assert roles[ti - 1] == "assistant"
        assert out[ti - 1].get("tool_calls")


def test_truncate_only_leading_system_kept():
    # Mid-history system messages (memory refreshes) must not scramble the cut.
    msgs = [_msg("system", "prompt")]
    msgs += [_msg("user", f"m-{i}") for i in range(8)]
    msgs.append(_msg("system", "memory refresh"))
    msgs += [_msg("user", f"n-{i}") for i in range(8)]
    out = truncate_messages(msgs, max_messages=10, keep_recent=4)
    assert out[0]["content"] == "prompt"
    contents = [m.get("content", "") for m in out]
    assert "memory refresh" not in contents  # mid-history system msg was dropped


def test_truncate_tool_output_capped_in_place():
    # Tool outputs older than the last 6 messages are capped in place.
    msgs = [_msg("system", "s")]
    msgs += _tool_pair("c9", "y" * 2000)
    msgs += [_msg("user", f"pad-{i}") for i in range(7)]  # push the pair out of keep_intact
    out = truncate_messages(msgs, max_messages=40, keep_recent=20, max_tool_output_chars=300)
    tool_msgs = [m for m in out if m.get("role") == "tool"]
    assert tool_msgs and len(tool_msgs[0]["content"]) <= 300 + len("\n... [truncated]")
    assert tool_msgs[0]["content"].endswith("... [truncated]")
    assert tool_msgs[0]["tool_call_id"] == "c9"  # pairing metadata preserved


# ── summarize_messages ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_summarize_with_llm():
    msgs = [_msg("system", "prompt")]
    msgs += [_msg("user", f"question {i}") for i in range(10)]
    msgs += [_msg("assistant", f"answer {i}") for i in range(10)]
    msgs += [_msg("user", "latest")]

    async def fake_summarizer(prompt: str) -> str:
        assert "question" in prompt or "Summarize" in prompt
        return "SUMMARY OF EARLY TURNS"

    out = await summarize_messages(
        msgs, max_messages=10, keep_recent=3, summarizer_fn=fake_summarizer
    )
    assert out[0]["content"] == "prompt"
    assert out[1]["role"] == "system"
    assert "SUMMARY OF EARLY TURNS" in out[1]["content"]
    assert out[-1]["content"] == "latest"
    assert len(out) <= 10


@pytest.mark.asyncio
async def test_summarize_falls_back_to_truncate_without_fn():
    msgs = [_msg("system", "prompt")] + [_msg("user", f"m-{i}") for i in range(20)]
    out = await summarize_messages(msgs, max_messages=10, keep_recent=3)
    assert out[0]["content"] == "prompt"
    assert len(out) <= 10


@pytest.mark.asyncio
async def test_summarize_falls_back_when_llm_raises():
    msgs = [_msg("system", "prompt")] + [_msg("user", f"m-{i}") for i in range(20)]

    async def boom(prompt: str) -> str:
        raise RuntimeError("llm down")

    out = await summarize_messages(msgs, max_messages=10, keep_recent=3, summarizer_fn=boom)
    assert out[0]["content"] == "prompt"
    assert len(out) <= 10


@pytest.mark.asyncio
async def test_summarize_skips_system_in_transcript():
    seen = {}

    async def spy(prompt: str) -> str:
        seen["prompt"] = prompt
        return "s"

    msgs = (
        [_msg("system", "prompt")]
        + [_msg("system", "SECRET-SYSTEM-NOTE")]
        + [_msg("user", f"m-{i}") for i in range(12)]
    )
    await summarize_messages(msgs, max_messages=8, keep_recent=2, summarizer_fn=spy)
    assert "SECRET-SYSTEM-NOTE" not in seen["prompt"]


# ── ContextManager strategies ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_manager_none_returns_as_is():
    mgr = ContextManager(strategy=CompactionStrategy.NONE)
    msgs = [_msg("user", "x") for _ in range(100)]
    assert await mgr.compact(msgs) is msgs


@pytest.mark.asyncio
async def test_manager_hybrid_uses_summary_when_short_enough():
    async def fake(prompt: str) -> str:
        return "short"

    mgr = ContextManager(
        strategy=CompactionStrategy.HYBRID,
        max_messages=10,
        keep_recent=2,
        summarizer_fn=fake,
    )
    msgs = [_msg("system", "p")] + [_msg("user", f"m-{i}") for i in range(20)]
    out = await mgr.compact(msgs)
    assert len(out) <= 10
    assert any("short" in m.get("content", "") for m in out)


@pytest.mark.asyncio
async def test_manager_hybrid_truncates_without_summarizer():
    mgr = ContextManager(strategy=CompactionStrategy.HYBRID, max_messages=10, keep_recent=2)
    msgs = [_msg("system", "p")] + [_msg("user", f"m-{i}") for i in range(20)]
    out = await mgr.compact(msgs)
    assert len(out) <= 10
    assert out[0]["content"] == "p"


@pytest.mark.asyncio
async def test_manager_microcompact_elides_old_tool_results():
    mgr = ContextManager(
        strategy=CompactionStrategy.MICROCOMPACT,
        max_messages=50,
        keep_recent=3,
        max_tool_result_chars=100,
    )
    msgs = [_msg("system", "p")]
    msgs += _tool_pair("old", "z" * 5000)
    msgs += [_msg("user", "q")]
    msgs += _tool_pair("new", "w" * 5000)
    msgs += [_msg("user", "final")]
    out = await mgr.compact(msgs)
    by_id = {m.get("tool_call_id"): m for m in out if m.get("role") == "tool"}
    assert "old" in by_id and "[microcompact]" in by_id["old"]["content"]
    assert "5000 chars" in by_id["old"]["content"]
    # Recent tool result kept intact.
    assert by_id["new"]["content"] == "w" * 5000
    # Pairing preserved: elided tool msg still follows its assistant caller.
    roles = [m["role"] for m in out]
    ti = roles.index("tool")
    assert roles[ti - 1] == "assistant"


@pytest.mark.asyncio
async def test_manager_microcompact_falls_back_to_truncate():
    mgr = ContextManager(
        strategy=CompactionStrategy.MICROCOMPACT,
        max_messages=6,
        keep_recent=2,
        max_tool_result_chars=100,
    )
    msgs = [_msg("system", "p")] + [_msg("user", f"m-{i}") for i in range(20)]
    out = await mgr.compact(msgs)
    assert len(out) <= 6 + 2  # system + marker + kept


def test_manager_static_estimators():
    assert ContextManager.estimate_tokens("abc") == estimate_tokens("abc")
    msgs = [_msg("user", "hi")]
    assert ContextManager.estimate_messages_tokens(msgs) == estimate_messages_tokens(msgs)


def test_manager_set_strategy():
    mgr = ContextManager()
    mgr.set_strategy(CompactionStrategy.SUMMARIZE)
    assert mgr.strategy == CompactionStrategy.SUMMARIZE
