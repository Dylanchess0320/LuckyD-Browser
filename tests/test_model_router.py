"""Unit tests for browser/browser_core/model_router.py and its ai_bridge wiring.

The router wraps litellm (an OPTIONAL dependency) behind a lazy import, so
every test here injects a fake litellm module — no test needs litellm
installed, and the no-litellm path is exercised by faking its absence.
"""

from __future__ import annotations

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

from browser_core import ai_bridge, model_router
from browser_core.ai_bridge import AIBridge
from browser_core.model_router import (
    ModelRouter,
    ProviderHealth,
    RouteEntry,
    RouterConfig,
    RouterUnavailableError,
)

MSGS = [{"role": "user", "content": "hello"}]


# ── Fake litellm ─────────────────────────────────────────────────────────


def _ok_response(text: str) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


def _chunk(text: str) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])


class FakeLiteLLM:
    """Stand-in for the litellm module: per-model scripted outcomes.

    behavior maps a model id to "ok" (default), "fail", or "empty".
    Every call's kwargs are recorded in .calls for order assertions.
    """

    def __init__(self, behavior: dict[str, str] | None = None) -> None:
        self.behavior = behavior or {}
        self.calls: list[dict] = []

    async def acompletion(self, **kwargs):
        self.calls.append(kwargs)
        model = kwargs["model"]
        action = self.behavior.get(model, "ok")
        if action == "fail":
            raise RuntimeError(f"boom from {model}")
        if kwargs.get("stream"):

            async def _gen():
                yield _chunk("streamed ")
                yield _chunk("text")

            return _gen()
        if action == "empty":
            return _ok_response("")
        return _ok_response(f"answer from {model}")


@pytest.fixture()
def fake_litellm(monkeypatch: pytest.MonkeyPatch) -> FakeLiteLLM:
    fake = FakeLiteLLM()
    monkeypatch.setattr(model_router, "_load_litellm", lambda: fake)
    return fake


@pytest.fixture()
def no_litellm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_router, "_load_litellm", lambda: None)


def _entry(name: str, tier: str) -> RouteEntry:
    return RouteEntry(name=name, model=f"model-{name}", tier=tier)


# ── Config parsing ───────────────────────────────────────────────────────


class TestConfigParsing:
    def test_defaults(self) -> None:
        cfg = RouterConfig.from_env({})
        assert cfg.enabled is True
        assert cfg.cooldown_sec == 60.0
        assert cfg.tier_order == ("free", "cheap", "local")
        assert cfg.free_models == cfg.cheap_models == cfg.local_models == ()

    @pytest.mark.parametrize("raw", ["off", "0", "false", "No", "OFF"])
    def test_master_switch_off(self, raw: str) -> None:
        assert RouterConfig.from_env({"LUCKYD_MODEL_ROUTER": raw}).enabled is False

    @pytest.mark.parametrize("raw", ["on", "1", "true", "yes", "garbage", ""])
    def test_master_switch_on_or_default(self, raw: str) -> None:
        assert RouterConfig.from_env({"LUCKYD_MODEL_ROUTER": raw}).enabled is True

    def test_chain_order_parsing(self) -> None:
        cfg = RouterConfig.from_env({"LUCKYD_ROUTER_CHAIN": "local, free"})
        assert cfg.tier_order == ("local", "free")

    def test_chain_order_drops_unknown_and_dupes(self) -> None:
        cfg = RouterConfig.from_env({"LUCKYD_ROUTER_CHAIN": "bogus,cheap,cheap,free"})
        assert cfg.tier_order == ("cheap", "free")

    def test_chain_order_all_garbage_falls_back_to_default(self) -> None:
        cfg = RouterConfig.from_env({"LUCKYD_ROUTER_CHAIN": "nope,nah"})
        assert cfg.tier_order == ("free", "cheap", "local")

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("30", 30.0), ("2.5", 2.5), ("abc", 60.0), ("-5", 60.0), ("0", 60.0), ("99999", 3600.0)],
    )
    def test_cooldown_parsing(self, raw: str, expected: float) -> None:
        cfg = RouterConfig.from_env({"LUCKYD_ROUTER_COOLDOWN_SEC": raw})
        assert cfg.cooldown_sec == expected

    def test_explicit_model_lists(self) -> None:
        cfg = RouterConfig.from_env(
            {
                "LUCKYD_ROUTER_FREE_MODELS": " openrouter/free , gemini/x ,, ",
                "LUCKYD_ROUTER_CHEAP_MODELS": "gpt-6-sol",
                "LUCKYD_ROUTER_LOCAL_MODELS": "ollama/llama3.2",
            }
        )
        assert cfg.free_models == ("openrouter/free", "gemini/x")
        assert cfg.cheap_models == ("gpt-6-sol",)
        assert cfg.local_models == ("ollama/llama3.2",)
        entries = cfg.explicit_entries()
        assert [(e.name, e.tier) for e in entries] == [
            ("openrouter/free", "free"),
            ("gemini/x", "free"),
            ("gpt-6-sol", "cheap"),
            ("ollama/llama3.2", "local"),
        ]


