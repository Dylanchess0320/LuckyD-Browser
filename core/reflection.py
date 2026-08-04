"""
Self-reflection loop — the agent critiques its own work before presenting it.

Inspired by Reflexion (Shinn et al.) and Constitutional AI. After generating
a response, the agent:
  1. Scores its own output on multiple dimensions
  2. Identifies specific weaknesses
  3. Generates an improved version
  4. Repeats until quality threshold met or max iterations reached

This is the single cheapest quality upgrade — no external tools needed.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class QualityDimension(Enum):
    """Dimensions on which to evaluate agent output."""

    CORRECTNESS = "correctness"  # Is the answer/code factually correct?
    COMPLETENESS = "completeness"  # Does it fully address the request?
    CLARITY = "clarity"  # Is it clear and well-structured?
    EFFICIENCY = "efficiency"  # Is it the simplest/best approach?
    SAFETY = "safety"  # Does it avoid dangerous operations?
    STYLE = "style"  # Does it match project conventions?


@dataclass
class QualityScore:
    """Score on a single quality dimension."""

    dimension: QualityDimension
    score: float  # 0.0 to 1.0
    feedback: str  # Specific issue found
    suggestion: str  # How to improve


@dataclass
class ReflectionResult:
    """Result of a single reflection pass."""

    original_output: str
    improved_output: str
    scores: list[QualityScore]
    overall_score: float  # Weighted average
    iteration: int
    improved: bool  # Did this pass actually improve?
    reflection_time_ms: float


@dataclass
class ReflectionConfig:
    """Configuration for the reflection loop."""

    max_iterations: int = 2
    quality_threshold: float = 0.85  # Stop if overall score >= this
    min_improvement: float = 0.05  # Stop if improvement < this
    dimensions: list[QualityDimension] = field(
        default_factory=lambda: [
            QualityDimension.CORRECTNESS,
            QualityDimension.COMPLETENESS,
            QualityDimension.CLARITY,
        ]
    )
    # Weights for each dimension (must sum to ~1.0)
    weights: dict[QualityDimension, float] = field(
        default_factory=lambda: {
            QualityDimension.CORRECTNESS: 0.35,
            QualityDimension.COMPLETENESS: 0.25,
            QualityDimension.CLARITY: 0.15,
            QualityDimension.EFFICIENCY: 0.10,
            QualityDimension.SAFETY: 0.10,
            QualityDimension.STYLE: 0.05,
        }
    )


# ── Reflection Prompts ────────────────────────────────────────────────

REFLECTION_PROMPT = """You are a critical reviewer evaluating an AI assistant's response.

ORIGINAL REQUEST:
{request}

ASSISTANT'S RESPONSE:
{response}

Evaluate the response on these dimensions. For each, give a score 0.0-1.0 and specific feedback.

Dimensions to evaluate:
{dimensions}

Return a JSON object:
{{
  "scores": [
    {{
      "dimension": "<dimension_name>",
      "score": <0.0-1.0>,
      "feedback": "<specific issue>",
      "suggestion": "<how to improve>"
    }}
  ],
  "overall_assessment": "<1-2 sentence summary>",
  "should_improve": <true/false>
}}

Be harsh. A score of 1.0 means perfect — almost nothing is perfect."""


IMPROVEMENT_PROMPT = """You are an AI assistant improving your own response based on feedback.

ORIGINAL REQUEST:
{request}

YOUR PREVIOUS RESPONSE:
{response}

CRITICAL FEEDBACK:
{feedback}

