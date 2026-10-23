"""Browser sidebar catalog audit (10.8.3 follow-up): the bridge's own
Cline catalog gets the same prune the terminal catalog got.

``browser/browser_core/ai_bridge.py`` keeps its own copy of the Cline
usage catalog (it backs the AI sidebar / web model picker fallback, the
bridge default model, and the gateway rotation pool). The 2026-10-22
live-probe audit behind ``tests/test_terminal_model_catalog.py`` proved
four "free" ids dead on the gateway — upstream 404 model_not_found
(qwen3-8b deprecated upstream 2026-10-09) — and the bridge still offered
all four, defaulted to one of them, and led its rotation pool with one.

These tests pin the audited state so the dead ids cannot creep back into
the bridge catalog, its default, its rotation pools, or the sidebar's
free-tier labelling, and so the bridge and terminal free tiers — meant
to stay in sync — cannot drift apart again.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

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


class TestBridgeCatalogPrune:
    def test_bridge_catalog_is_exactly_the_audited_survivors(self):
        from browser_core import ai_bridge

        assert set(ai_bridge._CLINE_USAGE_CATALOG) == CLINE_FREE_SURVIVORS

    def test_bridge_free_tier_is_explicit_and_matches_catalog(self):
        """The bridge free tier is an explicit set (never a positional
        slice) and — the bridge catalog being free-only since 10.7.5 —
        it is exactly the catalog."""
        from browser_core import ai_bridge

        assert isinstance(ai_bridge._CLINE_USAGE_FREE_TIER, frozenset)
        assert set(ai_bridge._CLINE_USAGE_FREE_TIER) == CLINE_FREE_SURVIVORS
        assert set(ai_bridge._CLINE_USAGE_FREE_TIER) == set(ai_bridge._CLINE_USAGE_CATALOG)

    def test_dead_ids_absent_from_every_bridge_surface(self):
        from browser_core import ai_bridge

        assert not (CLINE_FREE_DEAD & set(ai_bridge._CLINE_USAGE_CATALOG))
        assert not (CLINE_FREE_DEAD & set(ai_bridge._CLINE_USAGE_FREE_TIER))
        assert not (CLINE_FREE_DEAD & set(ai_bridge._CLINE_GATEWAY_TOP_MODELS))
        assert not (CLINE_FREE_DEAD & set(ai_bridge._CLINE_GATEWAY_CATALOG))

    def test_credit_billed_ids_never_leak_into_bridge_free_tier(self):
        from browser_core import ai_bridge

        assert not (CLINE_CREDIT_BILLED & set(ai_bridge._CLINE_USAGE_FREE_TIER))
        assert not (CLINE_CREDIT_BILLED & set(ai_bridge._CLINE_USAGE_CATALOG))

    def test_bridge_and_terminal_free_tiers_cannot_drift(self):
        from browser_core import ai_bridge

        import main

        assert set(ai_bridge._CLINE_USAGE_FREE_TIER) == set(main._CLINE_USAGE_FREE_TIER)
        assert set(ai_bridge._CLINE_USAGE_CATALOG) == set(main._CLINE_USAGE_FREE_TIER)


class TestBridgeDefaultsAndPools:
    def test_bridge_default_model_is_the_audited_lead(self):
        from browser_core import ai_bridge

        assert ai_bridge._CLINE_USAGE_MODEL == "minimax/minimax-m2.5"
        assert ai_bridge._CLINE_USAGE_MODEL in ai_bridge._CLINE_USAGE_FREE_TIER
        assert ai_bridge._CLINE_USAGE_MODEL not in CLINE_FREE_DEAD

    def test_gateway_pool_leads_with_minimax(self):
        from browser_core import ai_bridge

        assert ai_bridge._CLINE_GATEWAY_TOP_MODELS[0] == "minimax/minimax-m2.5"
        # The pool's usage-billed (free-tier) members come from the pruned
        # catalog. (cline-pass/claude-sonnet-4 is a live-list-only rotation
        # member — the gateway serves it, the static catalog never listed
        # it; that predates the audit and is unchanged by it.)
        usage_members = {
            m for m in ai_bridge._CLINE_GATEWAY_TOP_MODELS if not m.startswith("cline-pass/")
        }
        assert usage_members <= set(ai_bridge._CLINE_USAGE_CATALOG)

    def test_core_provider_default_is_alive(self):
        from core.providers import PROVIDER_DEFAULTS

        default = PROVIDER_DEFAULTS["cline-usage"]["default_model"]
        assert default == "minimax/minimax-m2.5"
        assert default not in CLINE_FREE_DEAD

    def test_agent_loop_pool_has_no_dead_ids_and_leads_with_minimax(self):
        from core.agent_loop import CodingAgent

        pool = CodingAgent._CLINE_ROTATION_POOL
        assert pool[0] == "minimax/minimax-m2.5"
        assert not (CLINE_FREE_DEAD & set(pool))

    def test_router_cline_free_entry_points_at_a_survivor(self):
        from core.router import MODEL_CATALOG

        entry = MODEL_CATALOG["cline-free"]
        assert entry.model_id == "minimax/minimax-m2.5"
        assert entry.model_id in CLINE_FREE_SURVIVORS


class TestSidebarLabelling:
    def test_sidebar_uses_the_bridge_free_tier_as_single_source(self):
        """The picker's "· free tier" label must come from the bridge's
        audited set — the sidebar's old hardcoded tuple had drifted to
        two dead ids plus a model that was never offered. Source-level
        pin (the suite's pattern for sidebar wiring; see
        test_skills_wiring.py) plus a dead-id sweep of the whole file."""
        source = (_REPO_ROOT / "browser" / "browser_ui" / "ai_sidebar.py").read_text(
            encoding="utf-8"
        )
        assert "_CLINE_USAGE_FREE_TIER" in source
        for dead in CLINE_FREE_DEAD:
            assert dead not in source
