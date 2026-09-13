"""Night-3 tests: core/multi_model_orchestrator.py.

Covers registry + pricing auto-fill, task routing (complexity weighting,
unknown task types, unhealthy pools), weighted round-robin, sequential
fallback, retry/backoff attempts, parallel fan-out, health spirals, cost
estimation, and perf persistence. All clients are fakes — no network.
"""

from __future__ import annotations

import json

import pytest

from core.multi_model_orchestrator import (
    ModelCapabilities,
    ModelConfig,
    ModelStats,
    MultiModelOrchestrator,
)


class FakeClient:
    """Mimics LLMClient.chat_nonstreaming without any HTTP."""

    def __init__(self, cfg: ModelConfig, *, fail_times: int = 0, usage=None):
        self.cfg = cfg
        self.fail_times = fail_times
        self.calls = 0
        self.usage = usage or {"prompt_tokens": 100, "completion_tokens": 50}

    async def chat_nonstreaming(self, messages, tools=None, **_):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError(f"simulated failure #{self.calls}")
        return {
            "role": "assistant",
            "content": f"[{self.cfg.name}] ok",
            "_usage": dict(self.usage),
        }


def make_orch(tmp_path, fail_map=None, **kw):
    fail_map = fail_map or {}

    def factory(cfg: ModelConfig):
        return FakeClient(cfg, fail_times=fail_map.get(cfg.name, 0))

    kw.setdefault("perf_dir", tmp_path)
    kw.setdefault("base_delay", 0.001)
    kw.setdefault("max_delay", 0.01)
    return MultiModelOrchestrator(client_factory=factory, **kw)


def register_pair(orch):
    orch.register_model(
        "fast-cheap",
        provider="deepseek",
        model_id="deepseek-chat",
        capabilities=ModelCapabilities(code=6, chat=7, speed=9, cost=9),
    )
    orch.register_model(
        "smart",
        provider="anthropic",
        model_id="claude-sonnet-4",
        capabilities=ModelCapabilities(code=9, chat=9, speed=4, cost=2),
    )
    return orch


# ── registry ───────────────────────────────────────────────────────────


def test_register_fills_provider_defaults_and_pricing(tmp_path):
    orch = make_orch(tmp_path)
    cfg = orch.register_model("m", provider="deepseek", model_id="deepseek-chat")
    assert cfg.base_url  # provider default filled in
    assert (cfg.capabilities.price_input, cfg.capabilities.price_output) == (0.27, 1.10)


def test_register_explicit_pricing_not_overwritten(tmp_path):
    orch = make_orch(tmp_path)
    cfg = orch.register_model(
        "m",
        provider="deepseek",
        model_id="deepseek-chat",
        capabilities=ModelCapabilities(price_input=9.0, price_output=9.0),
    )
    assert (cfg.capabilities.price_input, cfg.capabilities.price_output) == (9.0, 9.0)


def test_register_unknown_model_neutral_pricing(tmp_path):
    orch = make_orch(tmp_path)
    cfg = orch.register_model("m", provider="custom", model_id="mystery-9000")
    assert (cfg.capabilities.price_input, cfg.capabilities.price_output) == (1.0, 1.0)


def test_unregister_and_list(tmp_path):
    orch = make_orch(tmp_path)
    register_pair(orch)
    assert set(orch.list_models()) == {"fast-cheap", "smart"}
    orch.unregister_model("smart")
    assert orch.list_models() == ["fast-cheap"]
    orch.unregister_model("missing")  # no error


def test_re_register_rebuilds_client(tmp_path):
    orch = make_orch(tmp_path)
    register_pair(orch)
    orch.register_model("fast-cheap", provider="deepseek", model_id="deepseek-chat")
    assert "fast-cheap" not in orch._clients  # forced rebuild on next call


# ── routing ────────────────────────────────────────────────────────────


def test_route_task_unknown_type_raises(tmp_path):
    orch = make_orch(tmp_path)
    register_pair(orch)
    with pytest.raises(ValueError, match="unknown task_type"):
        orch.route_task("teleport")


def test_route_task_complexity_weighting(tmp_path):
    orch = make_orch(tmp_path)
    register_pair(orch)
    # Simple task → cheap+fast model; hard task → capable model.
    assert orch.route_task("code", complexity=0.0) == "fast-cheap"
    assert orch.route_task("code", complexity=1.0) == "smart"


