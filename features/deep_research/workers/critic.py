"""Critic: verify the draft report is complete, accurate, and well-cited."""

from __future__ import annotations

import re

from ..prompts import CRITIC_SYSTEM
from ..runtime.run_store import RunStore
from ..schemas import Critique, CritiqueIssue, EvidenceCard, ResearchReport
from ..tools.search_base import LLMProvider

# Matches inline citation tokens like [e0], [e12] used in report text.
_INLINE_CITE = re.compile(r"\[(e\d+)\]")


def run_critic(
    llm: LLMProvider,
    query: str,
    report: ResearchReport,
    evidence: list[EvidenceCard],
    store: RunStore,
) -> Critique:
    ev_ids = {e.id for e in evidence}
    # Hard structural check the LLM cannot override: every cited id must exist.
    # This covers BOTH explicit citation lists AND inline [eID] tokens in prose,
    # so the model cannot smuggle in an uncited reference through summary/body.
    cited = set(report.evidence_ids_used)
    for sec in report.sections:
        cited.update(sec.citations)
    inline_text = " ".join(
        [report.summary]
        + [s.content for s in report.sections]
        + report.key_findings
        + report.open_questions
    )
    cited.update(_INLINE_CITE.findall(inline_text))
    unknown = sorted(cited - ev_ids)

    user = (
        f"Original question: {query}\n\n"
        f"Report (JSON):\n{report.model_dump_json(indent=2)}\n\n"
        f"Valid evidence ids: {sorted(ev_ids)}\n\n"
        f"Unknown cited ids (not in evidence set): {unknown}\n"
    )

    try:
        critique = llm.structured(
            role="critic",
            system=CRITIC_SYSTEM,
            user=user,
            schema=Critique,
            temperature=0.1,
        )
    except Exception as e:
        # Small/offline models may fail structured critique; accept the draft
        # so the run completes instead of crashing.
        store.emit(
            "critique",
            f"structured critique failed ({type(e).__name__}); accepting draft",
            level="warn",
        )
        critique = Critique(
            status="pass",
            issues=[],
            missing_aspects=[],
            suggested_fixes=[],
            overall_confidence=0.4,
        )

    # Enforce structural failure: unknown citations -> must revise.
    if unknown:
        issues = list(critique.issues)
        issues.insert(
            0,
            CritiqueIssue(
                severity="high",
                description=f"Report cites unknown evidence ids: {unknown}",
                evidence_id=None,
            ),
        )
        critique = critique.model_copy(update={"status": "revise", "issues": issues})

    # Warn (don't hard-fail) on URL-only citations with no extracted passage:
    # a citation without a backing quote is weak evidence and should be revisited.
    ev_by_id = {e.id: e for e in evidence}
    unbacked = sorted(
        cid
        for cid in cited
        if cid in ev_by_id and not (ev_by_id[cid].quote or ev_by_id[cid].passage)
    )
    if unbacked and critique.status != "revise":
        issues = list(critique.issues)
        issues.append(
            CritiqueIssue(
                severity="medium",
                description=(
                    f"Citations lack extracted passages (URL-only): {unbacked}; "
                    "consider deeper reading."
                ),
                evidence_id=unbacked[0] if unbacked else None,
            )
        )
        critique = critique.model_copy(update={"issues": issues})

    store.emit(
        "critique",
        f"{critique.status} (confidence {critique.overall_confidence:.2f})",
        level="ok" if critique.status == "pass" else "warn",
    )
    return critique
