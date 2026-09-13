"""Night-1 audit: core/router.py (ModelRouter) and browser_core/dashboard.py
(read-only: dashboard.py is owned by the UX agent — tests only)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from browser_core.dashboard import (
    dashboard_html,
    hq_shell_html,
    hq_splash_html,
)

from core.router import (
    ComplexityEstimator,
    ModelConfig,
    ModelRouter,
    TaskComplexity,
    get_router,
    route_task,
)

# ── complexity estimation ────────────────────────────────────────────


def test_estimate_simple() -> None:
    assert ComplexityEstimator.estimate("fix typo in readme") == TaskComplexity.SIMPLE
    assert ComplexityEstimator.estimate("read the config file") == TaskComplexity.SIMPLE


def test_estimate_complex_and_frontier() -> None:
    assert ComplexityEstimator.estimate("debug the slow query") == TaskComplexity.MODERATE
    assert (
        ComplexityEstimator.estimate("architect a distributed async system; fix the race condition")
        == TaskComplexity.FRONTIER
    )


def test_estimate_no_signals_defaults_moderate() -> None:
    assert ComplexityEstimator.estimate("") == TaskComplexity.MODERATE
    assert ComplexityEstimator.estimate("hello") == TaskComplexity.MODERATE


def test_estimate_context_size_raises_complexity() -> None:
    assert (
        ComplexityEstimator.estimate("summarize this", context_size=60_000)
        == TaskComplexity.COMPLEX
    )


# ── routing decisions ────────────────────────────────────────────────


def test_route_returns_decision_with_fallbacks() -> None:
    r = ModelRouter()
    d = r.route("refactor the auth module")
    assert isinstance(d.model, ModelConfig)
    assert d.complexity in TaskComplexity
    assert d.reason
    assert len(d.fallback_chain) <= 3
    assert d.estimated_cost >= 0


def test_route_fallback_disabled() -> None:
    d = ModelRouter(fallback_enabled=False).route("anything")
    assert d.fallback_chain == []


def test_route_never_raises_on_garbage() -> None:
    assert route_task("").model is not None
    assert route_task("\x00\xff weird").model is not None


def test_route_unknown_default_id_tolerated_when_candidates_exist() -> None:
    """An unknown default_model_id does not break routing while candidates
    score > 0. (The "Default model ... not found" RuntimeError branch is
    currently unreachable: _score_model floors at max(score, 0) from a base
    of 100, so `candidates` is never empty. Left as-is — dead defensive
    code, not a behavior bug.)"""
    r = ModelRouter(default_model="ghost")
    d = r.route("x")
    assert isinstance(d.model, ModelConfig)
    assert d.model.model_id != "ghost"


def test_estimate_cost_math() -> None:
    m = ModelConfig(
        name="t", provider="p", model_id="m", cost_per_1k_input=0.001, cost_per_1k_output=0.002
    )
    assert m.estimate_cost(2000, 1000) == pytest.approx(0.004)
    assert m.estimate_cost(0, 0) == 0.0


def test_execute_falls_back_on_failure() -> None:
    models = [
        ModelConfig(name="one", provider="p", model_id="m1"),
        ModelConfig(name="two", provider="p", model_id="m2"),
    ]
    r = ModelRouter(models=models)
    calls: list[str] = []

    def caller(model, request):
        calls.append(model.model_id)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return "ok"

    res = asyncio.run(r.execute("do it", llm_caller=caller))
    assert res.success and res.output == "ok"
    assert res.fallback_used is True
    assert res.model_used == "two"
    assert len(calls) == 2


def test_execute_all_fail() -> None:
    r = ModelRouter(models=[ModelConfig(name="one", provider="p", model_id="m1")])

    def caller(model, request):
        raise RuntimeError("down")

    res = asyncio.run(r.execute("do it", llm_caller=caller))
    assert res.success is False
    assert "All models failed" in (res.error or "")


def test_get_router_singleton() -> None:
    assert get_router() is get_router()


# ── dashboard (read-only tests) ──────────────────────────────────────


def _fake_settings(**overrides):
    vals = {"theme": "neon", "browser_api_token": "SECRET-TOKEN-ABC", **overrides}
    return SimpleNamespace(get=lambda k, d=None: vals.get(k, d))


def test_dashboard_html_renders_and_replaces_tiles_placeholder() -> None:
    html = dashboard_html(_fake_settings())
    assert "__PLATFORM_TILES__" not in html
    assert "--ld-accent" in html
    assert "<!DOCTYPE html>" in html or "<html" in html


def test_dashboard_html_never_embeds_tokens() -> None:
    """4.0: the session cookie authenticates; served HTML must not carry
    the bearer credential."""
    html = dashboard_html(_fake_settings())
    assert "SECRET-TOKEN-ABC" not in html
    assert "Bearer" not in html


def test_hq_splash_states() -> None:
    err = hq_splash_html("http://127.0.0.1:8000", "error", "boot failed")
    assert "Coding agent backend unavailable" in err
    assert "boot failed" in err
    assert "refresh" not in err.lower()
    starting = hq_splash_html("http://127.0.0.1:8000", "starting")
    assert "refresh" in starting and "content='3'" in starting


def test_hq_shell_embeds_url_and_theme() -> None:
    html = hq_shell_html("http://127.0.0.1:8000", _fake_settings())
    assert 'src="http://127.0.0.1:8000"' in html
    assert "__HARNESS_URL__" not in html
    assert "--ld-grad" in html
    assert "SECRET-TOKEN-ABC" not in html
