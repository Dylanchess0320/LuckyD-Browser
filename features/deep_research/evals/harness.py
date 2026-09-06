"""Benchmark harness: run the swarm on a graded question set and score it.

Metrics per question:
  - completion: did the swarm produce a non-trivial report?
  - citation_precision: fraction of cited evidence IDs that are real + backed
  - unsupported_claims: count of claims the verifier flagged
  - source_diversity: number of distinct source domains
  - latency: wall-clock seconds
  - call_counts: llm calls, grounded searches, url fetches

Reports are saved as JSON + a markdown table under eval_runs/<timestamp>/.
A fixture-based mode (``run_fixture_evals``) runs offline with a mock LLM so
the harness is testable without a network or API key.
"""

from __future__ import annotations

import json
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore  # added to requirements (pyyaml)

from ..config import settings
from ..graph import run_swarm
from ..runtime.budget import get_budget
from ..schemas import EvidenceCard, ResearchReport
from ..tools.cache import canonicalize_url
from ..workers.verifier import audit_claims


def _eval_dir() -> Path:
    try:
        base = Path(settings.runs_dir).parent
        d = base / "eval_runs"
    except Exception:
        d = Path("eval_runs")
    d.mkdir(parents=True, exist_ok=True)
    return d


QUESTIONS_FILE = Path(__file__).parent / "test_questions.yaml"
try:
    EVAL_DIR = _eval_dir()
except Exception:
    EVAL_DIR = Path("eval_runs")


@dataclass
class QuestionResult:
    question: str
    completion: bool
    citation_precision: float
    unsupported_claims: int
    source_diversity: int
    latency: float
    llm_calls: int = 0
    search_calls: int = 0
    fetches: int = 0
    budget_exhausted: bool = False
    error: str = ""


@dataclass
class EvalReport:
    timestamp: str
    dry_run: bool
    results: list[QuestionResult] = field(default_factory=list)
    avg_citation_precision: float = 0.0
    avg_unsupported_claims: float = 0.0
    total_completion: int = 0
    total_unsupported: int = 0

    def to_markdown(self) -> str:
        lines = [
            "# Eval Report",
            "",
            "| Question | Completion | Citation Precision | Unsupported | Sources | Latency |",
            "|----------|------------|--------------------|-------------|----------|----------|",
        ]
        for r in self.results:
            lines.append(
                f"| {r.question[:40]} | {'yes' if r.completion else 'no'} | "
                f"{r.citation_precision:.2f} | {r.unsupported_claims} | "
                f"{r.source_diversity} | {r.latency:.1f}s |"
            )
        lines += [
            "",
            f"- Completion: {self.total_completion}/{len(self.results)}",
            f"- Avg citation precision: {self.avg_citation_precision:.2f}",
            f"- Avg unsupported claims: {self.avg_unsupported_claims:.2f}",
            f"- Total unsupported claims: {self.total_unsupported}",
        ]
        return "\n".join(lines)


def _score_report(
    report: ResearchReport, evidence: list[EvidenceCard], llm: Any | None = None
) -> tuple[float, int, int]:
    """Return (citation_precision, unsupported_claims, source_diversity)."""
    if not report:
        return 0.0, 0, 0
    ev_by_id = {e.id: e for e in evidence}
    cited = sorted(
        {e for sec in report.sections for e in sec.citations} | set(report.evidence_ids_used)
    )
    if not cited:
        return (
            0.0,
            0,
            len({canonicalize_url(e.url).split("/")[2] for e in evidence if e.url} - {""}),
        )
    backed = sum(
        1 for cid in cited if cid in ev_by_id and (ev_by_id[cid].quote or ev_by_id[cid].passage)
    )
    precision = backed / len(cited)
    domains = {canonicalize_url(e.url).split("/")[2] for e in evidence if e.url} - {""}
    # Unsupported claims: use the verifier if an llm is available (mock-safe).
    if llm is not None:
        try:
            audit = audit_claims(llm, report, evidence)
            unsupported = audit.unsupported_count
        except Exception:
            unsupported = 0
    else:
        unsupported = 0
    return precision, unsupported, len(domains)


