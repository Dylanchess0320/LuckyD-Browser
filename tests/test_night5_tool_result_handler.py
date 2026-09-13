"""Night-5 tests: core/tool_result_handler.py (was ~0% covered).

Truncator strategies (head/tail/head-tail/semantic/diff + auto-selection),
the continuation manager lifecycle, backpressure decisions, and the unified
process() pipeline with the global singleton.
"""

from __future__ import annotations

import time

import pytest

from core.tool_result_handler import (
    BackpressureController,
    BackpressureStrategy,
    ContinuationManager,
    ContinuationState,
    ContinuationStrategy,
    ToolResultHandler,
    ToolResultTruncator,
    TruncationResult,
    TruncationStrategy,
    get_tool_result_handler,
    reset_tool_result_handler,
)
from core.types import ToolResult


@pytest.fixture()
def truncator():
    return ToolResultTruncator(max_output_chars=100)


# ── truncator ────────────────────────────────────────────────────────────


def test_truncate_passthrough_under_limit(truncator):
    r = truncator.truncate("short")
    assert r.was_truncated is False
    assert r.content == "short"
    assert r.strategy == TruncationStrategy.NONE
    assert r.original_size == r.truncated_size == 5


def test_truncate_head(truncator):
    big = "x" * 300
    r = truncator.truncate(big, strategy=TruncationStrategy.HEAD)
    assert r.was_truncated is True
    assert r.content.startswith("x" * 100)
    assert "truncated 200 chars" in r.content
    assert r.continuation_hint.startswith("Use offset=100")


def test_truncate_tail(truncator):
    big = "ab" * 200
    r = truncator.truncate(big, strategy=TruncationStrategy.TAIL)
    assert r.was_truncated is True
    assert r.content.endswith("ab" * 50)
    assert r.content.startswith("[truncated")


def test_truncate_head_tail_elides_middle():
    t = ToolResultTruncator(max_output_chars=300)
    big = "A" * 200 + "B" * 200
    r = t.truncate(big, strategy=TruncationStrategy.HEAD_TAIL)
    assert r.was_truncated is True
    assert r.content.startswith("A" * 150)
    assert r.content.rstrip().endswith("B" * 50)
    assert "elided" in r.content
    assert r.elided_ranges == [(150, 400 - 50)]
    assert r.continuation_hint == "Use offset=150 to read elided section"


def test_truncate_semantic_keeps_definitions(truncator):
    # one long function whose body exceeds the budget -> truncated mid-def
    code = "def big():\n" + "    x = 1  # body line\n" * 100
    r = truncator.truncate(code, strategy=TruncationStrategy.SEMANTIC)
    assert r.was_truncated is True
    assert "def big():" in r.content
    assert "semantic truncation" in r.content
    assert r.strategy == TruncationStrategy.SEMANTIC


def test_truncate_semantic_small_code_keeps_all(truncator):
    code = "def f():\n    return 1\n"
    r = truncator.truncate(code * 2, strategy=TruncationStrategy.SEMANTIC)
    # short enough that semantic chunking keeps everything whole
    assert "def f():" in r.content


def test_truncate_diff_keeps_hunks(truncator):
    diff = (
        "@@ -1,3 +1,3 @@\n"
        "-old line\n"
        "+new line\n"
        " context\n" + (" filler line with content\n" * 60) + "@@ -50,2 +50,2 @@\n"
        "-gone\n"
        "+added\n"
    )
    r = truncator.truncate(diff, strategy=TruncationStrategy.DIFF)
    assert r.was_truncated is True
    assert "@@" in r.content
    assert "+new line" in r.content
    assert r.strategy == TruncationStrategy.DIFF


def test_auto_strategy_selection(truncator):
    assert truncator._select_strategy("Grep", "x" * 500, 100) == TruncationStrategy.HEAD
    assert truncator._select_strategy("Bash", "x" * 500, 100) == TruncationStrategy.HEAD_TAIL
    code = "def f():\n    return 1\n" * 30
    assert truncator._select_strategy("Read", code, 100) == TruncationStrategy.SEMANTIC
    assert (
        truncator._select_strategy("Read", "plain prose " * 50, 100) == TruncationStrategy.HEAD_TAIL
    )
    assert truncator._select_strategy("GitDiff", "@@ x\n" * 100, 100) == TruncationStrategy.DIFF
    assert (
        truncator._select_strategy("SomethingElse", "x" * 500, 100) == TruncationStrategy.HEAD_TAIL
    )


def test_looks_like_code(truncator):
    assert truncator._looks_like_code("def f():\n    return 1\n" * 10) is True
    assert truncator._looks_like_code("just some prose about things\n" * 10) is False


def test_truncation_result_dataclass_defaults():
    r = TruncationResult(
        content="c",
        was_truncated=False,
        original_size=1,
        truncated_size=1,
        strategy=TruncationStrategy.NONE,
    )
    assert r.elided_ranges == [] and r.continuation_hint == ""


# ── continuation manager ─────────────────────────────────────────────────


@pytest.fixture()
def cm():
    return ContinuationManager(max_continuations=10, chunk_size=10)


def test_register_and_total_parts(cm):
    state = cm.register_continuation("c1", "Read", ContinuationStrategy.CHUNKED, total_size=25)
    assert state.total_parts == 3  # ceil(25/10)
    assert state.limit == 10
    assert cm.get_continuation("c1") is state
    assert cm.get_continuation("missing") is None


