"""Terminal /model catalog audit (10.8.3): only models that actually work.

Dylan, 2026-10-22: in the terminal's model selection the Ollama options
should be his own luckyd models "instead of the other ollama options", and
"some of them don't even work for free on there". Ground truth that night:

- ``ollama list`` on his PC = exactly luckyd:4b, luckyd:8b, luckyd:latest.
  Every entry the picker used to offer for Ollama (llama3.2:3b, gemma3:4b,
  phi3:mini, mistral:7b, codellama:7b) was a dead pick — none installed.
- Live Cline-gateway probes with his real session: four cline-usage
  "free" ids (deepseek/deepseek-chat, meta-llama/llama-3.2-3b-instruct,
  qwen/qwen3-8b, kwaipilot/kat-coder-pro) map to upstream ids that return
  404 "model not found" (qwen3-8b deprecated upstream 2026-10-09), so
  they could never work at $0. The six survivors streamed real chunks.

These tests pin the audited catalogs so the dead ids — and the generic
Ollama list — cannot creep back, and so the free tier can never again be
a positional slice that promotes credit-billed ids into "free".
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

LUCKYD_MODELS = {"luckyd:4b", "luckyd:8b", "luckyd:latest"}
GENERIC_OLLAMA = {"llama3.2:3b", "gemma3:4b", "phi3:mini", "mistral:7b", "codellama:7b"}

CLINE_FREE_SURVIVORS = {
    "minimax/minimax-m2.5",
    "deepseek/deepseek-r1",
    "google/gemini-2.5-flash",
    "z-ai/glm-5",
    "z-ai/glm-5.3-flash",
    "poolside/laguna-s-2.1:free",
}
# Proven dead on the gateway 2026-10-22 (upstream 404 model_not_found).
CLINE_FREE_DEAD = {
    "deepseek/deepseek-chat",
    "meta-llama/llama-3.2-3b-instruct",
    "qwen/qwen3-8b",
    "kwaipilot/kat-coder-pro",
}
CLINE_CREDIT_BILLED = {
    "google/gemini-2.5-pro",
    "anthropic/claude-sonnet-4-6",
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "mistral/mistral-large",
}

_PROVIDER_ENV_VARS = (
    "CODING_AGENT_PROVIDER",
    "CODING_AGENT_API_KEY",
    "CLINEPASS_API_KEY",
    "CLINE_USAGE_MODEL",
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
def clean_env(monkeypatch):
    from core import providers

    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    providers.reset_cline_session_cache()
    monkeypatch.setattr(providers, "cline_session_token", lambda: "")

    def _refuse(*a, **k):
        raise ConnectionError("no network in catalog tests")

    monkeypatch.setattr(httpx, "get", _refuse)
    return monkeypatch


def _config() -> dict:
    return json.loads(
        (_REPO_ROOT / "browser" / "models" / "providers_config.json").read_text(
            encoding="utf-8-sig"
        )
    )


def _free_groups() -> list[dict]:
    import main

    sections = main.model_catalog()
    free = next(s for s in sections if s["tier"] == "free")
    return free["groups"]


def _group(groups: list[dict], key: str) -> dict:
    return next(g for g in groups if g.get("provider_key") == key)


class TestOllamaGroupIsHisModels:
    def test_config_ollama_free_models_are_exactly_the_luckyd_set(self):
        ollama = _config()["ai_providers"]["ollama"]
        assert set(ollama["free_models"]) == LUCKYD_MODELS
        assert ollama["recommended"] == "luckyd:8b"

    def test_picker_ollama_group_is_exactly_the_luckyd_set(self, clean_env):
        group = _group(_free_groups(), "ollama")
        assert set(group["models"]) == LUCKYD_MODELS

    def test_no_generic_ollama_models_anywhere_in_free_picker(self, clean_env):
        for group in _free_groups():
            assert not (set(group["models"]) & GENERIC_OLLAMA), group


class TestClineFreeAudit:
    def test_config_cline_free_models_are_the_audited_survivors(self):
        models = _config()["ai_providers"]["cline-usage"]["free_models"]
        assert set(models) == CLINE_FREE_SURVIVORS

    def test_main_free_tier_is_the_audited_survivors(self):
        import main

        assert set(main._CLINE_USAGE_FREE_TIER) == CLINE_FREE_SURVIVORS

    def test_config_and_main_free_lists_cannot_drift(self):
        import main

        config_models = set(_config()["ai_providers"]["cline-usage"]["free_models"])
        assert config_models == set(main._CLINE_USAGE_FREE_TIER)

    def test_dead_ids_are_gone_from_catalog_and_picker(self, clean_env):
        import main

        assert not (CLINE_FREE_DEAD & set(main._CLINE_USAGE_CATALOG))
        assert not (CLINE_FREE_DEAD & set(main._CLINE_USAGE_FREE_TIER))
        for group in _free_groups():
            assert not (set(group["models"]) & CLINE_FREE_DEAD), group

    def test_credit_billed_ids_never_leak_into_the_free_tier(self):
        """Regression for the old ``_CLINE_USAGE_CATALOG[:10]`` slice: once
        free entries are removed, a positional slice promotes paid ids."""
        import main

        assert not (CLINE_CREDIT_BILLED & set(main._CLINE_USAGE_FREE_TIER))
        free = [m for m in main._CLINE_USAGE_CATALOG if m in main._CLINE_USAGE_FREE_TIER]
        paid = [m for m in main._CLINE_USAGE_CATALOG if m not in main._CLINE_USAGE_FREE_TIER]
        assert set(free) == CLINE_FREE_SURVIVORS
        assert set(paid) == CLINE_CREDIT_BILLED


class TestDefaultsPointAtInstalledModels:
    def test_ollama_provider_default_is_luckyd_8b(self):
        from core.providers import PROVIDER_DEFAULTS

        assert PROVIDER_DEFAULTS["ollama"]["default_model"] == "luckyd:8b"

    def test_free_rotation_lead_and_fallback_pairs(self, clean_env):
        from core.free_rotation import FREE_MODEL_PRIORITY, available_free_models

        assert FREE_MODEL_PRIORITY[0] == ("cline-usage", "minimax/minimax-m2.5")
        assert ("ollama", "luckyd:8b") in FREE_MODEL_PRIORITY
        assert not any(m == "llama3.2:3b" for _p, m in FREE_MODEL_PRIORITY)
        assert not any(m == "deepseek/deepseek-chat" for _p, m in FREE_MODEL_PRIORITY)
        # With nothing else usable, the terminal fallback is the luckyd pair.
        assert available_free_models()[-1] == ("ollama", "luckyd:8b")

    def test_router_default_is_luckyd_8b(self):
        from core.router import MODEL_CATALOG, ModelRouter

        assert ModelRouter().default_model_id == "luckyd:8b"
        assert MODEL_CATALOG["ollama-local"].model_id == "luckyd:8b"
