"""Tests for the LuckyD-native Deep Research swarm tool."""

from __future__ import annotations

import pytest


class TestDeepResearchConfig:
    def test_model_defaults_are_real(self):
        from features.deep_research.config import settings

        for model in (
            settings.model_worker,
            settings.model_planner,
            settings.model_synthesizer,
            settings.model_critic,
        ):
            assert "3.8" not in model, f"fictional model default leaked: {model}"
            assert model, "model default must not be empty"

    def test_runs_cache_under_data_dir(self):
        from features.deep_research.config import settings

        assert "deep_research" in settings.runs_dir or "runs" in settings.runs_dir
        assert settings.cache_dir, "cache dir must be set"

    def test_search_backend_auto_resolves(self):
        from features.deep_research.config import settings

        assert settings.effective_search_backend("gemini") in ("gemini", "ddg")
        assert settings.effective_search_backend("luckyd") == "ddg"
        assert settings.effective_search_backend("") == "ddg"


class TestDeepResearchProviders:
    def test_mock_provider_end_to_end(self):
        from features.deep_research.graph import run_swarm_sync

        md = run_swarm_sync("What is a mock test?", dry_run=True)
        assert isinstance(md, str) and len(md) > 100
        assert "Sources" in md or "Mock" in md or "#" in md

    def test_router_dry_run(self):
        from features.deep_research.models.mock import MockProvider
        from features.deep_research.models.router import get_llm

        llm = get_llm(dry_run=True)
        assert isinstance(llm, MockProvider)

    def test_router_mock_provider(self):
        from features.deep_research.models.mock import MockProvider
        from features.deep_research.models.router import get_llm

        llm = get_llm(dry_run=False, provider="mock")
        assert isinstance(llm, MockProvider)

    def test_luckyd_provider_importable(self):
        from features.deep_research.models.luckyd import LuckyDProvider

        p = LuckyDProvider.__new__(LuckyDProvider)
        assert hasattr(p, "structured") and hasattr(p, "grounded")


class TestDeepResearchUtils:
    def test_canonicalize_url(self):
        from features.deep_research.tools.cache import canonicalize_url

        assert canonicalize_url(
            "https://www.Example.com/path/?utm_source=x&id=1"
        ) == canonicalize_url("https://example.com/path?id=1")

    def test_ddg_missing_package_returns_list(self):
        from features.deep_research.tools.search_ddg import DDGSearch

        out = DDGSearch().search("test query that should not crash")
        assert isinstance(out, list)

    def test_source_quality_without_tldextract(self):
        from features.deep_research.tools.source_quality import domain_type_score

        assert domain_type_score("https://en.wikipedia.org/wiki/X") > 0.5
        assert 0.0 <= domain_type_score("https://example.com") <= 1.0


class TestDeepResearchTool:
    def test_tool_registered(self):
        import tools.deep_research_tool  # noqa: F401  (registers on import)
        from tools.registry import registry

        tool = registry.get("DeepResearch")
        assert tool is not None
        assert registry.get("Research") is tool

    def test_tool_schema(self):
        import tools.deep_research_tool  # noqa: F401
        from tools.registry import registry

        tool = registry.get("deepresearch")
        schema = tool.to_openai_schema()
        assert schema["function"]["name"] == "DeepResearch"
        assert "query" in schema["function"]["parameters"]["properties"]

    @pytest.mark.asyncio
    async def test_tool_dry_run(self, tmp_path, monkeypatch):
        import tools.deep_research_tool  # noqa: F401
        from features.deep_research.config import settings as drs_settings
        from tools.registry import registry

        monkeypatch.setattr(drs_settings, "runs_dir", str(tmp_path / "runs"))
        monkeypatch.setattr(drs_settings, "cache_enabled", False)
        monkeypatch.setattr(drs_settings, "max_iterations", 1)
        monkeypatch.setattr(drs_settings, "research_rounds", 1)

        tool = registry.get("DeepResearch")
        out = await tool.execute(query="What is offline testing?", dry_run=True)
        assert not out.error, out.text[:500]
        assert len(out.text) > 50
        assert out.metadata.get("runs_dir")

    @pytest.mark.asyncio
    async def test_tool_depth_preset(self, tmp_path, monkeypatch):
        import tools.deep_research_tool  # noqa: F401
        from features.deep_research.config import settings as drs_settings
        from tools.registry import registry

        monkeypatch.setattr(drs_settings, "runs_dir", str(tmp_path / "runs"))
        monkeypatch.setattr(drs_settings, "cache_enabled", False)

        tool = registry.get("DeepResearch")
        before_rounds = drs_settings.research_rounds
        out = await tool.execute(query="What is depth testing?", dry_run=True, depth="quick")
        assert not out.error, out.text[:500]
        # per-run overrides are restored afterwards
        assert drs_settings.research_rounds == before_rounds

    @pytest.mark.asyncio
    async def test_tool_rejects_bad_params(self):
        import tools.deep_research_tool  # noqa: F401
        from tools.registry import registry

        tool = registry.get("DeepResearch")
        out = await tool.execute(query="x", depth="ultra")
        assert out.error
        out = await tool.execute(query="x", search_backend="yahoo")
        assert out.error
        out = await tool.execute(query="")
        assert out.error


class TestPremiumSearch:
    def test_missing_keys_return_empty(self, monkeypatch):
        from features.deep_research.tools.search_premium import BraveSearch, TavilySearch

        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        monkeypatch.delenv("BRAVE_API_KEY", raising=False)
        assert TavilySearch().search("test") == []
        assert BraveSearch().search("test") == []


class TestBudgetExhaustion:
    """A run that hits its budget must finalize best-effort, never crash."""

    def test_researcher_summarize_fallback(self):
        from features.deep_research.models.mock import MockProvider
        from features.deep_research.runtime.budget import BudgetExhausted
        from features.deep_research.schemas import ResearchTask
        from features.deep_research.workers.researcher import run_one_task

        class TiredMock(MockProvider):
            def __init__(self):
                self._texts = 0

            def text(self, *a, **k):
                self._texts += 1
                if self._texts > 1:
                    raise BudgetExhausted("max_seconds")
                return "follow-up query one\nfollow-up query two"

        task = ResearchTask(id="t1", question="What is X?", focus="overview")
        res = run_one_task(TiredMock(), task, "wid-1")
        assert res.stop_reason == "budget_exhausted"
        assert len(res.evidence) > 0
        assert "budget" in res.findings.lower()

    def test_swarm_finalizes_on_synthesis_budget_hit(self, monkeypatch):
        from features.deep_research import graph as gmod
        from features.deep_research.graph import run_swarm_sync
        from features.deep_research.runtime.budget import BudgetExhausted

        def _boom(*a, **k):
            raise BudgetExhausted("max_seconds")

        monkeypatch.setattr(gmod, "run_synthesizer", _boom)
        md = run_swarm_sync("What is budget testing?", dry_run=True)
        assert isinstance(md, str) and len(md) > 50
        assert "Best-effort" in md

    def test_swarm_finalizes_on_critic_verify_budget_hit(self, monkeypatch):
        from features.deep_research import graph as gmod
        from features.deep_research.graph import run_swarm_sync
        from features.deep_research.runtime.budget import BudgetExhausted

        def _boom(*a, **k):
            raise BudgetExhausted("max_seconds")

        monkeypatch.setattr(gmod, "run_critic", _boom)
        monkeypatch.setattr(gmod, "run_verify", _boom)
        md = run_swarm_sync("What is budget testing?", dry_run=True)
        assert isinstance(md, str) and len(md) > 50
