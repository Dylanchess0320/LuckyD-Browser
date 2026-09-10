"""Claim-level citation auditor.

The critic checks *that* citations exist. The verifier checks *that each
factual claim is actually supported by the cited evidence quote*. A claim that
"revenue rose 10%" citing a quote that says "revenue fell 10%" fails here,
even though the citation is structurally valid.

Pipeline:
  1. Extract every factual claim from the final report (structured output).
  2. For each claim, inspect ONLY its cited evidence quotes.
  3. Mark: supported / partially_supported / unsupported / wrong_citation /
     missing_citation.
  4. Hard-fail the run if any important claim is unsupported or mis-cited.
"""

from __future__ import annotations

import re

from ..runtime.run_store import RunStore
from ..schemas import (
    CitationAudit,
    ClaimSupport,
    EvidenceCard,
    FactualClaim,
    ResearchReport,
)
from ..tools.search_base import LLMProvider

_CITATION_TOKEN = re.compile(r"\[e(\d+)\]")

VERIFY_SYSTEM = (
    "You are a strict citation auditor. You verify that each factual claim in "
    "a research report is genuinely supported by the exact evidence quotes cited "
    "for it. You never accept a claim just because a citation is present — you "
    "read the quote and check it actually entails the claim."
)

from pydantic import BaseModel, Field


class ClaimList(BaseModel):
    """Structured extraction of factual claims from the report text."""

    claims: list[FactualClaim] = Field(default_factory=list)


def _report_text(report: ResearchReport) -> tuple[str, list[tuple[str, str]]]:
    """Return (joined_text, [(label, text), ...]) of every report section."""
    parts: list[tuple[str, str]] = [("summary", report.summary)]
    for s in report.sections:
        parts.append((s.heading, s.content))
    for kf in report.key_findings:
        parts.append(("key_finding", kf))
    joined = "\n\n".join(f"[{label}] {text}" for label, text in parts)
    return joined, parts


def _regex_claims(report: ResearchReport) -> list[FactualClaim]:
    """Deterministic fallback: derive claims from sentences with [eID] tokens."""
    claims: list[FactualClaim] = []
    for label, text in _report_text(report)[1]:
        for sent in re.split(r"(?<=[.!?])\s+", text):
            ids = sorted(set(_CITATION_TOKEN.findall(sent)))
            if ids and len(sent.strip()) > 8:
                claims.append(
                    FactualClaim(
                        claim=sent.strip(),
                        evidence_ids=[f"e{i}" for i in ids],
                        location=label,
                    )
                )
    return claims


def extract_claims(llm: LLMProvider, report: ResearchReport) -> list[FactualClaim]:
    """Ask the model to extract every factual claim + its cited evidence IDs."""
    joined, _ = _report_text(report)
    user = (
        "Extract every factual claim from this report. For each, record the "
        "exact claim text and the evidence ids cited for it (from [eID] tokens). "
        "Skip purely interpretive or framing sentences.\n\n"
        f"REPORT:\n{joined[:8000]}\n"
    )
    try:
        res = llm.structured(role="critic", system=VERIFY_SYSTEM, user=user, schema=ClaimList)
        claims = list(getattr(res, "claims", []) or [])
        # Guard: if structured extraction returned nothing but the report has
        # [eID] tokens, fall back to deterministic regex extraction so claims
        # are never silently skipped.
        if not claims and _CITATION_TOKEN.search(joined):
            return _regex_claims(report)
        return claims
    except Exception:
        return _regex_claims(report)


def audit_claims(
    llm: LLMProvider, report: ResearchReport, evidence: list[EvidenceCard]
) -> CitationAudit:
    """Verify each claim against its cited evidence quotes."""
    if getattr(llm, "_is_mock", False):
        # Mock path: assume claims are supported if they cite real evidence.
        ev_ids = {e.id for e in evidence}
        claims = extract_claims(llm, report)
        supported = [
            ClaimSupport(
                claim=c.claim,
                verdict=(
                    "supported"
                    if c.evidence_ids and all(eid in ev_ids for eid in c.evidence_ids)
                    else "missing_citation"
                ),
                evidence_ids=c.evidence_ids,
            )
            for c in claims
        ]
        unsupported = sum(1 for c in supported if c.verdict != "supported")
        return CitationAudit(
            verdict="pass" if unsupported == 0 else "fail",
            claims=supported,
            unsupported_count=unsupported,
            summary=f"mock audit: {unsupported} unsupported claims",
        )

    ev_by_id = {e.id: e for e in evidence}
    claims = extract_claims(llm, report)
    if not claims:
        return CitationAudit(
            verdict="pass",
            claims=[],
            unsupported_count=0,
            summary="no factual claims extracted",
        )

    # Build a compact context of only the cited quotes for each claim.
    findings: list[ClaimSupport] = []
    for c in claims:
        quotes = []
        for eid in c.evidence_ids:
            ev = ev_by_id.get(eid)
            if ev is None:
                quotes.append(f"[{eid}] MISSING")
            else:
                quotes.append(f"[{eid}] ({ev.url}) {ev.quote[:300]}")
        quotes_block = "\n".join(quotes) or "(no citations)"

        user = (
            f"CLAIM: {c.claim}\n"
            f"LOCATION: {c.location}\n\n"
            f"CITED EVIDENCE:\n{quotes_block}\n\n"
            "Decide if the cited evidence genuinely supports this claim. "
            "Return one of: supported, partially_supported, unsupported, "
            "wrong_citation, missing_citation. Give a one-line reason."
        )
        try:
            res = llm.structured(
                role="critic", system=VERIFY_SYSTEM, user=user, schema=ClaimSupport
            )
            findings.append(
                ClaimSupport(
                    claim=c.claim,
                    verdict=getattr(res, "verdict", "unsupported"),
                    reason=getattr(res, "reason", ""),
                    evidence_ids=c.evidence_ids,
                )
            )
        except Exception:
            findings.append(
                ClaimSupport(
                    claim=c.claim,
                    verdict="unsupported",
                    reason="verification call failed",
                    evidence_ids=c.evidence_ids,
                )
            )

    unsupported = sum(1 for f in findings if f.verdict != "supported")
    verdict = "fail" if unsupported > 0 else "pass"
    return CitationAudit(
        verdict=verdict,
        claims=findings,
        unsupported_count=unsupported,
        summary=f"{unsupported} of {len(findings)} claims unsupported or mis-cited",
    )


def run_verify(
    llm: LLMProvider, report: ResearchReport, evidence: list[EvidenceCard], store: RunStore
) -> CitationAudit:
    store.emit("verifier", "auditing claim-level citations...")
    audit = audit_claims(llm, report, evidence)
    level = "ok" if audit.verdict == "pass" else "warn"
    store.emit(
        "verifier",
        f"{audit.verdict}: {audit.unsupported_count} unsupported of {len(audit.claims)} claims",
        level=level,
    )
    return audit
