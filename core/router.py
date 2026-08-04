"""
Model router — intelligent routing across multiple LLM providers.

Routes tasks to the right model based on:
  - Task complexity (simple → fast/cheap, hard → frontier/expensive)
  - Cost budget (stay under a per-task or per-session limit)
  - Latency requirements (interactive → fast, batch → thorough)
  - Fallback chains (if primary fails, try next in chain)

Supports: OpenRouter, Anthropic, OpenAI, local models via Ollama.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum


class TaskComplexity(Enum):
    """Estimated complexity of a task."""

    TRIVIAL = "trivial"  # Single file read, simple grep
    SIMPLE = "simple"  # Small edit, single file change
    MODERATE = "moderate"  # Multi-file edit, refactoring
    COMPLEX = "complex"  # Architecture, debugging, multi-step
    FRONTIER = "frontier"  # Novel problem, requires deep reasoning


class LatencyRequirement(Enum):
    """How fast does this need to be?"""

    INTERACTIVE = "interactive"  # < 2s target
    NORMAL = "normal"  # < 10s target
    BATCH = "batch"  # No strict limit


@dataclass
class ModelConfig:
    """Configuration for a single model endpoint."""

    name: str  # Human-readable name
    provider: str  # "openrouter", "anthropic", "openai", "ollama"
    model_id: str  # Provider-specific model ID
    api_key: str = ""  # API key (if needed)
    base_url: str = ""  # Custom base URL
    max_tokens: int = 8192
    cost_per_1k_input: float = 0.0  # USD per 1K input tokens
    cost_per_1k_output: float = 0.0  # USD per 1K output tokens
    avg_latency_ms: float = 1000.0  # Typical response time
    strengths: list[str] = field(default_factory=list)  # e.g., ["code", "reasoning"]
    context_window: int = 128_000

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Estimate cost in USD for a given token count."""
        return (input_tokens / 1000) * self.cost_per_1k_input + (
            output_tokens / 1000
        ) * self.cost_per_1k_output


@dataclass
class RoutingDecision:
    """The router's decision for a given task."""

    model: ModelConfig
    complexity: TaskComplexity
    reason: str
    fallback_chain: list[ModelConfig] = field(default_factory=list)
    estimated_cost: float = 0.0
    estimated_latency_ms: float = 0.0


@dataclass
class RoutingResult:
    """Result of routing + executing a task."""

    decision: RoutingDecision
    output: str
    actual_latency_ms: float
    actual_cost: float
    model_used: str
    fallback_used: bool
    success: bool
    error: str | None = None


# ── Pre-built Model Catalog ───────────────────────────────────────────

MODEL_CATALOG: dict[str, ModelConfig] = {
    # Frontier models
    "claude-opus": ModelConfig(
        name="Claude Opus 4.6",
        provider="anthropic",
        model_id="claude-opus-4-6",
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.075,
        avg_latency_ms=3000,
        strengths=["reasoning", "code", "analysis", "writing"],
        context_window=200_000,
    ),
    "gpt-4o": ModelConfig(
        name="GPT-4o",
        provider="openai",
        model_id="gpt-4o",
        cost_per_1k_input=0.005,
        cost_per_1k_output=0.015,
        avg_latency_ms=2000,
        strengths=["code", "reasoning", "multimodal"],
        context_window=128_000,
    ),
    "gemini-2.5-pro": ModelConfig(
        name="Gemini 2.5 Pro",
        provider="openrouter",
        model_id="google/gemini-2.5-pro-preview",
        cost_per_1k_input=0.00125,
        cost_per_1k_output=0.01,
        avg_latency_ms=2500,
        strengths=["reasoning", "code", "long_context"],
        context_window=1_000_000,
    ),
    # Fast/cheap models
    "claude-haiku": ModelConfig(
        name="Claude Haiku 4.5",
        provider="anthropic",
        model_id="claude-haiku-4-5",
        cost_per_1k_input=0.0008,
        cost_per_1k_output=0.004,
        avg_latency_ms=800,
        strengths=["speed", "code"],
        context_window=200_000,
    ),
    "gpt-4o-mini": ModelConfig(
        name="GPT-4o Mini",
        provider="openai",
        model_id="gpt-4o-mini",
        cost_per_1k_input=0.00015,
        cost_per_1k_output=0.0006,
        avg_latency_ms=600,
        strengths=["speed", "simple_tasks"],
        context_window=128_000,
    ),
    "kimi-k3": ModelConfig(
        name="Kimi K3",
        provider="openrouter",
        model_id="moonshotai/kimi-k3",
        cost_per_1k_input=0.0005,
        cost_per_1k_output=0.002,
        avg_latency_ms=1200,
        strengths=["code", "reasoning", "speed"],
        context_window=256_000,
    ),
    # Local models
    "ollama-codellama": ModelConfig(
        name="CodeLlama 34B (Local)",
        provider="ollama",
        model_id="codellama:34b",
        cost_per_1k_input=0.0,
        cost_per_1k_output=0.0,
        avg_latency_ms=5000,
        strengths=["code", "privacy", "offline"],
        context_window=16_000,
    ),
}


