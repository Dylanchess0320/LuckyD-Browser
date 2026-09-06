"""Finalizer: render the report to markdown and persist artifacts."""

from __future__ import annotations

from ..runtime.run_store import RunStore
from ..schemas import CitationAudit, EvidenceCard, ResearchReport


def render_markdown(
    query: str,
    report: ResearchReport,
    evidence: list[EvidenceCard],
    audit: CitationAudit | None = None,
) -> str:
    lines: list[str] = [f"# {report.title}", "", f"> {report.summary}", ""]
    lines.append(f"**Research question:** {query}")
    lines.append("")

    for sec in report.sections:
        lines.append(f"## {sec.heading}")
        lines.append("")
        lines.append(sec.content)
        lines.append("")

    if report.key_findings:
        lines.append("## Key Findings")
        for kf in report.key_findings:
            lines.append(f"- {kf}")
        lines.append("")

    if report.open_questions:
        lines.append("## Open Questions")
        for oq in report.open_questions:
            lines.append(f"- {oq}")
        lines.append("")

    # Citation audit banner: surface unresolved claim-verification failures.
    if audit is not None and audit.verdict == "fail":
        lines.append("## Citation Audit")
        lines.append("")
        lines.append(
            f"> **Warning:** {audit.summary}. The following claims could not be "
            "verified against their cited sources:"
        )
        lines.append("")
        for cs in audit.claims:
            if cs.verdict == "supported":
                continue
            lines.append(f"- {cs.verdict}: {cs.claim[:120]}")
            if cs.reason:
                lines.append(f"  - {cs.reason[:160]}")
        lines.append("")

    # Sources appendix
    cited = set(report.evidence_ids_used)
    for sec in report.sections:
        cited.update(sec.citations)
    cited = sorted(cited)
    by_id = {e.id: e for e in evidence}
    if cited:
        lines.append("## Sources")
        for i, cid in enumerate(cited, 1):
            ev = by_id.get(cid)
            if ev:
                lines.append(f"{i}. [{ev.title or ev.url}]({ev.url})")
            else:
                lines.append(f"{i}. {cid} (missing)")
        lines.append("")

    return "\n".join(lines)


def run_finalizer(
    query: str,
    report: ResearchReport,
    evidence: list[EvidenceCard],
    store: RunStore,
    audit: CitationAudit | None = None,
) -> str:
    md = render_markdown(query, report, evidence, audit)
    store.save_report(report, md)
    store.save_evidence(evidence)
    store.emit("finalizer", f"report written ({len(evidence)} sources)", level="ok")
    return md
