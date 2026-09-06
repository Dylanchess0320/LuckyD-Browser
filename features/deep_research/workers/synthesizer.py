"""Synthesizer: merge worker findings + evidence into a cited structured report."""

from __future__ import annotations

from ..prompts import SYNTHESIZER_SYSTEM, SYNTHESIZER_USER
from ..runtime.run_store import RunStore
from ..schemas import EvidenceCard, ResearchReport, WorkerResult
from ..tools.search_base import LLMProvider


def _merge_evidence(
    results: list[WorkerResult],
) -> tuple[list[EvidenceCard], list[tuple[WorkerResult, dict[str, str]]]]:
    """Flatten evidence across workers and give each a unique global id.

    Returns (evidence_list, per_result_maps). The id map is built per worker
    result (not globally) so that local ids like 'e0' from different workers do
    not collide and mis-rewrite each other's inline citations.
    """
    merged: list[EvidenceCard] = []
    per_result_maps: list[tuple[WorkerResult, dict[str, str]]] = []
    counter = 0
    for r in results:
        local: dict[str, str] = {}
        for ev in r.evidence:
            new_id = f"e{counter}"
            local[ev.id] = new_id
            counter += 1
            merged.append(ev.model_copy(update={"id": new_id, "worker_id": r.worker_id}))
        per_result_maps.append((r, local))
    return merged, per_result_maps


def _rewrite_findings(per_result_maps: list[tuple[WorkerResult, dict[str, str]]]) -> str:
    parts = []
    for r, local in per_result_maps:
        # rewrite this worker's inline [e0] refs using ONLY its own local map
        findings = r.findings
        for old, new in local.items():
            findings = findings.replace(f"[{old}]", f"[{new}]")
        parts.append(f"### {r.task_id} ({r.worker_id})\n{findings}")
    return "\n\n".join(parts)


def run_synthesizer(
    llm: LLMProvider,
    query: str,
    results: list[WorkerResult],
    store: RunStore,
    previous_critique: str = "",
) -> tuple[ResearchReport, list[EvidenceCard]]:
    evidence, per_result_maps = _merge_evidence(results)
    findings_text = _rewrite_findings(per_result_maps)
    # Include the extracted quote/passage so the synthesizer writes text-backed claims.
    ev_text = "\n".join(
        f"- {e.id}: {e.title} | {e.url}\n  quote: {e.quote[:200]}" for e in evidence
    )

    user = SYNTHESIZER_USER.format(
        query=query,
        findings=findings_text,
        evidence=ev_text or "(no evidence collected)",
    )
    if previous_critique:
        user += f"\n\nPrevious critique to address:\n{previous_critique}"

    try:
        report = llm.structured(
            role="synthesizer",
            system=SYNTHESIZER_SYSTEM,
            user=user,
            schema=ResearchReport,
            temperature=0.4,
        )
    except Exception as e:
        # Small/offline models may fail complex structured synthesis.
        # Fall back to a findings-based report so the run still completes.
        from ..schemas import ReportSection

        store.emit(
            "synthesize",
            f"structured synthesis failed ({type(e).__name__}); using findings draft",
            level="warn",
        )
        ev_ids = [ev.id for ev in evidence]
        report = ResearchReport(
            title=query[:80] if query else "Research report",
            summary=(
                "Draft assembled directly from worker findings: the synthesizer "
                "LLM did not return valid structured output."
            ),
            sections=[
                ReportSection(
                    heading="Worker findings",
                    content=findings_text or "(no evidence collected)",
                    citations=ev_ids,
                )
            ],
            key_findings=[],
            open_questions=["Re-run with a stronger model for a fully synthesized report."],
            evidence_ids_used=ev_ids,
        )
    store.emit("synthesize", f"draft report: {len(report.sections)} sections", level="ok")
    return report, evidence
