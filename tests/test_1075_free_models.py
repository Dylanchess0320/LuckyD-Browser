"""LuckyD 10.7.5 free-model cleanup: dead ids pruned, free-on-any-setting.

Pins the 10.7.5 guarantees:
- the free rotation only names models that exist in the free catalogs,
- retired/mislabeled ids never reappear in the wired catalogs,
- a stale paid browser pick with no key cannot strand LuckyD Code,
- auto task routing defaults to a $0 model,
- the browser bridge defaults are free-catalog models.

Hermetic like test_free_rotation.py: provider env is stripped per-test and
only fake keys are used.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))

TEST_API_KEY = "test-key-not-a-secret"

# Ids retired or mislabeled before 10.7.5 — none may be offered again.
RETIRED_IDS = (
    "deepseek/deepseek-chat-v3.1:free",
    "deepseek-coder",
    "deepseek-lite",
    "google/gemini-2.0-flash",
    "stealth/ox-alpha",
    "llama-3.3-70b-versatile",
    "codellama:34b",
    "claude-opus-4-6",
    "gemini-2.5-pro-preview",
)

_PROVIDER_ENV_VARS = (
    "CODING_AGENT_PROVIDER",
    "CODING_AGENT_API_KEY",
    "CLINEPASS_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "ZAI_API_KEY",
    "OPENROUTER_API_KEY",
    "GROQ_API_KEY",
    "OPENCODE_API_KEY",
    "DEEPSEEK_API_KEY",
    "META_API_KEY",
    "MISTRAL_API_KEY",
    "MINIMAX_API_KEY",
    "OLLAMA_HOST",
    "OLLAMA_MODEL",
)


@pytest.fixture
def free_env(monkeypatch):
    from core import providers

    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    providers.reset_cline_session_cache()
    monkeypatch.setattr(providers, "cline_session_token", lambda: "")

    def _refuse(*a, **k):
        raise ConnectionError("no network in 10.7.5 tests")

    monkeypatch.setattr(httpx, "get", _refuse)
    return monkeypatch


def _catalog() -> dict:
    return json.loads(
        (_REPO_ROOT / "browser" / "models" / "providers_config.json").read_text(
            encoding="utf-8-sig"
        )
    )


class TestCatalogHygiene:
    def test_fallback_chain_is_free_or_local_only(self):
        chain = _catalog()["fallback_chain"]
        assert "opencode" not in chain  # keyed/paid since 2026-09
        assert chain[0] == "ollama"

    def test_no_retired_ids_in_shipped_catalog(self):
        blob = json.dumps(_catalog())
        for dead in RETIRED_IDS:
            assert dead not in blob, dead

    def test_deepseek_is_labeled_paid(self):
        deepseek = _catalog()["ai_providers"]["deepseek"]
        assert deepseek["free_tier"] is False
        assert deepseek["free_models"] == []

    def test_clinepass_is_not_flagged_free(self):
        from core.providers import FREE_TIER_PROVIDERS

        assert "clinepass" not in FREE_TIER_PROVIDERS  # $9.99/mo subscription
        assert "cline-usage" in FREE_TIER_PROVIDERS

    def test_openrouter_list_is_all_free(self):
        models = _catalog()["ai_providers"]["openrouter"]["free_models"]
        assert models[0] == "openrouter/free"
        assert all(m == "openrouter/free" or m.endswith(":free") for m in models)


class TestFreeRotationTargets:
    def test_openrouter_leg_uses_real_free_router(self):
        from core.free_rotation import FREE_MODEL_PRIORITY

        pairs = dict(FREE_MODEL_PRIORITY)
        assert pairs["openrouter"] == "openrouter/free"


class TestFreeOnAnySetting:
    def test_stale_paid_browser_pick_falls_back_to_free(self, free_env, monkeypatch):
        """Browser settings say OpenAI, but no key exists on this machine:
        LuckyD Code must land on the free terminal fallback, not OpenAI."""
        from core import providers

        monkeypatch.setattr(
            providers,
            "_assistant_browser_settings",
            lambda: {"ai_provider": "openai", "ai_model_overrides": {}},
        )
        cfg = providers.resolve_provider_config()
        # The guarantee is a free landing spot, not one specific provider:
        # another suite may leak a (fake) Cline session, which is also $0.
        from core.providers import FREE_TIER_PROVIDERS

        assert cfg["provider"] != "openai"
        assert cfg["provider"] == "ollama" or cfg["provider"] in FREE_TIER_PROVIDERS

    def test_keyed_browser_pick_is_still_mirrored(self, free_env, monkeypatch):
        from core import providers

        monkeypatch.setattr(
            providers,
            "_assistant_browser_settings",
            lambda: {"ai_provider": "openai", "ai_model_overrides": {}},
        )
        free_env.setenv("OPENAI_API_KEY", TEST_API_KEY)
        cfg = providers.resolve_provider_config()
        assert cfg["provider"] == "openai"
        assert cfg["api_key"] == TEST_API_KEY


class TestRouterFreeFirst:
    def test_auto_route_picks_free_model(self):
        from core.router import route_task

        decision = route_task("refactor the auth module and fix the tests")
        assert decision.model.cost_per_1k_input == 0
        assert decision.model.cost_per_1k_output == 0
        assert decision.estimated_cost == 0

    def test_router_default_is_local_free(self):
        from core.router import ModelRouter

        assert ModelRouter().default_model_id == "luckyd:8b"


class TestBridgeFreeDefaults:
    def test_provider_spec_defaults_are_free(self):
        from browser_core.ai_bridge import _PROVIDER_SPECS

        defaults = {name: model for name, _k, model, _u, _kind in _PROVIDER_SPECS}
        assert defaults["groq"] == "groq/compound-mini"
        assert defaults["openrouter"] == "openrouter/free"

    def test_cline_usage_catalog_has_no_billed_ids(self):
        from browser_core.ai_bridge import _CLINE_USAGE_CATALOG

        for billed in ("openai/gpt-4o", "anthropic/claude-sonnet-4-6", "google/gemini-2.5-pro"):
            assert billed not in _CLINE_USAGE_CATALOG
