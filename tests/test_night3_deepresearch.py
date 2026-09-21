"""Night-3 tests: features/deep_research (config + schemas).

Covers Settings role routing, temperature routing, search-backend
auto-resolution, env refresh, and the pydantic schema contracts
(validation bounds, required fields, defaults).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from features.deep_research.config import Settings
from features.deep_research.schemas import (
    CoverageAssessment,
    EvidenceCard,
    ResearchPlan,
    ResearchTask,
    SourceDocument,
)

# ── Settings ───────────────────────────────────────────────────────────


def test_model_for_roles():
    s = Settings(model_worker="w", model_planner="p", model_synthesizer="s", model_critic="c")
    assert s.model_for("worker") == "w"
    assert s.model_for("planner") == "p"
    assert s.model_for("synthesizer") == "s"
    assert s.model_for("critic") == "c"
    assert s.model_for("unknown-role") == "w"  # falls back to worker


def test_temp_for_roles():
    s = Settings(temp_reasoning=0.7, temp_extract=0.1)
    assert s.temp_for("planner") == 0.7
    assert s.temp_for("synthesizer") == 0.7
    assert s.temp_for("worker") == 0.1
    assert s.temp_for("critic") == 0.1


def test_effective_search_backend_explicit():
    s = Settings(search_backend="ddg")
    assert s.effective_search_backend("gemini") == "ddg"
    s = Settings(search_backend="none")
    assert s.effective_search_backend() == "none"


def test_effective_search_backend_mock_stays_offline():
    s = Settings(search_backend="auto")
    assert s.effective_search_backend("mock") == "gemini"  # mock's grounded path


def test_effective_search_backend_gemini_with_key():
    s = Settings(search_backend="auto", api_key="k")
    assert s.effective_search_backend("gemini") == "gemini"
    s = Settings(search_backend="auto", api_key=None)
    assert s.effective_search_backend("gemini") == "ddg"


def test_effective_search_backend_default_ddg():
    s = Settings(search_backend="auto", api_key="k")
    assert s.effective_search_backend("cline-usage") == "ddg"
    assert s.effective_search_backend("") == "ddg"


def test_settings_defaults_frozen_at_import_refresh_re_reads(monkeypatch):
    # Dataclass field defaults are evaluated at class-definition (import)
    # time, so Settings() ignores later env changes — refresh() is the
    # sanctioned re-read path (used by the LuckyD tool per run).
    monkeypatch.setenv("DRS_PROVIDER", "MOCK")
    monkeypatch.setenv("DRS_MAX_ITERATIONS", "5")
    s = Settings()
    assert s.provider != "mock"  # frozen at import; env change ignored
    assert s.max_iterations != 5
    s.refresh()
    assert s.provider == "mock"
    # max_iterations is not re-read by refresh (only provider/backend/key).
    assert s.max_iterations != 5


def test_settings_refresh_picks_up_env(monkeypatch):
    s = Settings(provider="auto", search_backend="auto", api_key=None)
    monkeypatch.setenv("DRS_PROVIDER", "ollama")
    monkeypatch.setenv("DRS_SEARCH_BACKEND", "none")
    monkeypatch.setenv("GOOGLE_API_KEY", "new-key")
    s.refresh()
    assert s.provider == "ollama"
    assert s.search_backend == "none"
    assert s.api_key == "new-key"


def test_settings_refresh_keeps_values_without_env(monkeypatch):
    s = Settings(provider="gemini", search_backend="ddg", api_key="old")
    monkeypatch.delenv("DRS_PROVIDER", raising=False)
    monkeypatch.delenv("DRS_SEARCH_BACKEND", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    s.refresh()
    assert s.provider == "gemini"
    assert s.search_backend == "ddg"
    assert s.api_key == "old"


# ── Schemas ────────────────────────────────────────────────────────────


def test_research_task_priority_bounds():
    t = ResearchTask(id="t1", question="q", focus="f", priority=3)
    assert t.priority == 3
    with pytest.raises(ValidationError):
        ResearchTask(id="t1", question="q", focus="f", priority=0)
    with pytest.raises(ValidationError):
        ResearchTask(id="t1", question="q", focus="f", priority=6)


def test_research_plan_requires_tasks():
    with pytest.raises(ValidationError):
        ResearchPlan(reasoning="r", tasks=[])
    p = ResearchPlan(reasoning="r", tasks=[ResearchTask(id="t1", question="q", focus="f")])
    assert len(p.tasks) == 1


def test_evidence_card_score_bounds_and_defaults():
    c = EvidenceCard(id="e1", url="https://x.test", source_score=0.9, confidence=0.2)
    assert c.source_score == 0.9 and c.confidence == 0.2
    assert c.retrieved_at  # default factory fills in
    with pytest.raises(ValidationError):
        EvidenceCard(id="e1", url="https://x.test", source_score=1.5)
    with pytest.raises(ValidationError):
        EvidenceCard(id="e1", url="https://x.test", confidence=-0.1)
    with pytest.raises(ValidationError):
        EvidenceCard(id="e1", url="https://x.test", round=0)


def test_source_document_defaults():
    d = SourceDocument(url="https://x.test")
    assert d.fetch_ok is True and d.text == "" and d.error == ""


def test_coverage_assessment_bounds():
    a = CoverageAssessment(coverage_score=0.9, gaps=["g1"], is_sufficient=True)
    assert a.is_sufficient
    with pytest.raises(ValidationError):
        CoverageAssessment(coverage_score=2.0)


def test_evidence_card_serializes():
    c = EvidenceCard(id="e1", url="https://x.test", title="T", quote="Q")
    d = c.model_dump()
    assert d["id"] == "e1" and d["quote"] == "Q"
