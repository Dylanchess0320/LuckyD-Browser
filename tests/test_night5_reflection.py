"""Night-5 tests: core/reflection.py (was ~0% covered).

Reflection loop with a stubbed llm_caller (no network): score parsing,
neutral-score fallback on bad JSON, improvement loop dynamics
(threshold / min-improvement / max-iterations), weighted averaging, and the
no-LLM passthrough path.
"""

from __future__ import annotations

import json

import pytest

from core.reflection import (
    QualityDimension,
    QualityScore,
    ReflectionConfig,
    ReflectionEngine,
    get_reflection_engine,
    reflect_on_output,
)


def _scores_json(items):
    return json.dumps({"scores": items})


def _make_caller(scores=None, improved=None):
    """Fake llm_caller: returns score JSON for reflection prompts, `improved`
    text for improvement prompts. Accepts sync or async."""
    calls = []

    def caller(prompt):
        calls.append(prompt)
        if "critical reviewer" in prompt:
            return _scores_json(
                scores
                if scores is not None
                else [
                    {
                        "dimension": "correctness",
                        "score": 0.4,
                        "feedback": "wrong",
                        "suggestion": "fix it",
                    },
                    {
                        "dimension": "completeness",
                        "score": 0.4,
                        "feedback": "partial",
                        "suggestion": "finish it",
                    },
                    {
                        "dimension": "clarity",
                        "score": 0.4,
                        "feedback": "murky",
                        "suggestion": "clarify",
                    },
                ]
            )
        return improved if improved is not None else "BETTER OUTPUT"

    caller.calls = calls
    return caller


# ── no-LLM passthrough ─────────────────────────────────────────────────


async def test_reflect_without_llm_returns_original():
    engine = ReflectionEngine()
    result = await engine.reflect("do x", "my output")
    assert result.original_output == "my output"
    assert result.improved_output == "my output"
    assert result.improved is False
    assert result.overall_score == 0.5
    assert result.iteration == 0


async def test_score_output_without_llm_empty():
    engine = ReflectionEngine()
    assert await engine._score_output("r", "o") == []


async def test_improve_output_without_llm_passthrough():
    engine = ReflectionEngine()
    assert await engine._improve_output("r", "same", "fb") == "same"


# ── score parsing ──────────────────────────────────────────────────────


async def test_score_output_parses_json():
    engine = ReflectionEngine(llm_caller=_make_caller())
    scores = await engine._score_output("req", "out")
    assert len(scores) == 3
    assert scores[0].dimension == QualityDimension.CORRECTNESS
    assert scores[0].score == 0.4
    assert scores[0].feedback == "wrong"
    assert scores[0].suggestion == "fix it"


async def test_score_output_skips_unknown_dimensions():
    caller = _make_caller(
        scores=[
            {"dimension": "correctness", "score": 0.9, "feedback": "", "suggestion": ""},
            {"dimension": "bogus_dim", "score": 1.0, "feedback": "", "suggestion": ""},
        ]
    )
    engine = ReflectionEngine(llm_caller=caller)
    scores = await engine._score_output("r", "o")
    assert [s.dimension for s in scores] == [QualityDimension.CORRECTNESS]


async def test_score_output_bad_json_falls_back_neutral():
    engine = ReflectionEngine(llm_caller=lambda p: "not json at all {{{")
    scores = await engine._score_output("r", "o")
    assert len(scores) == 3  # one per configured dimension
    assert all(s.score == 0.5 for s in scores)
    assert all(s.feedback == "Unable to parse reflection" for s in scores)


async def test_score_output_caller_raises_falls_back():
    def boom(prompt):
        raise RuntimeError("llm down")

    engine = ReflectionEngine(llm_caller=boom)
    scores = await engine._score_output("r", "o")
    assert all(s.score == 0.5 for s in scores)
    assert all(s.feedback == "Reflection unavailable" for s in scores)
    # and the full loop survives a dead caller too
    result = await engine.reflect("r", "orig")
    assert result.improved is False
    assert result.improved_output == "orig"


async def test_improve_output_strips_and_handles_error():
    engine = ReflectionEngine(llm_caller=lambda p: "  improved text  \n")
    assert await engine._improve_output("r", "o", "fb") == "improved text"

    def boom(prompt):
        raise RuntimeError("down")

    engine2 = ReflectionEngine(llm_caller=boom)
    assert await engine2._improve_output("r", "orig", "fb") == "orig"


async def test_call_llm_supports_sync_and_async():
    engine_sync = ReflectionEngine(llm_caller=lambda p: "sync")
    assert await engine_sync._call_llm("x") == "sync"

    async def acaller(p):
        return "async"

    engine_async = ReflectionEngine(llm_caller=acaller)
    assert await engine_async._call_llm("x") == "async"


# ── loop dynamics ──────────────────────────────────────────────────────