# ── Fallback order + health ──────────────────────────────────────────────


class TestFallbackOrder:
    async def test_tier_order_wins_over_insertion_order(self, fake_litellm: FakeLiteLLM) -> None:
        router = ModelRouter(
            entries=[_entry("loc", "local"), _entry("chp", "cheap"), _entry("fre", "free")]
        )
        assert router.chain_names() == ["fre", "chp", "loc"]
        text, used = await router.chat(MSGS)
        assert (text, used) == ("answer from model-fre", "fre")
        assert [c["model"] for c in fake_litellm.calls] == ["model-fre"]

    async def test_falls_through_tiers_on_failure(self, fake_litellm: FakeLiteLLM) -> None:
        fake_litellm.behavior = {"model-fre": "fail", "model-chp": "fail"}
        router = ModelRouter(
            entries=[_entry("fre", "free"), _entry("chp", "cheap"), _entry("loc", "local")]
        )
        _text, used = await router.chat(MSGS)
        assert used == "loc"
        assert [c["model"] for c in fake_litellm.calls] == ["model-fre", "model-chp", "model-loc"]

    async def test_empty_response_counts_as_failure(self, fake_litellm: FakeLiteLLM) -> None:
        fake_litellm.behavior = {"model-fre": "empty"}
        router = ModelRouter(entries=[_entry("fre", "free"), _entry("chp", "cheap")])
        _text, used = await router.chat(MSGS)
        assert used == "chp"
        assert router.health.snapshot()["fre"]["failures"] == 1

    async def test_all_fail_raises_router_unavailable(self, fake_litellm: FakeLiteLLM) -> None:
        fake_litellm.behavior = {"model-fre": "fail"}
        router = ModelRouter(entries=[_entry("fre", "free")])
        with pytest.raises(RouterUnavailableError):
            await router.chat(MSGS)

    async def test_streaming_tokens_forwarded(self, fake_litellm: FakeLiteLLM) -> None:
        seen: list[str] = []
        router = ModelRouter(entries=[_entry("fre", "free")])
        text, _used = await router.chat(MSGS, on_token=seen.append)
        assert text == "streamed text"
        assert seen == ["streamed ", "text"]
        assert fake_litellm.calls[0]["stream"] is True

    async def test_api_base_and_key_passed_through(self, fake_litellm: FakeLiteLLM) -> None:
        entry = RouteEntry(
            name="gw", model="openai/m", tier="free", api_base="https://gw.example/v1", api_key="k"
        )
        router = ModelRouter(entries=[entry])
        await router.chat(MSGS)
        call = fake_litellm.calls[0]
        assert call["api_base"] == "https://gw.example/v1"
        assert call["api_key"] == "k"


