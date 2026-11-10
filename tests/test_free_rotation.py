"""LuckyD 10.4 free-model auto-rotation: priority, triggers, rotator, defaults.

Covers Dylan's 2026-09-24 requirement — every LuckyD surface auto-rotates the
best FREE models by default (Cline free tier -> Gemini free tier -> Ollama
local -> other free providers), with no user config required.

Hermetic: provider env is stripped per-test, the Cline session token is
mocked, HTTP is stubbed at the httpx seam, and only fake keys ("test-key…")
are ever used — a developer's real ~/.cline session or 402 marker can never
flip results (LUCKYD_CLINE_CREDIT_STATE is already pointed at a per-test path
by tests/conftest.py).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))

# Intentionally fake API keys for tests (not real secrets).
TEST_API_KEY = "test-key-not-a-secret"

_ERR_403 = {"role": "assistant", "content": "[API Error: 403 — forbidden] nope"}
_ERR_402 = {"role": "assistant", "content": "[API Error: 402 — payment required] broke"}
_GOOD = {"role": "assistant", "content": "OK"}

_PROVIDER_ENV_VARS = (
    "CODING_AGENT_PROVIDER",
    "CODING_AGENT_API_KEY",
    "CLINEPASS_API_KEY",
    "CLINEPASS_BASE_URL",
    "CLINEPASS_MODEL",
    "CLINE_USAGE_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "GOOGLE_API_KEY",
    "GOOGLE_BASE_URL",
    "GOOGLE_MODEL",
    "ZAI_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL",
    "OPENROUTER_MODEL",
    "GROQ_API_KEY",
    "GROQ_BASE_URL",
    "GROQ_MODEL",
    "OPENCODE_API_KEY",
    "MINIMAX_API_KEY",
    "DEEPSEEK_API_KEY",
    "META_API_KEY",
    "META_BASE_URL",
    "META_MODEL",
    "MISTRAL_API_KEY",
    "MISTRAL_BASE_URL",
    "MISTRAL_MODEL",
    "OLLAMA_HOST",
    "OLLAMA_MODEL",
)


@pytest.fixture
def free_env(monkeypatch):
    """Strip every provider/auth env var; no Cline session; no localhost."""
    from core import providers

    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    providers.reset_cline_session_cache()
    monkeypatch.setattr(providers, "cline_session_token", lambda: "")

    # Ollama liveness probe always refuses — tests opt back in per-case.
    def _refuse(*a, **k):
        raise ConnectionError("no network in free-rotation tests")

    monkeypatch.setattr(httpx, "get", _refuse)
    return monkeypatch


def _status_error(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://example.test/v1/chat/completions")
    resp = httpx.Response(code, json={"error": {"message": "x"}}, request=req)
    return httpx.HTTPStatusError(f"{code}", request=req, response=resp)


# ── rotation trigger predicate ─────────────────────────────────────────


class TestTriggerPredicate:
    @pytest.mark.parametrize("code", [402, 403, 429, 408, 500, 502, 503, 504])
    def test_trigger_codes_rotate(self, code):
        from core.free_rotation import should_rotate_free_model

        assert should_rotate_free_model(_status_error(code)) is True
        assert (
            should_rotate_free_model(
                {"role": "assistant", "content": f"[API Error: {code} — boom] x"}
            )
            is True
        )

    @pytest.mark.parametrize("code", [400, 401, 404, 418])
    def test_non_trigger_codes_do_not_rotate(self, code):
        from core.free_rotation import should_rotate_free_model

        assert should_rotate_free_model(_status_error(code)) is False
        assert (
            should_rotate_free_model(
                {"role": "assistant", "content": f"[API Error: {code} — boom] x"}
            )
            is False
        )

    def test_timeouts_and_connection_errors_rotate(self):
        from core.free_rotation import should_rotate_free_model

        assert should_rotate_free_model(httpx.ConnectError("refused")) is True
        assert should_rotate_free_model(httpx.ReadTimeout("slow")) is True
        assert should_rotate_free_model(httpx.RemoteProtocolError("bad")) is True

    def test_unknown_errors_do_not_rotate(self):
        from core.free_rotation import should_rotate_free_model

        assert should_rotate_free_model(ValueError("huh")) is False
        assert should_rotate_free_model({"role": "assistant", "content": "plain"}) is False

    def test_trigger_code_extraction(self):
        from core.free_rotation import rotation_trigger_code

        assert rotation_trigger_code(_status_error(429)) == 429
        assert (
            rotation_trigger_code({"role": "assistant", "content": "[API Error: 503 — down]"})
            == 503
        )
        assert rotation_trigger_code(ValueError("x")) is None


# ── priority order ─────────────────────────────────────────────────────


class TestPriority:
    def test_priority_order_is_best_free_first(self):
        from core.free_rotation import FREE_MODEL_PRIORITY

        # 10.8.8: Dylan's own model is the default (first); Cline is LAST
        # (free credits ending); the cloud free legs sit between.
        assert [p for p, _ in FREE_MODEL_PRIORITY] == [
            "ollama",
            "openrouter",
            "openrouter",
            "gemini",
            "groq",
            "cline-usage",
        ]
        assert FREE_MODEL_PRIORITY[0] == ("ollama", "luckyd:8b")
        assert FREE_MODEL_PRIORITY[-1] == ("cline-usage", "minimax/minimax-m2.5")

    def test_gemini_is_a_free_tier_provider(self):
        from core.providers import FREE_TIER_PROVIDERS

        assert "gemini" in FREE_TIER_PROVIDERS


# ── rotator ────────────────────────────────────────────────────────────


class TestRotator:
    def test_rotate_walks_priority(self, free_env):
        from core.free_rotation import FreeModelRotator

        free_env.setenv("GOOGLE_API_KEY", TEST_API_KEY)
        rot = FreeModelRotator()
        assert rot.rotate() == ("gemini", "gemini-2.5-flash")
        assert rot.current == ("gemini", "gemini-2.5-flash")

    def test_cline_session_is_the_free_option_when_others_absent(self, free_env, monkeypatch):
        from core import providers
        from core.free_rotation import FreeModelRotator

        monkeypatch.setattr(providers, "cline_session_token", lambda: "tok-live")
        rot = FreeModelRotator()
        assert rot.rotate() == ("cline-usage", "minimax/minimax-m2.5")

    def test_mark_failed_skips_pair(self, free_env, monkeypatch):
        from core import providers
        from core.free_rotation import FreeModelRotator

        monkeypatch.setattr(providers, "cline_session_token", lambda: "tok-live")
        free_env.setenv("GOOGLE_API_KEY", TEST_API_KEY)
        rot = FreeModelRotator()
        rot.mark_failed("cline-usage", "minimax/minimax-m2.5")
        assert rot.rotate() == ("gemini", "gemini-2.5-flash")

    def test_mark_provider_failed_skips_whole_provider(self, free_env, monkeypatch):
        from core import providers
        from core.free_rotation import FreeModelRotator

        monkeypatch.setattr(providers, "cline_session_token", lambda: "tok-live")
        free_env.setenv("GOOGLE_API_KEY", TEST_API_KEY)
        rot = FreeModelRotator()
        rot.mark_provider_failed("cline-usage")
        # Next usable is gemini, then the ollama terminal fallback.
        assert rot.rotate() == ("gemini", "gemini-2.5-flash")
        assert rot.rotate() == ("ollama", "luckyd:8b")

    def test_ollama_is_terminal_fallback_without_network(self, free_env):
        from core.free_rotation import available_free_models

        # Nothing usable (probe refuses) — ollama is still listed last.
        assert available_free_models() == [("ollama", "luckyd:8b")]

    def test_exhausted_rotator_returns_none(self, free_env):
        from core.free_rotation import FreeModelRotator

        rot = FreeModelRotator()
        rot.mark_provider_failed("ollama")
        assert rot.rotate() is None
        assert rot.current is None

    def test_cline_skipped_while_credit_exhausted(self, free_env, monkeypatch):
        from core import providers
        from core.cline_credit import record_cline_credit_exhausted
        from core.free_rotation import FreeModelRotator

        monkeypatch.setattr(providers, "cline_session_token", lambda: "tok-live")
        free_env.setenv("GOOGLE_API_KEY", TEST_API_KEY)
        assert record_cline_credit_exhausted("test 402") is True
        rot = FreeModelRotator()
        assert rot.rotate() == ("gemini", "gemini-2.5-flash")


# ── best_free_provider ─────────────────────────────────────────────────


class TestBestFreeProvider:
    def test_nothing_usable_returns_none(self, free_env):
        from core.free_rotation import best_free_provider

        assert best_free_provider() is None

    def test_gemini_when_keyed(self, free_env):
        from core.free_rotation import best_free_provider

        free_env.setenv("GOOGLE_API_KEY", TEST_API_KEY)
        assert best_free_provider() == "gemini"

    def test_gemini_beats_cline_now(self, free_env, monkeypatch):
        """10.8.8: Cline is last in the rotation (free credits ending) —
        a keyed Gemini beats a live Cline session."""
        from core import providers
        from core.free_rotation import best_free_provider

        monkeypatch.setattr(providers, "cline_session_token", lambda: "tok-live")
        free_env.setenv("GOOGLE_API_KEY", TEST_API_KEY)
        assert best_free_provider() == "gemini"


# ── detect_provider: free-first default ────────────────────────────────


class TestDetectProviderFreeFirst:
    def test_gemini_free_beats_paid_openai(self, free_env):
        from core.providers import detect_provider

        free_env.setenv("OPENAI_API_KEY", TEST_API_KEY)
        free_env.setenv("GOOGLE_API_KEY", TEST_API_KEY)
        assert detect_provider() == "gemini"

    def test_explicit_provider_still_wins(self, free_env):
        from core.providers import detect_provider

        free_env.setenv("CODING_AGENT_PROVIDER", "openai")
        free_env.setenv("GOOGLE_API_KEY", TEST_API_KEY)
        assert detect_provider() == "openai"

    def test_cline_session_first(self, free_env, monkeypatch):
        from core import providers
        from core.providers import detect_provider

        monkeypatch.setattr(providers, "cline_session_token", lambda: "tok-live")
        assert detect_provider() == "cline-usage"

    def test_nothing_usable_returns_none(self, free_env):
        from core.providers import detect_provider

        assert detect_provider() is None

    def test_meta_detected_by_key(self, free_env):
        from core.providers import detect_provider

        free_env.setenv("META_API_KEY", TEST_API_KEY)
        assert detect_provider() == "meta"

    def test_mistral_detected_by_key(self, free_env):
        from core.providers import detect_provider

        free_env.setenv("MISTRAL_API_KEY", TEST_API_KEY)
        assert detect_provider() == "mistral"

    def test_keyed_priority_openai_beats_meta(self, free_env):
        from core.providers import detect_provider

        free_env.setenv("OPENAI_API_KEY", TEST_API_KEY)
        free_env.setenv("META_API_KEY", TEST_API_KEY)
        free_env.setenv("MISTRAL_API_KEY", TEST_API_KEY)
        assert detect_provider() == "openai"

    def test_meta_beats_mistral(self, free_env):
        from core.providers import detect_provider

        free_env.setenv("META_API_KEY", TEST_API_KEY)
        free_env.setenv("MISTRAL_API_KEY", TEST_API_KEY)
        assert detect_provider() == "meta"


# ── resolve_provider_config: ollama terminal fallback ──────────────────


class TestResolveFallback:
    def test_no_provider_configured_falls_back_to_ollama(self, free_env):
        from core.providers import resolve_provider_config

        cfg = resolve_provider_config()
        assert cfg["provider"] == "ollama"
        assert cfg["model"] == "luckyd:8b"

    def test_free_default_picks_gemini(self, free_env):
        from core.providers import resolve_provider_config

        free_env.setenv("GOOGLE_API_KEY", TEST_API_KEY)
        cfg = resolve_provider_config()
        assert cfg["provider"] == "gemini"
        assert cfg["api_key"] == TEST_API_KEY


# ── agent: 403 walks the free priority ─────────────────────────────────


def _make_agent(**kwargs):
    with patch("llm.ProviderRouter"):
        from core.agent_loop import CodingAgent

        ag = CodingAgent(api_key=TEST_API_KEY, model="test-model", **kwargs)
        ag._extract_session_memories = AsyncMock()
        ag._result_handler = None
        return ag


def _cline_cfg():
    return SimpleNamespace(
        api_key="tok",
        base_url="https://api.cline.bot/api/v1",
        model="minimax/minimax-m2.5",
        temperature=0.0,
        max_tokens=8192,
        provider="cline-usage",
        thinking=False,
    )


class TestAgentRotate403:
    async def test_403_rotates_to_cline_free_tier(self, free_env, monkeypatch):
        from core import providers
        from core.agent_loop import CodingAgent

        monkeypatch.setattr(providers, "cline_session_token", lambda: "tok-live")
        ag = _make_agent()
        ag._provider_config.provider = "openai"
        ag.model = "gpt-4o"

        async def _chat_stream(*_a, **_k):
            if ag.model == "minimax/minimax-m2.5":
                return dict(_GOOD)
            return dict(_ERR_403)

        with (
            patch.object(CodingAgent, "_cline_gateway_usable", return_value=True),
            patch.object(CodingAgent, "_ollama_reachable", return_value=False),
            patch("core.providers.build_llm_config", return_value=_cline_cfg()),
            patch("core.llm_client.LLMClient.chat_stream", new=_chat_stream),
        ):
            msg = await ag._auto_rotate_model(dict(_ERR_403), [], None)

        assert msg is not None and msg["content"] == "OK"
        assert ag._provider_config.provider == "cline-usage"
        assert ag.model == "minimax/minimax-m2.5"

    async def test_403_with_nothing_usable_restores_original(self, free_env):
        from core.agent_loop import CodingAgent

        ag = _make_agent()
        ag._provider_config.provider = "openai"
        ag.model = "gpt-4o"

        def _must_not_build(*a, **k):
            raise AssertionError("no provider switch should be attempted")

        with (
            patch.object(CodingAgent, "_cline_gateway_usable", return_value=False),
            patch.object(CodingAgent, "_ollama_reachable", return_value=False),
            patch("core.providers.build_llm_config", side_effect=_must_not_build),
        ):
            msg = await ag._auto_rotate_model(dict(_ERR_403), [], None)

        assert msg is None
        assert ag._provider_config.provider == "openai"
        assert ag.model == "gpt-4o"

    async def test_403_skips_dead_provider_entirely(self, free_env, monkeypatch):
        """The 403'd provider is not retried with another model — the rotator
        moves straight to the next free provider (Ollama here)."""
        from core.agent_loop import CodingAgent

        ag = _make_agent()
        ag._provider_config.provider = "openai"
        ag.model = "gpt-4o"
        tried_models = []

        async def _chat_stream(*_a, **_k):
            tried_models.append(ag.model)
            if ag.model == "luckyd:8b":
                return dict(_GOOD)
            return dict(_ERR_403)

        ollama_cfg = SimpleNamespace(
            api_key="",
            base_url="http://127.0.0.1:11434/v1",
            model="luckyd:8b",
            temperature=0.0,
            max_tokens=8192,
            provider="ollama",
            thinking=False,
        )
        with (
            patch.object(CodingAgent, "_cline_gateway_usable", return_value=False),
            patch.object(CodingAgent, "_ollama_reachable", return_value=True),
            patch("core.providers.build_llm_config", return_value=ollama_cfg),
            patch("core.llm_client.LLMClient.chat_stream", new=_chat_stream),
        ):
            msg = await ag._auto_rotate_model(dict(_ERR_403), [], None)

        assert msg is not None and msg["content"] == "OK"
        assert ag._provider_config.provider == "ollama"
        # Only the ollama model was attempted — openai was never retried.
        assert tried_models == ["luckyd:8b"]


# ── ai_bridge.default_provider: free priority order ────────────────────


def _bridge_state(monkeypatch, env, local, cline_token: str | None = None):
    from browser_core import ai_bridge, cline_session
    from browser_core.ai_bridge import AIBridge

    state = {"env": dict(env), "local": dict(local)}
    monkeypatch.setattr(ai_bridge, "_load_env", lambda: dict(state["env"]))
    monkeypatch.setattr(AIBridge, "_detect_local", staticmethod(lambda env: dict(state["local"])))
    if cline_token is not None:
        monkeypatch.setattr(cline_session, "has_session", lambda: True)
        monkeypatch.setattr(cline_session, "stored_token", lambda: cline_token)
    else:
        monkeypatch.setattr(cline_session, "has_session", lambda: False)
    monkeypatch.setattr(ai_bridge, "_route_task", None)
    return AIBridge()


def _local_entry(model="llama3.2:3b"):
    return (model, "http://127.0.0.1:11434/v1", "", "openai")


class TestBridgeDefaultOrder:
    def test_local_luckyd_beats_cline(self, monkeypatch):
        """10.8.8: the default is Dylan's own model — local Ollama beats a
        live Cline session now (Cline is last in the free rotation)."""
        bridge = _bridge_state(
            monkeypatch,
            {"OPENAI_API_KEY": "k"},
            {"ollama": _local_entry()},
            cline_token="sess-token",
        )
        assert bridge.default_provider() == "ollama"

    def test_local_luckyd_beats_gemini_and_paid(self, monkeypatch):
        """10.8.8: local Ollama beats keyed Gemini and paid OpenAI."""
        bridge = _bridge_state(
            monkeypatch,
            {"GOOGLE_API_KEY": "k", "OPENAI_API_KEY": "k2"},
            {"ollama": _local_entry()},
        )
        assert bridge.default_provider() == "ollama"

    def test_local_beats_paid(self, monkeypatch):
        bridge = _bridge_state(monkeypatch, {"OPENAI_API_KEY": "k"}, {"ollama": _local_entry()})
        assert bridge.default_provider() == "ollama"

    def test_openrouter_before_groq(self, monkeypatch):
        bridge = _bridge_state(monkeypatch, {"GROQ_API_KEY": "k", "OPENROUTER_API_KEY": "k2"}, {})
        assert bridge.default_provider() == "openrouter"

    def test_no_free_provider_means_no_default(self, monkeypatch):
        """10.8.8: paid models are NEVER auto-picked as default — with only
        a paid key configured, Auto has no default and says so plainly
        instead of silently billing."""
        bridge = _bridge_state(monkeypatch, {"OPENAI_API_KEY": "k"}, {})
        assert bridge.default_provider() is None
        assert "no free provider" in bridge.free_fallback_note()

    def test_luckyd_offline_falls_back_to_openrouter(self, monkeypatch):
        """Steer 2: when Ollama is unreachable, Auto falls back to the
        next free leg (OpenRouter) instead of failing or picking Cline."""
        bridge = _bridge_state(monkeypatch, {"OPENROUTER_API_KEY": "k"}, {})
        assert bridge.default_provider() == "openrouter"
        assert "LuckyD (offline)" in bridge.free_fallback_note()


# ── live-detected priority (10.8.8) ─────────────────────────────────────


def _write_detection_cache(path, pairs, live_benchmarks=True):
    """A fake detection cache file (same shape as core.free_detection
    writes), fresh as of now."""
    import json
    import time

    data = {
        "fetched_at_ts": time.time(),
        "fetched_at": "2026-11-10T00:00:00",
        "pairs": [[p, m] for p, m in pairs],
        "opencode_app_free": ["opencode/deepseek-v4.1-flash-free"],
        "antigravity_free": ["gemini-3.8-flash"],
        "report": "cached detection (test)",
        "sources": ["openrouter", "rankllms"],
        "live_benchmarks": live_benchmarks,
    }
    path.write_text(json.dumps(data), encoding="utf-8")


class TestEffectiveFreePriority:
    def test_cached_detection_overrides_static_order(self, free_env, tmp_path, monkeypatch):
        from core.free_rotation import effective_free_priority

        # A detected order that differs from the static one (gemini ahead
        # of openrouter) — the cache must win when fresh.
        cache = tmp_path / "free_models_cache.json"
        _write_detection_cache(
            cache,
            [
                ("ollama", "luckyd:8b"),
                ("gemini", "gemini-2.5-flash"),
                ("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free"),
                ("groq", "compound-mini"),
                ("cline-usage", "minimax/minimax-m2.5"),
            ],
        )
        monkeypatch.setenv("LUCKYD_FREE_MODELS_CACHE", str(cache))
        order = effective_free_priority()
        assert order[1] == ("gemini", "gemini-2.5-flash")
        assert order[2] == ("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free")

    def test_no_cache_falls_back_to_static_priority(self, free_env, tmp_path, monkeypatch):
        from core.free_rotation import FREE_MODEL_PRIORITY, effective_free_priority

        monkeypatch.setenv("LUCKYD_FREE_MODELS_CACHE", str(tmp_path / "missing.json"))
        assert effective_free_priority() == list(FREE_MODEL_PRIORITY)

    def test_stale_cache_falls_back_to_static_priority(self, free_env, tmp_path, monkeypatch):
        """A cache older than the 24h TTL is ignored, not trusted."""
        import json
        import time

        from core.free_rotation import FREE_MODEL_PRIORITY, effective_free_priority

        cache = tmp_path / "free_models_cache.json"
        _write_detection_cache(cache, [("gemini", "gemini-2.5-flash")])
        data = json.loads(cache.read_text(encoding="utf-8"))
        data["fetched_at_ts"] = time.time() - 25 * 3600
        cache.write_text(json.dumps(data), encoding="utf-8")
        monkeypatch.setenv("LUCKYD_FREE_MODELS_CACHE", str(cache))
        assert effective_free_priority() == list(FREE_MODEL_PRIORITY)

    def test_rotator_snapshots_priority_at_construction(self, free_env, tmp_path, monkeypatch):
        """A mid-conversation cache refresh never swaps the active pick:
        the rotator keeps the list it was built with."""
        from core.free_rotation import FreeModelRotator

        cache = tmp_path / "free_models_cache.json"
        _write_detection_cache(
            cache,
            [
                ("ollama", "luckyd:8b"),
                ("gemini", "gemini-2.5-flash"),
                ("cline-usage", "minimax/minimax-m2.5"),
            ],
        )
        monkeypatch.setenv("LUCKYD_FREE_MODELS_CACHE", str(cache))
        free_env.setenv("GOOGLE_API_KEY", TEST_API_KEY)
        rot = FreeModelRotator()
        # Ollama probe refuses in free_env, so the first usable pair is gemini.
        assert rot.rotate() == ("gemini", "gemini-2.5-flash")
        # The cache now changes (a re-probe re-ranked the cloud legs) —
        # the live rotator must not notice until a fresh rotator is built.
        _write_detection_cache(
            cache,
            [
                ("ollama", "luckyd:8b"),
                ("openrouter", "openrouter/free"),
                ("gemini", "gemini-2.5-flash"),
            ],
        )
        rot.mark_failed("gemini", "gemini-2.5-flash")
        assert rot.rotate() == ("ollama", "luckyd:8b")  # terminal fallback, old list