# ── Complexity Estimator ──────────────────────────────────────────────


class ComplexityEstimator:
    """Estimate task complexity from the request text."""

    # Keywords that signal complexity
    COMPLEX_SIGNALS = {
        "architect",
        "design",
        "refactor",
        "migrate",
        "optimize",
        "debug",
        "investigate",
        "analyze",
        "security",
        "performance",
        "distributed",
        "concurrent",
        "async",
        "race condition",
        "memory leak",
        "deadlock",
        "scalab",
    }

    SIMPLE_SIGNALS = {
        "read",
        "show",
        "list",
        "what is",
        "find",
        "grep",
        "rename",
        "typo",
        "comment",
        "format",
        "import",
        "add logging",
        "print",
        "hello world",
    }

    @classmethod
    def estimate(cls, request: str, context_size: int = 0) -> TaskComplexity:
        """Estimate complexity from request text and context size."""
        request_lower = request.lower()

        # Count signal hits
        complex_hits = sum(1 for s in cls.COMPLEX_SIGNALS if s in request_lower)
        simple_hits = sum(1 for s in cls.SIMPLE_SIGNALS if s in request_lower)

        # Context size matters — more context = more complex
        if context_size > 50_000:
            complex_hits += 2
        elif context_size > 20_000:
            complex_hits += 1

        # Multi-step indicators
        if any(word in request_lower for word in ["then", "after that", "next", "finally", "step"]):
            complex_hits += 1

        # Code block indicators
        if "```" in request:
            complex_hits += 1

        # Decision logic
        if complex_hits >= 3:
            return TaskComplexity.FRONTIER
        elif complex_hits >= 2:
            return TaskComplexity.COMPLEX
        elif complex_hits >= 1 or (complex_hits == 0 and simple_hits == 0):
            return TaskComplexity.MODERATE
        elif simple_hits >= 1:
            return TaskComplexity.SIMPLE
        else:
            return TaskComplexity.TRIVIAL


# ── Model Router ───────────────────────────────────────────────────────


