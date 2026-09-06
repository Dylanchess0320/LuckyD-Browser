"""A fake LLM provider for offline / keyless smoke tests (`--dry-run`).

Returns deterministic, schema-valid outputs so the entire graph can be
exercised end-to-end without a Gemini key. Not for real research.
"""

from __future__ import annotations

from typing import TypeVar

from ..schemas import (
    Critique,
    EvidenceCard,
    ReportSection,
    ResearchPlan,
    ResearchReport,
    ResearchTask,
    WorkerResult,
)
from ..tools.search_base import LLMProvider

T = TypeVar("T")


class MockProvider(LLMProvider):
    _is_mock = True
    provider_name = "mock"

    def structured(self, role, system, user, schema, temperature=None):  # type: ignore[override]
        return self._sample(schema, user)

    def text(self, role, system, user, temperature=None) -> str:  # type: ignore[override]
        return f"[mock {role}] synthesized answer for: {user[:80]}"

    def grounded(self, role, system, user) -> tuple[str, list[EvidenceCard]]:  # type: ignore[override]
        ev = [
            EvidenceCard(
                id="e0",
                url="https://en.wikipedia.org/wiki/Example",
                title="Example - Wikipedia",
                snippet="A sample grounded snippet.",
                quote="A sample grounded snippet from a mock source.",
                passage="A sample grounded snippet from a mock source.",
                source_query=user[:80],
                worker_id=role,
                confidence=0.7,
                source_score=0.9,
            ),
            EvidenceCard(
                id="e1",
                url="https://news.example.com/article",
                title="Example News Article",
                snippet="Another sample source.",
                quote="Another sample source stating a mock fact.",
                passage="Another sample source stating a mock fact.",
                source_query=user[:80],
                worker_id=role,
                confidence=0.6,
                source_score=0.6,
            ),
        ]
        return f"Based on sources, the answer to '{user[:80]}' is ...", ev

    def _sample(self, schema, user: str):
        if schema is ResearchPlan:
            return ResearchPlan(
                reasoning="Mock decomposition for smoke test.",
                tasks=[
                    ResearchTask(id="t1", question=user[:120], focus="overview"),
                    ResearchTask(
                        id="t2",
                        question="Recent developments",
                        focus="news",
                        search_hints=["recent news"],
                    ),
                ],
            )
        if schema is WorkerResult:
            return WorkerResult(
                task_id="t1",
                worker_id="mock-worker",
                findings="Mock grounded findings [e0].",
                evidence=[
                    EvidenceCard(
                        id="e0",
                        url="https://example.com/a",
                        title="A",
                        quote="A mock quote.",
                        passage="A mock quote.",
                        worker_id="mock",
                    ),
                ],
                gaps=["mock gap"],
                coverage_score=0.9,
                stop_reason="coverage_threshold",
                rounds_run=1,
            )
        if schema is ResearchReport:
            return ResearchReport(
                title="Mock Report",
                summary="A mock synthesized summary.",
                sections=[
                    ReportSection(heading="Overview", content="Mock body [e0].", citations=["e0"]),
                ],
                key_findings=["mock finding"],
                open_questions=["mock question"],
                evidence_ids_used=["e0"],
            )
        if schema is Critique:
            return Critique(
                status="pass",
                issues=[],
                overall_confidence=0.9,
            )
        raise ValueError(f"Mock has no sample for {schema}")