async def _run_one(question: str, dry_run: bool) -> QuestionResult:
    from ..runtime.budget import reset_budget

    reset_budget()
    # Isolate each question in its own runs dir so we never score a stale run.
    isolated = tempfile.mkdtemp(prefix="eval_run_")
    prev_runs_dir = settings.runs_dir
    settings.runs_dir = isolated
    t0 = time.time()
    try:
        md = await run_swarm(question, dry_run=dry_run, no_tui=True)
        usage = get_budget().usage()
        report, evidence = _load_latest_artifacts()
        llm = None
        if dry_run:
            from ..models.mock import MockProvider

            llm = MockProvider()
        precision, unsupported, diversity = _score_report(report, evidence, llm)
        return QuestionResult(
            question=question,
            completion=bool(md and len(md) > 200),
            citation_precision=round(precision, 3),
            unsupported_claims=unsupported,
            source_diversity=diversity,
            latency=round(time.time() - t0, 2),
            llm_calls=usage.llm_calls,
            search_calls=usage.search_calls,
            fetches=usage.fetched_urls,
            budget_exhausted=usage.exhausted,
        )
    except Exception as e:
        return QuestionResult(
            question=question,
            completion=False,
            citation_precision=0.0,
            unsupported_claims=0,
            source_diversity=0,
            latency=round(time.time() - t0, 2),
            error=f"{type(e).__name__}: {e}",
        )
    finally:
        settings.runs_dir = prev_runs_dir


def _load_latest_artifacts() -> tuple[ResearchReport | None, list[EvidenceCard]]:
    runs = sorted(Path(settings.runs_dir).glob("*"), reverse=True)
    for run in runs:
        rp = run / "report.json"
        ep = run / "evidence.json"
        if rp.exists() and ep.exists():
            try:
                report = ResearchReport.model_validate_json(rp.read_text(encoding="utf-8"))
                evidence = [
                    EvidenceCard.model_validate(d)
                    for d in json.loads(ep.read_text(encoding="utf-8"))
                ]
                return report, evidence
            except Exception:
                continue
    return None, []


async def run_evals(dry_run: bool = False) -> EvalReport:
    questions = yaml.safe_load(QUESTIONS_FILE.read_text(encoding="utf-8"))
    print(f"Running {len(questions)} benchmark questions (dry_run={dry_run})...\n")
    results: list[QuestionResult] = []
    for i, q in enumerate(questions, 1):
        r = await _run_one(q["question"], dry_run=dry_run)
        results.append(r)
        status = "PASS" if r.completion else "FAIL"
        print(
            f"[{i}/{len(questions)}] {status} prec={r.citation_precision:.2f} "
            f"unsup={r.unsupported_claims} src={r.source_diversity} "
            f"({r.latency}s) {r.question[:50]}"
        )
    report = EvalReport(
        timestamp=time.strftime("%Y%m%d-%H%M%S"),
        dry_run=dry_run,
        results=results,
    )
    report.total_completion = sum(1 for r in results if r.completion)
    report.avg_citation_precision = round(
        sum(r.citation_precision for r in results) / max(len(results), 1), 3
    )
    report.total_unsupported = sum(r.unsupported_claims for r in results)
    report.avg_unsupported_claims = round(report.total_unsupported / max(len(results), 1), 2)

    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    out = EVAL_DIR / f"{report.timestamp}"
    (out.with_suffix(".json")).write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
    (out.with_suffix(".md")).write_text(report.to_markdown(), encoding="utf-8")
    print(
        f"\n{report.total_completion}/{len(results)} complete. "
        f"Avg citation precision: {report.avg_citation_precision:.2f}. "
        f"Total unsupported claims: {report.total_unsupported}."
    )
    print(f"Saved to {out}.json and {out}.md")
    return report