class TestCooldown:
    def _router_with_clock(self, fake: FakeLiteLLM) -> tuple[ModelRouter, list[float]]:
        now = [1000.0]
        router = ModelRouter(
            entries=[_entry("a", "free"), _entry("b", "free")],
            config=RouterConfig(cooldown_sec=60.0),
        )
        router.health = ProviderHealth(cooldown_sec=60.0, clock=lambda: now[0])
        return router, now

    async def test_failed_entry_skipped_during_cooldown(self, fake_litellm: FakeLiteLLM) -> None:
        fake_litellm.behavior = {"model-a": "fail"}
        router, _now = self._router_with_clock(fake_litellm)
        _text, used = await router.chat(MSGS)
        assert used == "b"
        # Second call: a is cooling down, so it is not even attempted.
        fake_litellm.calls.clear()
        _text, used = await router.chat(MSGS)
        assert used == "b"
        assert [c["model"] for c in fake_litellm.calls] == ["model-b"]

    async def test_cooldown_expires(self, fake_litellm: FakeLiteLLM) -> None:
        fake_litellm.behavior = {"model-a": "fail"}
        router, now = self._router_with_clock(fake_litellm)
        await router.chat(MSGS)
        assert router.health.in_cooldown("a")
        now[0] += 61.0
        assert not router.health.in_cooldown("a")
        fake_litellm.calls.clear()
        await router.chat(MSGS)
        assert fake_litellm.calls[0]["model"] == "model-a"  # retried first again

    async def test_all_cooling_down_fails_open(self, fake_litellm: FakeLiteLLM) -> None:
        fake_litellm.behavior = {"model-a": "fail"}
        router, _now = self._router_with_clock(fake_litellm)
        router.entries = [_entry("a", "free")]
        with pytest.raises(RouterUnavailableError):
            await router.chat(MSGS)
        # Immediately again: a is in cooldown, but with no alternative the
        # router must retry it rather than refuse outright.
        with pytest.raises(RouterUnavailableError):
            await router.chat(MSGS)
        assert len(fake_litellm.calls) == 2

    def test_backoff_grows_and_success_resets(self) -> None:
        now = [0.0]
        health = ProviderHealth(cooldown_sec=60.0, clock=lambda: now[0])
        health.record_failure("x", "e1")
        assert health.cooldown_remaining("x") == 60.0
        now[0] += 61.0
        health.record_failure("x", "e2")
        assert health.cooldown_remaining("x") == 120.0
        health.record_success("x", latency_sec=0.25)
        snap = health.snapshot()["x"]
        assert snap["successes"] == 1
        assert snap["consecutive_failures"] == 0
        assert snap["cooldown_remaining_sec"] == 0.0
        assert snap["last_error"] == ""
        assert snap["last_latency_sec"] == 0.25


# ── Graceful degradation ─────────────────────────────────────────────────


class TestGracefulDegradation:
    async def test_no_litellm_raises_router_unavailable(self, no_litellm: None) -> None:
        router = ModelRouter(entries=[_entry("fre", "free")])
        with pytest.raises(RouterUnavailableError):
            await router.chat(MSGS)

    async def test_disabled_router_raises(self, fake_litellm: FakeLiteLLM) -> None:
        router = ModelRouter(entries=[_entry("fre", "free")], config=RouterConfig(enabled=False))
        with pytest.raises(RouterUnavailableError):
            await router.chat(MSGS)
        assert fake_litellm.calls == []

    async def test_empty_chain_raises(self, fake_litellm: FakeLiteLLM) -> None:
        router = ModelRouter(entries=[])
        with pytest.raises(RouterUnavailableError):
            await router.chat(MSGS)


# ── Bridge integration ───────────────────────────────────────────────────


@pytest.fixture()
def bridge_env(monkeypatch: pytest.MonkeyPatch):
    """AIBridge with a hermetic env (one keyed provider, no local servers)."""

    def _make(env: dict[str, str]) -> AIBridge:
        monkeypatch.setattr(ai_bridge, "_load_env", lambda: dict(env))
        monkeypatch.setattr(AIBridge, "_detect_local", staticmethod(lambda env: {}))
        return AIBridge()

    return _make


