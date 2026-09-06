"""Pydantic schemas — the structured contract between every agent in the swarm.

Workers may only emit typed values defined here. The synthesizer may only
reference evidence IDs that actually exist in the collected evidence set, and
the citation-integrity test enforces exactly that.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class ResearchTask(BaseModel):
    """A single sub-question the planner assigns to a research worker."""

    id: str = Field(description="Short stable id, e.g. 't1'")
    question: str = Field(description="The specific sub-question to research")
    focus: str = Field(description="What angle or evidence the worker should target")
    search_hints: list[str] = Field(
        default_factory=list,
        description="Optional search-query phrasings to try",
    )
    priority: int = Field(default=1, ge=1, le=5, description="1 highest -> 5 lowest")


class ResearchPlan(BaseModel):
    """The planner's decomposition of the user query into research tasks."""

    reasoning: str = Field(description="Why this decomposition covers the query")
    tasks: list[ResearchTask] = Field(
        description="Ordered list of research tasks to dispatch to workers",
        min_length=1,
    )


class EvidenceCard(BaseModel):
    """A single citable source captured by a research worker.

    Every claim in the final report must reference one or more evidence IDs,
    and every evidence card carries a real ``quote``/``passage`` extracted from
    the source page — not just a URL. This is what makes a citation "backed."
    """

    id: str = Field(description="Stable evidence id, e.g. 'e3'")
    url: str
    title: str = Field(default="")
    snippet: str = Field(
        default="",
        description="Short summary or grounded extract from the source",
    )
    quote: str = Field(
        default="",
        description="A verbatim quote/passage extracted from the source page backing a claim",
    )
    passage: str = Field(
        default="",
        description="A longer extracted passage (context around the quote)",
    )
    source_query: str = Field(default="", description="The query that surfaced this")
    worker_id: str = Field(default="")
    round: int = Field(default=1, ge=1, description="Which research round produced this")
    published_date: str = Field(default="", description="Source publish date if detectable")
    source_score: float = Field(default=0.5, ge=0.0, le=1.0, description="Source quality score")
    retrieved_at: str = Field(default_factory=utcnow)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class SourceDocument(BaseModel):
    """A fetched web page with extracted clean text."""

    url: str
    title: str = Field(default="")
    text: str = Field(default="", description="Cleaned, boilerplate-stripped page text")
    fetch_ok: bool = Field(default=True)
    error: str = Field(default="")


class Passage(BaseModel):
    """A relevant passage carved out of a source document."""

    url: str
    quote: str = Field(description="The extracted passage text (may be a quote)")
    char_offset: int = Field(default=0)
    score: float = Field(default=0.5, ge=0.0, le=1.0)


class SearchQuery(BaseModel):
    query: str
    rationale: str = Field(default="")


class CoverageAssessment(BaseModel):
    """LLM-graded coverage of a sub-question by the collected evidence."""

    coverage_score: float = Field(ge=0.0, le=1.0)
    gaps: list[str] = Field(default_factory=list)
    is_sufficient: bool = False


class VerifiedPassage(BaseModel):
    """A passage an LLM has confirmed directly supports the sub-question."""

    index: int = Field(ge=0)
    relevance: float = Field(ge=0.0, le=1.0)
    reason: str = Field(default="")


class VerifiedPassages(BaseModel):
    """Structured LLM response selecting the best supporting passages."""

    selected: list[VerifiedPassage] = Field(default_factory=list)


class WorkerResult(BaseModel):
    """The structured output of one research worker run."""

    task_id: str
    worker_id: str
    findings: str = Field(description="A grounded prose summary answering the task question")
    evidence: list[EvidenceCard] = Field(
        default_factory=list, description="Citable sources backing the findings"
    )
    gaps: list[str] = Field(
        default_factory=list, description="What could not be answered or is uncertain"
    )
    coverage_score: float = Field(default=0.0, ge=0.0, le=1.0)
    stop_reason: str = Field(default="max_rounds")
    follow_up_queries: list[str] = Field(default_factory=list)
    rounds_run: int = Field(default=0)


class ReportSection(BaseModel):
    heading: str
    content: str = Field(description="Markdown body. Cite evidence inline as [eID].")
    citations: list[str] = Field(
        default_factory=list,
        description="Evidence IDs this section relies on, e.g. ['e1','e3']",
    )


class ResearchReport(BaseModel):
    """The synthesizer's structured, citation-backed report."""

    title: str
    summary: str = Field(description="3-6 sentence executive summary")
    sections: list[ReportSection]
    key_findings: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    evidence_ids_used: list[str] = Field(
        default_factory=list,
        description="Every evidence id referenced anywhere in the report",
    )


class CritiqueIssue(BaseModel):
    severity: Literal["low", "medium", "high"] = "medium"
    description: str
    evidence_id: str | None = Field(
        default=None, description="Evidence id the issue concerns, if applicable"
    )


class Critique(BaseModel):
    """The critic's verdict on the draft report."""

    status: Literal["pass", "revise"]
    issues: list[CritiqueIssue] = Field(default_factory=list)
    missing_aspects: list[str] = Field(
        default_factory=list,
        description="Parts of the original query not yet addressed",
    )
    suggested_fixes: list[str] = Field(
        default_factory=list,
        description="Concrete, actionable next steps for the synthesizer",
    )
    overall_confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class CostAccumulator(BaseModel):
    """Tracks approximate token usage / call counts per role."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    search_calls: int = 0


class BudgetUsage(BaseModel):
    """Live snapshot of budget consumption across the whole run."""

    llm_calls: int = 0
    search_calls: int = 0
    fetched_urls: int = 0
    elapsed_seconds: float = 0.0
    limits: dict[str, int | None] = Field(default_factory=dict)
    exhausted: bool = False
    reason: str = Field(default="")


class FactualClaim(BaseModel):
    """A single factual claim extracted from the final report."""

    claim: str = Field(description="The factual assertion made in the report")
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="Evidence ids cited for this claim, e.g. ['e0','e2']",
    )
    location: str = Field(
        default="", description="Where it appears, e.g. 'summary' or section heading"
    )


class ClaimSupport(BaseModel):
    """The verifier's verdict on one claim vs. its cited evidence quotes."""

    claim: str
    verdict: Literal[
        "supported",
        "partially_supported",
        "unsupported",
        "wrong_citation",
        "missing_citation",
    ]
    reason: str = Field(default="")
    evidence_ids: list[str] = Field(default_factory=list)


class CitationAudit(BaseModel):
    """Full claim-level audit of the final report."""

    verdict: Literal["pass", "fail"]
    claims: list[ClaimSupport] = Field(default_factory=list)
    unsupported_count: int = Field(default=0)
    summary: str = Field(default="")