Rewrite your response addressing ALL the feedback. Keep what works, fix what doesn't.
Output ONLY the improved response — no meta-commentary."""


class ReflectionEngine:
    """Run the self-reflection loop on agent outputs.

    Usage:
        engine = ReflectionEngine(llm_caller=my_llm_fn)
        result = await engine.reflect(
            request="Fix the bug in auth.py",
            output="I changed line 42 to...",
        )
        if result.improved:
            final_output = result.improved_output
    """

    def __init__(
        self,
        llm_caller: Callable | None = None,
        config: ReflectionConfig | None = None,
    ):
        self.config = config or ReflectionConfig()
        self._llm_caller = llm_caller

    async def reflect(self, request: str, output: str) -> ReflectionResult:
        """Run the full reflection loop on an output.

        Returns the best result after all iterations.
        """
        if self._llm_caller is None:
            # No LLM available — return original unchanged
            return ReflectionResult(
                original_output=output,
                improved_output=output,
                scores=[],
                overall_score=0.5,
                iteration=0,
                improved=False,
                reflection_time_ms=0,
            )

        best_output = output
        iteration = 0

        for iteration in range(1, self.config.max_iterations + 1):
            start = time.monotonic()

            # Step 1: Score the current output
            scores = await self._score_output(request, best_output)
            overall = self._weighted_average(scores)

            elapsed = (time.monotonic() - start) * 1000

            # Check if we're done
            if overall >= self.config.quality_threshold:
                return ReflectionResult(
                    original_output=output,
                    improved_output=best_output,
                    scores=scores,
                    overall_score=overall,
                    iteration=iteration,
                    improved=(best_output != output),
                    reflection_time_ms=elapsed,
                )

            # Step 2: Generate improvement
            feedback = self._format_feedback(scores)
            improved = await self._improve_output(request, best_output, feedback)

            if not improved or improved == best_output:
                # No improvement possible
                return ReflectionResult(
                    original_output=output,
                    improved_output=best_output,
                    scores=scores,
                    overall_score=overall,
                    iteration=iteration,
                    improved=False,
                    reflection_time_ms=elapsed,
                )

            # Check minimum improvement
            new_scores = await self._score_output(request, improved)
            new_overall = self._weighted_average(new_scores)

            if new_overall - overall < self.config.min_improvement:
                # Improvement too small — stop
                return ReflectionResult(
                    original_output=output,
                    improved_output=best_output,
                    scores=scores,
                    overall_score=overall,
                    iteration=iteration,
                    improved=False,
                    reflection_time_ms=elapsed,
                )

            best_output = improved

        # Max iterations reached
        final_scores = await self._score_output(request, best_output)
        return ReflectionResult(
            original_output=output,
            improved_output=best_output,
            scores=final_scores,
            overall_score=self._weighted_average(final_scores),
            iteration=iteration,
            improved=(best_output != output),
            reflection_time_ms=(time.monotonic() - start) * 1000,
        )

    async def _score_output(self, request: str, output: str) -> list[QualityScore]:
        """Score an output on all configured dimensions."""
        if self._llm_caller is None:
            return []

        dimensions_text = "\n".join(
            f"- {d.value}: {self._dimension_description(d)}" for d in self.config.dimensions
        )

        prompt = REFLECTION_PROMPT.format(
            request=request,
            response=output,
            dimensions=dimensions_text,
        )

        try:
            response = await self._call_llm(prompt)
            parsed = json.loads(response)

            scores = []
            for item in parsed.get("scores", []):
                dim_name = item.get("dimension", "")
                try:
                    dim = QualityDimension(dim_name)
                except ValueError:
                    continue
                scores.append(
                    QualityScore(
                        dimension=dim,
                        score=float(item.get("score", 0.5)),
                        feedback=item.get("feedback", ""),
                        suggestion=item.get("suggestion", ""),
                    )
                )
            return scores

        except (json.JSONDecodeError, KeyError, TypeError):
            # Fallback: return neutral scores
            return [
                QualityScore(
                    dimension=d,
                    score=0.5,
                    feedback="Unable to parse reflection",
                    suggestion="",
                )
                for d in self.config.dimensions
            ]

    async def _improve_output(self, request: str, output: str, feedback: str) -> str:
        """Generate an improved version of the output."""
        if self._llm_caller is None:
            return output

        prompt = IMPROVEMENT_PROMPT.format(
            request=request,
            response=output,
            feedback=feedback,
        )

        try:
            response = await self._call_llm(prompt)
            return response.strip()
        except Exception:
            return output

    async def _call_llm(self, prompt: str) -> str:
        """Call the LLM with a prompt. Returns raw text response."""
        if self._llm_caller is None:
            raise RuntimeError("No LLM caller configured")

        result = self._llm_caller(prompt)
        if asyncio.iscoroutine(result):
            return await result
        return str(result)

    def _weighted_average(self, scores: list[QualityScore]) -> float:
        """Compute weighted average of quality scores."""
        if not scores:
            return 0.5

        total_weight = 0.0
        weighted_sum = 0.0

        for score in scores:
            weight = self.config.weights.get(score.dimension, 0.1)
            weighted_sum += score.score * weight
            total_weight += weight

        return weighted_sum / total_weight if total_weight > 0 else 0.5

    def _format_feedback(self, scores: list[QualityScore]) -> str:
        """Format quality scores into actionable feedback text."""
        lines = []
        for score in scores:
            if score.score < 0.8:  # Only include areas needing improvement
                lines.append(
                    f"- {score.dimension.value} ({score.score:.1f}/1.0): "
                    f"{score.feedback}. Suggestion: {score.suggestion}"
                )
        return "\n".join(lines) if lines else "No specific issues found."

    def _dimension_description(self, dim: QualityDimension) -> str:
        """Human-readable description of a quality dimension."""
        descriptions = {
            QualityDimension.CORRECTNESS: "Factually accurate, code runs without errors, logic is sound",
            QualityDimension.COMPLETENESS: "Fully addresses all aspects of the request, nothing important omitted",
            QualityDimension.CLARITY: "Well-structured, easy to understand, appropriate detail level",
            QualityDimension.EFFICIENCY: "Uses the simplest/best approach, no unnecessary complexity",
            QualityDimension.SAFETY: "Avoids dangerous operations, validates inputs, handles errors",
            QualityDimension.STYLE: "Matches project conventions, consistent formatting, idiomatic",
        }
        return descriptions.get(dim, "")


# ── Convenience function ───────────────────────────────────────────────


async def reflect_on_output(
    request: str,
    output: str,
    llm_caller: Callable | None = None,
    max_iterations: int = 2,
) -> ReflectionResult:
    """One-shot reflection on an agent output.

    Usage:
        result = await reflect_on_output(
            request="Fix the login bug",
            output=agent_response,
            llm_caller=my_llm_fn,
        )
        final = result.improved_output if result.improved else output
    """
    engine = ReflectionEngine(
        llm_caller=llm_caller,
        config=ReflectionConfig(max_iterations=max_iterations),
    )
    return await engine.reflect(request, output)


# ── Singleton ─────────────────────────────────────────────────────────

_engine: ReflectionEngine | None = None


def get_reflection_engine() -> ReflectionEngine:
    """Get or create the global reflection engine."""
    global _engine
    if _engine is None:
        _engine = ReflectionEngine()
    return _engine


# Need asyncio import for _call_llm
import asyncio