class TestBridgeIntegration:
    async def test_router_serves_auto_chat(self, bridge_env, fake_litellm: FakeLiteLLM) -> None:
        # Both Cline gateway entries sit ahead of DeepSeek in the chain;
        # fail them so the router has to fall through to DeepSeek.
        fake_litellm.behavior = {
            "openai/cline-pass/deepseek-v4-pro": "fail",
            "openai/deepseek/deepseek-chat": "fail",
        }
        bridge = bridge_env({"DEEPSEEK_API_KEY": "test-key"})
        assert bridge._router is not None
        text, used = await bridge.chat(MSGS)
        assert used == "deepseek"
        assert text == "answer from deepseek/deepseek-v4-flash"
        models_called = [c["model"] for c in fake_litellm.calls]
        assert "deepseek/deepseek-v4-flash" in models_called

    async def test_router_health_recorded_on_bridge(self, bridge_env, fake_litellm) -> None:
        fake_litellm.behavior = {
            "openai/cline-pass/deepseek-v4-pro": "fail",
            "openai/deepseek/deepseek-chat": "fail",
        }
        bridge = bridge_env({"DEEPSEEK_API_KEY": "test-key"})
        await bridge.chat(MSGS)
        status = bridge.router_status()
        assert status["enabled"] is True
        assert "deepseek" in status["chain"]
        assert status["health"]["deepseek"]["successes"] == 1

    async def test_no_litellm_falls_back_to_legacy_chain(self, bridge_env, no_litellm) -> None:
        bridge = bridge_env({"DEEPSEEK_API_KEY": "test-key"})
        bridge._configs = {"deepseek": bridge._configs["deepseek"]}

        async def _legacy(name, info, messages, on_token):
            return "legacy answer"

        bridge._call = _legacy  # type: ignore[method-assign]
        text, used = await bridge.chat(MSGS)
        assert (text, used) == ("legacy answer", "deepseek")

    async def test_router_disabled_by_env_uses_legacy(self, bridge_env, fake_litellm) -> None:
        bridge = bridge_env({"DEEPSEEK_API_KEY": "test-key", "LUCKYD_MODEL_ROUTER": "off"})
        assert bridge._router is None
        bridge._configs = {"deepseek": bridge._configs["deepseek"]}

        async def _legacy(name, info, messages, on_token):
            return "legacy answer"

        bridge._call = _legacy  # type: ignore[method-assign]
        text, used = await bridge.chat(MSGS)
        assert (text, used) == ("legacy answer", "deepseek")
        assert fake_litellm.calls == []
        assert bridge.router_status()["enabled"] is False

    async def test_explicit_provider_never_routes(self, bridge_env, fake_litellm) -> None:
        bridge = bridge_env({"DEEPSEEK_API_KEY": "test-key"})
        bridge._configs = {"deepseek": bridge._configs["deepseek"]}

        async def _legacy(name, info, messages, on_token):
            return "legacy answer"

        bridge._call = _legacy  # type: ignore[method-assign]
        text, used = await bridge.chat(MSGS, provider="deepseek")
        assert (text, used) == ("legacy answer", "deepseek")
        assert fake_litellm.calls == []

    def test_router_entry_mapping(self, bridge_env) -> None:
        bridge = bridge_env({"DEEPSEEK_API_KEY": "k", "GOOGLE_API_KEY": "g"})
        deepseek = bridge._router_entry_for("deepseek")
        assert deepseek is not None
        assert (deepseek.model, deepseek.tier, deepseek.api_key) == (
            "deepseek/deepseek-v4-flash",
            "cheap",
            "k",
        )
        google = bridge._router_entry_for("google")
        assert google is not None
        assert (google.model, google.tier) == ("gemini/gemini-3.8-flash", "free")