def test_route_task_no_healthy_models_returns_none(tmp_path):
    orch = make_orch(tmp_path)
    orch.register_model("bad", provider="deepseek", model_id="deepseek-chat")
    s = orch._stats["bad"]
    s.consecutive_failures = 3  # failure spiral → unhealthy
    assert orch.route_task("code") is None


def test_route_task_skips_unhealthy_keeps_healthy(tmp_path):
    orch = make_orch(tmp_path)
    register_pair(orch)
    orch._stats["smart"].consecutive_failures = 5
    assert orch.route_task("code", complexity=1.0) == "fast-cheap"


def test_weighted_pick_round_robins(tmp_path):
    orch = make_orch(tmp_path)
    register_pair(orch)
    picks = [orch._weighted_pick(["fast-cheap", "smart"]) for _ in range(20)]
    # Both have success_rate 1.0 → weight 10 each → expanded list is
    # 10x fast-cheap then 10x smart, cycling every 20 picks.
    assert picks == ["fast-cheap"] * 10 + ["smart"] * 10
    assert orch._weighted_pick(["fast-cheap", "smart"]) == "fast-cheap"  # wraps


def test_weighted_pick_favors_higher_success_rate(tmp_path):
    orch = make_orch(tmp_path)
    register_pair(orch)
    s = orch._stats["smart"]
    s.calls, s.successes = 10, 1  # success_rate 0.1 → weight 1
    picks = [orch._weighted_pick(["fast-cheap", "smart"]) for _ in range(11)]
    assert picks.count("smart") == 1
    assert picks.count("fast-cheap") == 10


# ── calls, fallback, retry ─────────────────────────────────────────────


def test_call_with_fallback_no_models(tmp_path):
    orch = make_orch(tmp_path)
    res = orch.call_with_fallback([{"role": "user", "content": "hi"}])
    assert res.ok is False and res.error == "no models registered"


def test_call_with_fallback_first_success_wins(tmp_path):
    orch = make_orch(tmp_path, fail_map={"fast-cheap": 99})
    register_pair(orch)
    res = orch.call_with_fallback(
        [{"role": "user", "content": "hi"}], models=["smart", "fast-cheap"]
    )
    assert res.ok and res.model == "smart"


def test_call_with_fallback_falls_through(tmp_path):
    orch = make_orch(tmp_path, fail_map={"smart": 99})
    register_pair(orch)
    res = orch.call_with_fallback(
        [{"role": "user", "content": "hi"}], models=["smart", "fast-cheap"]
    )
    assert res.ok and res.model == "fast-cheap"
    assert "[fast-cheap] ok" in res.content


def test_call_with_fallback_all_fail_returns_last_error(tmp_path):
    orch = make_orch(tmp_path, fail_map={"smart": 99, "fast-cheap": 99}, max_retries=0)
    register_pair(orch)
    res = orch.call_with_fallback([{"role": "user", "content": "hi"}])
    assert res.ok is False
    assert "simulated failure" in res.error


def test_retry_attempts_and_backoff(tmp_path):
    orch = make_orch(tmp_path, fail_map={"m": 2}, max_retries=3)
    orch.register_model("m", provider="deepseek", model_id="deepseek-chat")
    res = orch.call_with_fallback([{"role": "user", "content": "hi"}], models=["m"])
    assert res.ok and res.attempts == 3
    s = orch.get_stats("m")
    assert s.calls == 3 and s.failures == 2 and s.successes == 1
    assert s.consecutive_failures == 0  # reset by the success


def test_retry_exhausted_records_failure(tmp_path):
    orch = make_orch(tmp_path, fail_map={"m": 99}, max_retries=2)
    orch.register_model("m", provider="deepseek", model_id="deepseek-chat")
    res = orch.call_with_fallback([{"role": "user", "content": "hi"}], models=["m"])
    assert res.ok is False and res.attempts == 3
    s = orch.get_stats("m")
    assert s.consecutive_failures == 3
    assert s.healthy is False
    assert "simulated failure" in s.last_error


def test_call_unregistered_model(tmp_path):
    orch = make_orch(tmp_path)
    res = orch.call_with_fallback([{"role": "user", "content": "hi"}], models=["ghost"])
    assert res.ok is False and "not registered" in res.error


def test_call_disabled_model(tmp_path):
    orch = make_orch(tmp_path)
    cfg = orch.register_model("m", provider="deepseek", model_id="deepseek-chat")
    cfg.enabled = False
    res = orch.call_with_fallback([{"role": "user", "content": "hi"}], models=["m"])
    assert res.ok is False and "disabled" in res.error


