"""
Multi-model orchestrator — capability registry, routing, fallback, fan-out,
health tracking, weighted load balancing, and per-call cost estimation.

Builds on top of core.llm_client.LLMClient for transport and core.providers
for credential / endpoint resolution. Persists per-model performance stats to
``data/memory_store/model_perf.json`` so routing decisions improve across
sessions.
"""

from __future__ import annotations

import asyncio
import json
import random
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .llm_client import LLMClient
from .providers import PROVIDER_DEFAULTS

# ── Capability & pricing metadata ───────────────────────────────────────

# Task types understood by ``route_task``. Capabilities are scored 0..10.
TASK_TYPES = {"code", "chat", "reasoning", "vision", "speed", "cost"}

# Rough USD-per-1M-token pricing for well-known models. Used only when the
# caller did not supply explicit pricing on ``register_model``. Unknown
# models fall back to a neutral 1.0/1.0 estimate.
_KNOWN_PRICING: dict[str, tuple[float, float]] = {
    # model substring -> (input_usd_per_1m, output_usd_per_1m)
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-opus": (15.00, 75.00),
    "claude-haiku": (0.25, 1.25),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-1.5-pro": (1.25, 5.00),
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    "deepseek-v3": (0.27, 1.10),
    "glm-4.5": (0.50, 2.00),
    "codellama": (0.00, 0.00),  # local ollama
    "llama3": (0.00, 0.00),
}


@dataclass
class ModelCapabilities:
    """Per-model capability scores (0-10) and pricing metadata."""

    code: float = 5.0
    chat: float = 5.0
    reasoning: float = 5.0
    vision: float = 0.0
    speed: float = 5.0
    # cost is treated as "cheapness" — higher = cheaper — so all scores
    # point the same direction for routing.
    cost: float = 5.0
    # USD per 1M tokens (input, output). Used by cost estimation.
    price_input: float = 1.0
    price_output: float = 1.0


@dataclass
class ModelConfig:
    """A registered model: how to reach it plus its capabilities."""

    name: str
    provider: str
    model_id: str
    api_key: str = ""
    base_url: str = ""
    temperature: float = 0.0
    max_tokens: int = 4096
    timeout_sec: int = 120
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)
    enabled: bool = True


@dataclass
class ModelStats:
    """Live performance counters for a registered model."""

    calls: int = 0
    successes: int = 0
    failures: int = 0
    total_latency: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    last_success_ts: float = 0.0
    last_failure_ts: float = 0.0
    last_error: str = ""
    consecutive_failures: int = 0

    @property
    def success_rate(self) -> float:
        if self.calls == 0:
            return 1.0  # benefit of the doubt until proven otherwise
        return self.successes / self.calls

    @property
    def avg_latency(self) -> float:
        if self.calls == 0:
            return 0.0
        return self.total_latency / self.calls

    @property
    def healthy(self) -> bool:
        """A model is healthy if it's not in a failure spiral."""
        if self.consecutive_failures >= 3:
            return False
        return not (self.calls >= 5 and self.success_rate < 0.3)


@dataclass
class CallResult:
    """Outcome of one orchestrated call."""

    ok: bool
    model: str
    content: str = ""
    message: dict | None = None
    latency: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    error: str = ""
    attempts: int = 1


# ── Orchestrator ────────────────────────────────────────────────────────