def test_advance_walks_parts_then_cleans_up(cm):
    cm.register_continuation("c1", "Read", ContinuationStrategy.CHUNKED, total_size=25)
    s1 = cm.advance("c1")
    assert s1 is not None and s1.current_part == 1 and s1.offset == 10
    s2 = cm.advance("c1")
    assert s2 is not None and s2.current_part == 2
    assert cm.advance("c1") is None  # past the end -> cleaned up
    assert cm.get_continuation("c1") is None
    assert cm.advance("missing") is None


def test_get_next_chunk_uses_callback(cm):
    def cb(tool_name, offset, limit):
        assert tool_name == "Read"
        return ToolResult(text=f"chunk@{offset}:{limit}")

    cm.register_continuation("c1", "Read", ContinuationStrategy.CHUNKED, total_size=25, callback=cb)
    content, has_more = cm.get_next_chunk("c1")
    assert content == "chunk@0:10"
    assert has_more is True
    assert cm.get_next_chunk("nope") is None


def test_cleanup_expired(cm):
    state = cm.register_continuation("c1", "Read", ContinuationStrategy.CHUNKED, total_size=25)
    state.last_accessed = time.time() - 1000
    cm.cleanup_expired(max_age_sec=60)
    assert cm.get_continuation("c1") is None


def test_continuation_state_defaults():
    s = ContinuationState(tool_name="t", call_id="c", strategy=ContinuationStrategy.NONE)
    assert s.current_part == 0 and s.metadata == {}


# ── backpressure ─────────────────────────────────────────────────────────


@pytest.fixture()
def bp():
    return BackpressureController(
        max_session_chars=1000,
        max_result_chars=400,
        rate_limit_per_sec=1000.0,
        token_budget=8192,
    )


def test_backpressure_allows_small(bp):
    allowed, reason = bp.check(100)
    assert allowed is True and reason == ""


def test_backpressure_session_cap(bp):
    bp.record(900)
    allowed, reason = bp.check(200, strategy=BackpressureStrategy.SIZE_CAP)
    assert allowed is False
    assert "cap" in reason


def test_backpressure_token_budget(bp):
    # >10% of token budget on one result -> rejected
    allowed, reason = bp.check(4000, strategy=BackpressureStrategy.TOKEN_BUDGET)
    assert allowed is False
    assert "token budget" in reason


def test_backpressure_rate_limit():
    bp = BackpressureController(rate_limit_per_sec=1.0)
    bp.record(10)
    allowed, _ = bp.check(10, strategy=BackpressureStrategy.RATE_LIMIT)
    assert allowed is False


def test_backpressure_adaptive_large_recents(bp):
    bp.record(350)  # > 0.8 * 400
    bp._last_result_time = 0.0  # bypass the rate limiter for this check
    allowed, reason = bp.check(250, strategy=BackpressureStrategy.ADAPTIVE)  # > 0.5 * 400
    assert allowed is False
    assert "Adaptive" in reason


def test_backpressure_record_and_reset(bp):
    bp.record(100)
    bp.record(200)
    assert bp.get_pressure() > 0.0
    bp.reset()
    assert bp.get_pressure() == 0.0


# ── unified pipeline ─────────────────────────────────────────────────────


def test_process_small_result_passthrough():
    h = ToolResultHandler(max_output_chars=100, enable_backpressure=False)
    out = h.process(ToolResult(text="hello"), tool_name="Echo", call_id="x")
    assert out.text == "hello"


def test_process_truncates_and_registers_continuation():
    h = ToolResultHandler(max_output_chars=100, enable_backpressure=False)
    out = h.process(ToolResult(text="A" * 500), tool_name="Bash", call_id="call-1")
    assert "elided" in out.text  # HEAD_TAIL auto for Bash
    prompt = h.get_continuation_prompt("call-1")
    assert prompt is not None and "call-1" not in prompt and "Bash" in prompt
    assert h.get_continuation_prompt("missing") is None


def test_process_truncation_disabled():
    h = ToolResultHandler(max_output_chars=10, enable_truncation=False, enable_backpressure=False)
    out = h.process(ToolResult(text="A" * 500), tool_name="Bash", call_id="x")
    assert out.text == "A" * 500


def test_advance_continuation_returns_tool_result():
    h = ToolResultHandler(max_output_chars=50, enable_backpressure=False)
    h.process(ToolResult(text="B" * 300), tool_name="Read", call_id="c9")
    assert h.continuation.get_continuation("c9") is not None
    # no callback registered by process() -> advance returns None
    assert h.advance_continuation("c9") is None
    assert h.advance_continuation("missing") is None


def test_get_stats_shape():
    h = ToolResultHandler()
    stats = h.get_stats()
    assert stats["continuation"]["active"] == 0
    assert stats["truncation"]["max_output_chars"] == 4000
    assert stats["backpressure"]["session_chars"] == 0


def test_cleanup_delegates():
    h = ToolResultHandler()
    h.cleanup()  # must not raise


# ── singleton ────────────────────────────────────────────────────────────


def test_global_singleton_and_reset():
    reset_tool_result_handler()
    a = get_tool_result_handler()
    b = get_tool_result_handler(max_output_chars=1)  # kwargs ignored on 2nd call
    assert a is b
    assert a.truncator.max_output_chars == 4000
    reset_tool_result_handler()
    c = get_tool_result_handler(max_output_chars=123)
    assert c is not a
    assert c.truncator.max_output_chars == 123
    reset_tool_result_handler()