class ModelRouter:
    """Route tasks to the optimal model based on complexity, cost, and latency.

    Usage:
        router = ModelRouter(
            models=list(MODEL_CATALOG.values()),
            default_model="kimi-k3",
        )
        decision = router.route("Fix the auth bug in login.py")
        result = await router.execute("Fix the auth bug", llm_caller=my_fn)
    """

    def __init__(
        self,
        models: list[ModelConfig] | None = None,
        default_model: str = "kimi-k3",
        max_cost_per_task: float = 0.50,  # USD
        fallback_enabled: bool = True,
    ):
        self.models = {m.model_id: m for m in (models or list(MODEL_CATALOG.values()))}
        self.default_model_id = default_model
        self.max_cost_per_task = max_cost_per_task
        self.fallback_enabled = fallback_enabled
        self.estimator = ComplexityEstimator()

    def route(
        self,
        request: str,
        context_size: int = 0,
        latency_req: LatencyRequirement = LatencyRequirement.NORMAL,
        max_cost: float | None = None,
    ) -> RoutingDecision:
        """Decide which model to use for a given request.

        Returns a RoutingDecision with the chosen model and reasoning.
        """
        complexity = self.estimator.estimate(request, context_size)
        budget = max_cost or self.max_cost_per_task

        # Score each model
        candidates: list[tuple[float, ModelConfig]] = []

        for model in self.models.values():
            score = self._score_model(model, complexity, latency_req, budget)
            if score > 0:
                candidates.append((score, model))

        if not candidates:
            # Fallback to default
            default = self.models.get(self.default_model_id)
            if default is None:
                raise RuntimeError(f"Default model '{self.default_model_id}' not found")
            return RoutingDecision(
                model=default,
                complexity=complexity,
                reason="No suitable model found, using default",
            )

        # Sort by score descending
        candidates.sort(key=lambda x: x[0], reverse=True)
        _best_score, best_model = candidates[0]

        # Build fallback chain
        fallbacks = [m for _, m in candidates[1:4]] if self.fallback_enabled else []

        return RoutingDecision(
            model=best_model,
            complexity=complexity,
            reason=self._explain_choice(best_model, complexity, latency_req, budget),
            fallback_chain=fallbacks,
            estimated_cost=best_model.estimate_cost(2000, 1000),  # rough estimate
            estimated_latency_ms=best_model.avg_latency_ms,
        )

    async def execute(
        self,
        request: str,
        llm_caller: Callable,
        context_size: int = 0,
        latency_req: LatencyRequirement = LatencyRequirement.NORMAL,
    ) -> RoutingResult:
        """Route and execute a request with automatic fallback.

        Tries the primary model, falls back to chain on failure.
        """
        decision = self.route(request, context_size, latency_req)
        models_to_try = [decision.model, *decision.fallback_chain]

        last_error = None
        for i, model in enumerate(models_to_try):
            try:
                start = time.monotonic()
                output = await self._call_model(model, request, llm_caller)
                latency = (time.monotonic() - start) * 1000

                return RoutingResult(
                    decision=decision,
                    output=output,
                    actual_latency_ms=latency,
                    actual_cost=model.estimate_cost(2000, 1000),
                    model_used=model.name,
                    fallback_used=(i > 0),
                    success=True,
                )
            except Exception as e:
                last_error = str(e)
                continue

        # All models failed
        return RoutingResult(
            decision=decision,
            output="",
            actual_latency_ms=0,
            actual_cost=0,
            model_used="none",
            fallback_used=True,
            success=False,
            error=f"All models failed. Last error: {last_error}",
        )

    def _score_model(
        self,
        model: ModelConfig,
        complexity: TaskComplexity,
        latency_req: LatencyRequirement,
        budget: float,
    ) -> float:
        """Score a model for a given task. Higher = better fit."""
        score = 100.0

        # Complexity match
        complexity_scores = {
            TaskComplexity.TRIVIAL: {"speed": 30, "code": 10, "reasoning": 0},
            TaskComplexity.SIMPLE: {"speed": 20, "code": 20, "reasoning": 5},
            TaskComplexity.MODERATE: {"speed": 5, "code": 25, "reasoning": 20},
            TaskComplexity.COMPLEX: {"speed": 0, "code": 20, "reasoning": 30},
            TaskComplexity.FRONTIER: {"speed": 0, "code": 15, "reasoning": 35},
        }

        needed = complexity_scores.get(complexity, {})
        for strength in model.strengths:
            score += needed.get(strength, 0)

        # Latency penalty
        if latency_req == LatencyRequirement.INTERACTIVE:
            if model.avg_latency_ms > 3000:
                score -= 20
            elif model.avg_latency_ms > 1500:
                score -= 5
        elif latency_req == LatencyRequirement.BATCH:
            score += 5  # Don't care about latency

        # Cost penalty
        estimated = model.estimate_cost(2000, 1000)
        if estimated > budget:
            score -= 50  # Over budget
        elif estimated > budget * 0.5:
            score -= 10  # Getting expensive

        # Context window check
        # (would need actual context size to be precise)

        return max(score, 0)

    def _explain_choice(
        self,
        model: ModelConfig,
        complexity: TaskComplexity,
        latency_req: LatencyRequirement,
        budget: float,
    ) -> str:
        """Human-readable explanation of why this model was chosen."""
        parts = [f"Chose {model.name} for {complexity.value} task"]
        if latency_req == LatencyRequirement.INTERACTIVE:
            parts.append(f"(latency target: interactive, ~{model.avg_latency_ms:.0f}ms)")
        if model.cost_per_1k_input == 0:
            parts.append("(free/local model)")
        else:
            parts.append(f"(est. cost: ${model.estimate_cost(2000, 1000):.4f})")
        return " ".join(parts)

    async def _call_model(self, model: ModelConfig, request: str, llm_caller: Callable) -> str:
        """Call a specific model via the provided caller function."""
        # The llm_caller should accept (model_config, request) and return str
        result = llm_caller(model, request)
        if asyncio.iscoroutine(result):
            return await result
        return str(result)


# ── Convenience: quick routing ─────────────────────────────────────────


def route_task(request: str, context_size: int = 0) -> RoutingDecision:
    """Quick routing decision without creating a router instance."""
    router = ModelRouter()
    return router.route(request, context_size)


# ── Singleton ─────────────────────────────────────────────────────────

_router: ModelRouter | None = None


def get_router() -> ModelRouter:
    """Get or create the global model router."""
    global _router
    if _router is None:
        _router = ModelRouter()
    return _router


import asyncio