class MultiModelOrchestrator:
    """Routes chat-completion calls across a registry of models.

    Responsibilities:
      * Registry + capability metadata
      * Task-aware routing (``route_task``)
      * Sequential fallback (``call_with_fallback``)
      * Parallel fan-out (``parallel_call``)
      * Per-call retry with exponential backoff
      * Health checks and weighted round-robin load balancing
      * Performance tracking persisted to JSON
    """

    PERF_FILENAME = "model_perf.json"

    def __init__(
        self,
        perf_dir: str | Path | None = None,
        max_retries: int = 2,
        base_delay: float = 0.5,
        max_delay: float = 8.0,
        client_factory: Callable[[ModelConfig], Any] | None = None,
    ) -> None:
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        # Injection point for tests — must return an object exposing
        # ``async def chat_nonstreaming(messages, ...) -> dict | None``.
        self._client_factory = client_factory

        # Registry + stats
        self._models: dict[str, ModelConfig] = {}
        self._stats: dict[str, ModelStats] = {}
        self._clients: dict[str, Any] = {}
        self._rr_counters: dict[str, int] = {}  # weighted-round-robin state
        self._lock = threading.Lock()

        # Perf persistence
        root = Path(perf_dir) if perf_dir else Path("data") / "memory_store"
        root.mkdir(parents=True, exist_ok=True)
        self._perf_path = root / self.PERF_FILENAME
        self._load_perf()

    # ── Registry ───────────────────────────────────────────────────────

    def register_model(
        self,
        name: str,
        provider: str,
        model_id: str,
        *,
        api_key: str = "",
        base_url: str = "",
        temperature: float = 0.0,
        max_tokens: int = 4096,
        timeout_sec: int = 120,
        capabilities: ModelCapabilities | None = None,
    ) -> ModelConfig:
        """Add a model to the registry.

        If ``base_url``/``api_key`` are omitted we pull provider defaults
        from ``core.providers`` so a model can be registered by name alone.
        Pricing defaults are looked up from the well-known table.
        """
        provider_defaults = PROVIDER_DEFAULTS.get(provider, {})
        base_url = base_url or provider_defaults.get("default_base", "")
        if not api_key:
            import os

            env_key = provider_defaults.get("env_key")
            if env_key:
                api_key = os.environ.get(env_key, "")

        caps = capabilities or ModelCapabilities()
        # Auto-fill pricing from known table if caller left defaults.
        if (caps.price_input, caps.price_output) == (1.0, 1.0):
            for key, (pi, po) in _KNOWN_PRICING.items():
                if key in model_id.lower():
                    caps.price_input = pi
                    caps.price_output = po
                    break

        cfg = ModelConfig(
            name=name,
            provider=provider,
            model_id=model_id,
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_sec=timeout_sec,
            capabilities=caps,
        )
        with self._lock:
            self._models[name] = cfg
            self._stats.setdefault(name, ModelStats())
            self._clients.pop(name, None)  # force re-build on next call
        return cfg

    def unregister_model(self, name: str) -> None:
        with self._lock:
            self._models.pop(name, None)
            self._clients.pop(name, None)

    def list_models(self, enabled_only: bool = True) -> list[str]:
        with self._lock:
            return [n for n, c in self._models.items() if (c.enabled or not enabled_only)]

    def get_stats(self, name: str) -> ModelStats | None:
        return self._stats.get(name)

    # ── Routing ────────────────────────────────────────────────────────

    def route_task(
        self,
        task_type: str,
        complexity: float = 0.5,
        candidates: list[str] | None = None,
    ) -> str | None:
        """Pick the best model for a task.

        ``complexity`` is 0..1 — higher complexity weights capability scores
        more heavily, lower complexity weights speed/cost more heavily.
        Returns the model name or ``None`` if no candidate is healthy.
        """
        if task_type not in TASK_TYPES:
            raise ValueError(f"unknown task_type {task_type!r}; expected one of {TASK_TYPES}")

        pool = candidates or self.list_models()
        pool = [n for n in pool if self._stats.get(n, ModelStats()).healthy]
        if not pool:
            return None

        # Equivalent models (same top capability score within epsilon) get
        # weighted-round-robin'd so traffic spreads across them.
        scored = [(n, self._score(n, task_type, complexity)) for n in pool]
        scored.sort(key=lambda x: x[1], reverse=True)
        top_score = scored[0][1]
        epsilon = 0.5
        top_group = [n for n, s in scored if top_score - s <= epsilon]
        if len(top_group) == 1:
            return top_group[0]
        return self._weighted_pick(top_group)

    def _score(self, name: str, task_type: str, complexity: float) -> float:
        cfg = self._models[name]
        stats = self._stats.get(name, ModelStats())
        caps = cfg.capabilities

        primary = getattr(caps, task_type, 5.0)
        # Blend in speed + cost as secondary factors, weighted by inverse
        # of complexity — simple tasks should pick cheap+fast models.
        secondary = (caps.speed + caps.cost) / 2.0
        base = complexity * primary + (1.0 - complexity) * secondary
        # Tie-break using observed success rate (0.5 .. 1.5 multiplier).
        return base * (0.5 + stats.success_rate)

    def _weighted_pick(self, names: list[str]) -> str:
        """Weighted round-robin: weight = round(success_rate * 10), min 1."""
        weights: list[int] = []
        for n in names:
            sr = self._stats.get(n, ModelStats()).success_rate
            weights.append(max(1, round(sr * 10)))

        key = "|".join(names)
        idx = self._rr_counters.get(key, 0)
        # Build the expanded weighted sequence lazily.
        expanded: list[str] = []
        for n, w in zip(names, weights, strict=False):
            expanded.extend([n] * w)
        pick = expanded[idx % len(expanded)]
        self._rr_counters[key] = idx + 1
        return pick

    # ── Client construction ────────────────────────────────────────────

    def _get_client(self, name: str) -> Any:
        if name in self._clients:
            return self._clients[name]
        cfg = self._models[name]
        if self._client_factory is not None:
            client = self._client_factory(cfg)
        else:
            client = LLMClient(
                api_key=cfg.api_key,
                base_url=cfg.base_url,
                model=cfg.model_id,
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
                timeout_sec=cfg.timeout_sec,
                max_retries=0,  # orchestrator owns retry policy
            )
        self._clients[name] = client
        return client

    # ── Invocation ─────────────────────────────────────────────────────

    def call_with_fallback(
        self,
        messages: list[dict],
        models: list[str] | None = None,
        tools: list[dict] | None = None,
    ) -> CallResult:
        """Try each model in order until one succeeds.

        ``models=None`` means "all enabled models, ranked by general
        capability". Returns the first successful ``CallResult`` or the
        last failure if everything failed.
        """
        pool = models or self._rank_general()
        if not pool:
            return CallResult(ok=False, model="", error="no models registered")

        last_result: CallResult | None = None
        for name in pool:
            result = self._call_one(name, messages, tools)
            if result.ok:
                return result
            last_result = result
        return last_result or CallResult(ok=False, model="", error="unreachable")

    def parallel_call(
        self,
        messages: list[dict],
        models: list[str],
        tools: list[dict] | None = None,
        max_workers: int | None = None,
    ) -> dict[str, CallResult]:
        """Fan the same prompt out to multiple models concurrently.

        Returns ``{model_name: CallResult}`` — both successes and failures
        are reported, so callers can compare answers.
        """
        if not models:
            return {}
        workers = max_workers or min(len(models), 8)
        results: dict[str, CallResult] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self._call_one, name, messages, tools): name for name in models}
            for fut in as_completed(futures):
                name = futures[fut]
                try:
                    results[name] = fut.result()
                except Exception as e:  # pragma: no cover - defensive
                    results[name] = CallResult(ok=False, model=name, error=str(e))
        return results

    # ── Internals ──────────────────────────────────────────────────────

    def _rank_general(self) -> list[str]:
        """Default ordering when the caller doesn't specify a model list."""
        names = self.list_models()
        return sorted(
            names,
            key=lambda n: (
                self._stats.get(n, ModelStats()).healthy,
                self._stats.get(n, ModelStats()).success_rate,
                -self._stats.get(n, ModelStats()).avg_latency,
            ),
            reverse=True,
        )

    def _call_one(
        self,
        name: str,
        messages: list[dict],
        tools: list[dict] | None,
    ) -> CallResult:
        """Single-model call with retry + exponential backoff."""
        cfg = self._models.get(name)
        if cfg is None:
            return CallResult(ok=False, model=name, error=f"model {name!r} not registered")
        if not cfg.enabled:
            return CallResult(ok=False, model=name, error=f"model {name!r} disabled")

        client = self._get_client(name)
        attempts = 0
        last_err = ""
        for attempt in range(self.max_retries + 1):
            attempts = attempt + 1
            started = time.monotonic()
            try:
                msg = asyncio.run(client.chat_nonstreaming(messages, tools=tools))
                latency = time.monotonic() - started
                if msg is None:
                    raise RuntimeError("empty response")
                content = msg.get("content", "") or ""
                usage = msg.get("_usage", {}) or {}
                in_tok = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
                out_tok = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
                cost = self._estimate_cost(cfg, in_tok, out_tok)
                self._record_success(name, latency, in_tok, out_tok, cost)
                return CallResult(
                    ok=True,
                    model=name,
                    content=content,
                    message=msg,
                    latency=latency,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    cost_usd=cost,
                    attempts=attempts,
                )
            except Exception as e:
                latency = time.monotonic() - started
                last_err = f"{type(e).__name__}: {e}"
                self._record_failure(name, latency, last_err)
                if attempt < self.max_retries:
                    delay = self._retry_delay(attempt)
                    time.sleep(delay)

        return CallResult(
            ok=False,
            model=name,
            error=last_err,
            attempts=attempts,
        )

    def _retry_delay(self, attempt: int) -> float:
        """Exponential backoff with jitter, capped at ``max_delay``."""
        base = self.base_delay * (2**attempt)
        jitter = random.uniform(0.0, base * 0.25)
        return min(base + jitter, self.max_delay)

    def _estimate_cost(self, cfg: ModelConfig, in_tok: int, out_tok: int) -> float:
        """USD cost estimate for a call, given its token usage."""
        return (in_tok / 1_000_000.0) * cfg.capabilities.price_input + (
            out_tok / 1_000_000.0
        ) * cfg.capabilities.price_output

    # ── Stats & health ─────────────────────────────────────────────────

    def _record_success(
        self, name: str, latency: float, in_tok: int, out_tok: int, cost: float
    ) -> None:
        with self._lock:
            s = self._stats.setdefault(name, ModelStats())
            s.calls += 1
            s.successes += 1
            s.total_latency += latency
            s.total_input_tokens += in_tok
            s.total_output_tokens += out_tok
            s.total_cost_usd += cost
            s.last_success_ts = time.time()
            s.consecutive_failures = 0
            s.last_error = ""
        self._save_perf()

    def _record_failure(self, name: str, latency: float, error: str) -> None:
        with self._lock:
            s = self._stats.setdefault(name, ModelStats())
            s.calls += 1
            s.failures += 1
            s.total_latency += latency
            s.last_failure_ts = time.time()
            s.consecutive_failures += 1
            s.last_error = error
        self._save_perf()

    def health_check(self, name: str | None = None) -> dict[str, bool]:
        """Return ``{model_name: healthy}`` for one or all models."""
        if name is not None:
            s = self._stats.get(name, ModelStats())
            cfg = self._models.get(name)
            return {name: bool(cfg and cfg.enabled and s.healthy)}
        return {n: self.health_check(n)[n] for n in self.list_models(enabled_only=False)}

    def reset_stats(self, name: str | None = None) -> None:
        """Zero out counters (single model, or all when ``name=None``)."""
        with self._lock:
            if name is None:
                self._stats = {n: ModelStats() for n in self._models}
            else:
                self._stats[name] = ModelStats()
        self._save_perf()

    # ── Persistence ────────────────────────────────────────────────────

    def _save_perf(self) -> None:
        try:
            payload = {n: asdict(s) for n, s in self._stats.items()}
            tmp = self._perf_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self._perf_path)
        except OSError:
            pass  # perf stats are best-effort; never crash a call over them

    def _load_perf(self) -> None:
        if not self._perf_path.exists():
            return
        try:
            payload = json.loads(self._perf_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for name, raw in payload.items():
            if not isinstance(raw, dict):
                continue
            try:
                self._stats[name] = ModelStats(
                    **{k: v for k, v in raw.items() if k in ModelStats.__dataclass_fields__}
                )
            except TypeError:
                continue


# ── Smoke test ──────────────────────────────────────────────────────────


if __name__ == "__main__":
    """Smoke test using a mock provider — no network calls."""

    class _MockClient:
        """Mimics LLMClient.chat_nonstreaming without any HTTP."""

        def __init__(self, cfg: ModelConfig, *, fail: bool = False, delay: float = 0.01):
            self.cfg = cfg
            self.fail = fail
            self.delay = delay

        async def chat_nonstreaming(self, messages, tools=None, **_):
            await asyncio.sleep(self.delay)
            if self.fail:
                raise RuntimeError(f"simulated failure for {self.cfg.name}")
            user = next((m for m in reversed(messages) if m.get("role") == "user"), {})
            text = (user.get("content") or "")[:60]
            return {
                "role": "assistant",
                "content": f"[{self.cfg.name}] echo: {text}",
                "_usage": {"prompt_tokens": 12, "completion_tokens": 24},
                "_finish_reason": "stop",
            }

    def _factory(fail_names: set[str]):
        def build(cfg: ModelConfig):
            return _MockClient(cfg, fail=cfg.name in fail_names)

        return build

    orch = MultiModelOrchestrator(
        perf_dir="data/memory_store",
        max_retries=1,
        base_delay=0.01,
        client_factory=_factory(fail_names={"broken-model"}),
    )

    # Register three mock models with different capability profiles.
    orch.register_model(
        "fast-cheap",
        provider="deepseek",
        model_id="deepseek-chat",
        capabilities=ModelCapabilities(
            code=6,
            chat=7,
            reasoning=5,
            speed=9,
            cost=9,
            price_input=0.27,
            price_output=1.10,
        ),
    )
    orch.register_model(
        "smart-expensive",
        provider="anthropic",
        model_id="claude-sonnet-4",
        capabilities=ModelCapabilities(
            code=9,
            chat=9,
            reasoning=9,
            speed=4,
            cost=2,
            price_input=3.0,
            price_output=15.0,
        ),
    )
    orch.register_model(
        "broken-model",
        provider="openai",
        model_id="gpt-4o",
        capabilities=ModelCapabilities(code=8, chat=8, reasoning=8, speed=5, cost=3),
    )

    messages = [{"role": "user", "content": "Write a Python hello world."}]

    print("== route_task (code, high complexity) ==")
    pick = orch.route_task("code", complexity=0.9)
    print(f"  picked: {pick}")

    print("== route_task (chat, low complexity) ==")
    pick = orch.route_task("chat", complexity=0.1)
    print(f"  picked: {pick}")

    print("== call_with_fallback ==")
    res = orch.call_with_fallback(messages, models=["broken-model", "fast-cheap"])
    print(f"  ok={res.ok} model={res.model} attempts={res.attempts} latency={res.latency:.3f}s")
    print(f"  content: {res.content!r}")
    print(f"  cost estimate: ${res.cost_usd:.6f}")

    print("== parallel_call ==")
    out = orch.parallel_call(messages, models=["fast-cheap", "smart-expensive", "broken-model"])
    for name, r in out.items():
        print(
            f"  {name}: ok={r.ok} latency={r.latency:.3f}s cost=${r.cost_usd:.6f} err={r.error!r}"
        )

    print("== health_check ==")
    for name, healthy in orch.health_check().items():
        print(f"  {name}: {'healthy' if healthy else 'UNHEALTHY'}")

    print("== stats ==")
    for name in orch.list_models():
        s = orch.get_stats(name)
        print(
            f"  {name}: calls={s.calls} ok={s.successes} fail={s.failures} "
            f"sr={s.success_rate:.2f} avg_lat={s.avg_latency:.3f}s "
            f"tok={s.total_input_tokens}+{s.total_output_tokens} "
            f"cost=${s.total_cost_usd:.6f}"
        )

    print(f"== perf persisted to {orch._perf_path} ==")