def test_cost_estimation(tmp_path):
    orch = make_orch(tmp_path)
    orch.register_model(
        "m",
        provider="deepseek",
        model_id="deepseek-chat",
        capabilities=ModelCapabilities(price_input=2.0, price_output=10.0),
    )
    res = orch.call_with_fallback([{"role": "user", "content": "hi"}], models=["m"])
    # 100 in + 50 out tokens at $2/$10 per 1M
    assert res.cost_usd == pytest.approx(100 / 1e6 * 2.0 + 50 / 1e6 * 10.0)
    s = orch.get_stats("m")
    assert s.total_input_tokens == 100 and s.total_output_tokens == 50
    assert s.total_cost_usd == pytest.approx(res.cost_usd)
    assert s.avg_latency >= 0


def test_parallel_call_reports_success_and_failure(tmp_path):
    orch = make_orch(tmp_path, fail_map={"smart": 99}, max_retries=0)
    register_pair(orch)
    out = orch.parallel_call([{"role": "user", "content": "hi"}], models=["fast-cheap", "smart"])
    assert out["fast-cheap"].ok is True
    assert out["smart"].ok is False
    assert orch.parallel_call([{"role": "user", "content": "hi"}], models=[]) == {}


# ── health & stats ─────────────────────────────────────────────────────


def test_model_stats_defaults():
    s = ModelStats()
    assert s.success_rate == 1.0  # benefit of the doubt
    assert s.avg_latency == 0.0
    assert s.healthy is True


def test_health_spiral_and_low_success_rate(tmp_path):
    orch = make_orch(tmp_path, fail_map={"m": 99}, max_retries=0)
    orch.register_model("m", provider="deepseek", model_id="deepseek-chat")
    for _ in range(3):
        orch.call_with_fallback([{"role": "user", "content": "hi"}], models=["m"])
    assert orch.health_check("m") == {"m": False}
    # Slow bleed: 5+ calls with <30% success also unhealthy.
    s = orch._stats["m"]
    s.calls, s.successes, s.failures, s.consecutive_failures = 10, 2, 8, 0
    assert s.healthy is False
    assert orch.health_check()["m"] is False


def test_health_check_unknown_model(tmp_path):
    orch = make_orch(tmp_path)
    assert orch.health_check("ghost") == {"ghost": False}


def test_reset_stats(tmp_path):
    orch = make_orch(tmp_path)
    register_pair(orch)
    orch.call_with_fallback([{"role": "user", "content": "hi"}], models=["smart"])
    assert orch.get_stats("smart").calls == 1
    orch.reset_stats("smart")
    assert orch.get_stats("smart").calls == 0
    orch.reset_stats()
    assert all(orch.get_stats(n).calls == 0 for n in orch.list_models(enabled_only=False))


def test_rank_general_prefers_healthy(tmp_path):
    orch = make_orch(tmp_path)
    register_pair(orch)
    orch._stats["smart"].consecutive_failures = 3
    ranked = orch._rank_general()
    assert ranked[0] == "fast-cheap"


# ── persistence ────────────────────────────────────────────────────────


def test_perf_persisted_and_reloaded(tmp_path):
    orch = make_orch(tmp_path)
    register_pair(orch)
    orch.call_with_fallback([{"role": "user", "content": "hi"}], models=["smart"])
    perf_file = tmp_path / MultiModelOrchestrator.PERF_FILENAME
    assert perf_file.exists()
    payload = json.loads(perf_file.read_text(encoding="utf-8"))
    assert payload["smart"]["calls"] == 1

    orch2 = make_orch(tmp_path)
    register_pair(orch2)
    assert orch2.get_stats("smart").calls == 1


def test_perf_load_ignores_corrupt_file(tmp_path):
    (tmp_path / MultiModelOrchestrator.PERF_FILENAME).write_text("not json", encoding="utf-8")
    orch = make_orch(tmp_path)  # must not raise
    assert orch.get_stats("nothing") is None


def test_perf_save_failure_does_not_break_call(tmp_path, monkeypatch):
    orch = make_orch(tmp_path)
    register_pair(orch)
    monkeypatch.setattr(
        "pathlib.Path.write_text",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    res = orch.call_with_fallback([{"role": "user", "content": "hi"}], models=["smart"])
    assert res.ok  # best-effort persistence never breaks a call