async def test_reflect_stops_when_quality_above_threshold():
    caller = _make_caller(
        scores=[
            {"dimension": "correctness", "score": 0.95, "feedback": "", "suggestion": ""},
            {"dimension": "completeness", "score": 0.95, "feedback": "", "suggestion": ""},
            {"dimension": "clarity", "score": 0.95, "feedback": "", "suggestion": ""},
        ]
    )
    engine = ReflectionEngine(llm_caller=caller)
    result = await engine.reflect("req", "great output")
    assert result.improved is False
    assert result.improved_output == "great output"
    assert result.overall_score >= 0.85
    assert result.iteration == 1
    # only the scoring prompt was sent, no improvement prompt
    assert len(caller.calls) == 1


async def test_reflect_applies_improvement_when_gain_big_enough():
    # First scoring: low (0.4). Improvement text scores high (0.95).
    score_calls = {"n": 0}

    def caller(prompt):
        if "critical reviewer" in prompt:
            score_calls["n"] += 1
            low = score_calls["n"] == 1
            v = 0.4 if low else 0.95
            return _scores_json(
                [
                    {"dimension": "correctness", "score": v, "feedback": "f", "suggestion": "s"},
                    {"dimension": "completeness", "score": v, "feedback": "f", "suggestion": "s"},
                    {"dimension": "clarity", "score": v, "feedback": "f", "suggestion": "s"},
                ]
            )
        return "IMPROVED TEXT"

    engine = ReflectionEngine(llm_caller=caller)
    result = await engine.reflect("req", "original")
    assert result.improved is True
    assert result.improved_output == "IMPROVED TEXT"
    assert result.overall_score == pytest.approx(0.95)


async def test_reflect_stops_when_improvement_too_small():
    def caller(prompt):
        if "critical reviewer" in prompt:
            return _scores_json(
                [
                    {"dimension": "correctness", "score": 0.5, "feedback": "f", "suggestion": "s"},
                    {"dimension": "completeness", "score": 0.5, "feedback": "f", "suggestion": "s"},
                    {"dimension": "clarity", "score": 0.5, "feedback": "f", "suggestion": "s"},
                ]
            )
        return "slightly different"

    engine = ReflectionEngine(llm_caller=caller)
    result = await engine.reflect("req", "original")
    # new score == old score -> gain 0 < min_improvement 0.05 -> stop, not improved
    assert result.improved is False
    assert result.improved_output == "original"


async def test_reflect_stops_when_no_change():
    engine = ReflectionEngine(llm_caller=_make_caller(improved="original"))
    result = await engine.reflect("req", "original")
    assert result.improved is False
    assert result.iteration == 1


async def test_reflect_max_iterations_caps_loop():
    # Scores climb 0.50 -> 0.60 -> 0.70 (never reaching the 0.85 threshold,
    # gains stay >= min_improvement) -> loop must stop at max_iterations.
    state = {"scores": 0, "improves": 0}

    def caller(prompt):
        if "critical reviewer" in prompt:
            state["scores"] += 1
            v = 0.50 + 0.10 * (state["scores"] - 1)
            return _scores_json(
                [
                    {"dimension": "correctness", "score": v, "feedback": "f", "suggestion": "s"},
                    {"dimension": "completeness", "score": v, "feedback": "f", "suggestion": "s"},
                    {"dimension": "clarity", "score": v, "feedback": "f", "suggestion": "s"},
                ]
            )
        state["improves"] += 1
        return f"version-{state['improves']}"

    engine = ReflectionEngine(llm_caller=caller, config=ReflectionConfig(max_iterations=2))
    result = await engine.reflect("req", "v0")
    assert result.iteration == 2
    assert result.improved is True
    assert result.improved_output == "version-2"
    # each iteration does score + improve + re-score; after the loop a final
    # scoring pass runs -> 2*2 + 1 = 5 scoring calls, 2 improvements
    assert state["scores"] == 5
    assert state["improves"] == 2


# ── math & formatting ──────────────────────────────────────────────────


def test_weighted_average_math():
    engine = ReflectionEngine()
    scores = [
        QualityScore(QualityDimension.CORRECTNESS, 1.0, "", ""),
        QualityScore(QualityDimension.COMPLETENESS, 0.0, "", ""),
    ]
    # (1.0*0.35 + 0.0*0.25) / (0.35+0.25)
    assert engine._weighted_average(scores) == pytest.approx(0.35 / 0.60)
    assert engine._weighted_average([]) == 0.5


def test_format_feedback_includes_dimensions():
    engine = ReflectionEngine()
    scores = [QualityScore(QualityDimension.SAFETY, 0.2, "runs rm -rf", "ask first")]
    fb = engine._format_feedback(scores)
    assert "safety" in fb
    assert "runs rm -rf" in fb
    assert "ask first" in fb


def test_dimension_description_known():
    engine = ReflectionEngine()
    assert "Factually accurate" in engine._dimension_description(QualityDimension.CORRECTNESS)


# ── convenience + singleton ────────────────────────────────────────────


async def test_reflect_on_output_convenience():
    result = await reflect_on_output("req", "out", llm_caller=None, max_iterations=3)
    assert result.improved is False
    assert result.improved_output == "out"


def test_get_reflection_engine_singleton():
    e1 = get_reflection_engine()
    e2 = get_reflection_engine()
    assert e1 is e2
    assert isinstance(e1, ReflectionEngine)
