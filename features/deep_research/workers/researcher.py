"""Deep-research worker: an iterative search -> read -> extract -> gap loop.

Each worker runs up to ``DRS_RESEARCH_ROUNDS`` rounds:
  1. generate search queries for the sub-question (+ known gaps)
  2. run grounded search via Gemini -> collect source URLs + snippets
  3. rank/dedupe URLs, fetch + read top pages, extract relevant passages
  4. assess coverage of the sub-question
  5. if coverage >= threshold or no new sources, stop; else loop

Evidence accumulates across rounds. Only evidence with a real extracted
``quote``/``passage`` is kept, so downstream citations are text-backed.
"""

from __future__ import annotations

import asyncio
import contextlib

from ..config import settings
from ..prompts import RESEARCHER_SYSTEM
from ..runtime.run_store import RunStore
from ..schemas import (
    CoverageAssessment,
    EvidenceCard,
    ResearchTask,
    VerifiedPassages,
    WorkerResult,
)
from ..tools.extract import extract_relevant_passages
from ..tools.fetch import fetch_document
from ..tools.search_base import LLMProvider
from ..tools.search_ddg import DDGSearch
from ..tools.source_quality import dedupe_by_url, rank_urls, score_source

# Prompt for generating the next round's search queries from the task + gaps.
_QUERY_GEN_USER = """You are researching this sub-question:
{question}

Focus: {focus}

Known gaps so far (generate queries to fill them):
{gaps}

Already-searched queries (do not repeat):
{prior}

Return up to {n} diverse, specific web-search queries that would help answer
the sub-question or fill the gaps. Prefer recent / specific phrasings.
"""


def _collect_urls_via_grounding(llm: LLMProvider, query: str, worker_id: str) -> list[EvidenceCard]:
    """One grounded Gemini call -> evidence cards (URL+title+snippet)."""
    import json

    from ..runtime.budget import BudgetExhausted, get_budget
    from ..tools.cache import get_cache

    cache = get_cache()
    cached = cache.get_search(query, "gemini")
    if cached:
        try:
            return [EvidenceCard.model_validate(d) for d in json.loads(cached)]
        except Exception:
            pass

    get_budget().record_search()
    try:
        _text, ev = llm.grounded(
            role="worker",
            system=RESEARCHER_SYSTEM,
            user=f"Find sources answering: {query}",
        )
    except BudgetExhausted:
        raise
    except Exception:
        return []
    for e in ev:
        e.source_query = query[:120]
    with contextlib.suppress(Exception):
        cache.put_search(query, "gemini", json.dumps([e.model_dump() for e in ev]))
    return ev


def _collect_urls_ddg(query: str) -> list[EvidenceCard]:
    return DDGSearch().search(query)


def _collect_urls_premium(query: str, backend: str) -> list[EvidenceCard]:
    """Tavily/Brave search with budget accounting; [] on missing key/error."""
    from ..runtime.budget import get_budget

    try:
        get_budget().record_search()
    except Exception:
        return []
    try:
        if backend == "tavily":
            from ..tools.search_premium import TavilySearch

            return TavilySearch().search(query)
        if backend == "brave":
            from ..tools.search_premium import BraveSearch

            return BraveSearch().search(query)
    except Exception:
        pass
    return []


def _search_round(llm: LLMProvider, queries: list[str], worker_id: str) -> list[EvidenceCard]:
    """Run search for each query and merge the resulting evidence cards."""
    out: list[EvidenceCard] = []
    # Mock provider must stay offline: use its grounded() stub regardless of
    # the configured backend so dry-runs never touch the network.
    if getattr(llm, "_is_mock", False):
        for q in queries:
            out.extend(_collect_urls_via_grounding(llm, q, worker_id))
        return out
    backend = settings.search_backend
    if backend == "auto":
        try:
            backend = settings.effective_search_backend(getattr(llm, "provider_name", ""))
        except Exception:
            backend = "ddg"
    for q in queries:
        if backend == "gemini":
            out.extend(_collect_urls_via_grounding(llm, q, worker_id))
        elif backend in ("tavily", "brave"):
            cards = _collect_urls_premium(q, backend)
            out.extend(cards if cards else _collect_urls_ddg(q))
        elif backend in ("ddg", "luckyd", "auto"):
            # Keyless DDG search (no per-query LLM draft call); deep-read
            # fetches full pages afterwards for text-backed quotes.
            out.extend(_collect_urls_ddg(q))
        else:
            break
    return out


def _verify_passages(llm: LLMProvider, task: ResearchTask, candidates: list, doc_url: str) -> list:
    """Ask the LLM (structured) which candidate passages directly support the
    task; return only the verified ones in rank order."""
    if not candidates or getattr(llm, "_is_mock", False):
        return candidates
    numbered = "\n".join(f"[{i}] {p.quote[:240]}" for i, p in enumerate(candidates))
    user = (
        f"Sub-question: {task.question}\nFocus: {task.focus}\n\n"
        f"Candidate passages from {doc_url}:\n{numbered}\n\n"
        "Select the passages (by index) that directly and factually support "
        "answering the sub-question. Return each with a relevance score and a "
        "one-line reason. Skip passages that are off-topic or boilerplate."
    )
    try:
        res = llm.structured(
            role="worker", system=RESEARCHER_SYSTEM, user=user, schema=VerifiedPassages
        )
        chosen = getattr(res, "selected", []) or []
        if not chosen:
            return []
        idxs = sorted({s.index for s in chosen if isinstance(getattr(s, "index", None), int)})
        return [candidates[i] for i in idxs if 0 <= i < len(candidates)]
    except Exception:
        # If verification fails, fall back to the lexical top candidates.
        return candidates


def _deep_read(
    llm: LLMProvider,
    task: ResearchTask,
    cards: list[EvidenceCard],
    round_idx: int,
    worker_id: str,
) -> list[EvidenceCard]:
    """Fetch + extract passages for the top URLs, returning enriched cards."""
    # Mock provider (dry-run): skip real network; use search snippets as quotes.
    if getattr(llm, "_is_mock", False):
        out: list[EvidenceCard] = []
        for c in cards[: settings.max_urls_per_round]:
            out.append(
                c.model_copy(
                    update={
                        "id": "",
                        "quote": c.snippet or "Mock extracted quote.",
                        "passage": c.snippet or "Mock extracted quote.",
                        "round": round_idx,
                        "worker_id": worker_id,
                        "source_score": 0.8,
                    }
                )
            )
        return out

    urls = dedupe_by_url([c.url for c in cards])
    urls = rank_urls(urls)[: settings.max_urls_per_round]
    if not urls:
        return []

    focus_terms = [*list(task.search_hints), task.focus]

    enriched: list[EvidenceCard] = []
    # Map url -> the best snippet we already had from search.
    snippet_by_url = {c.url: c.snippet or "" for c in cards}

    for url in urls:
        doc = fetch_document(url, timeout=settings.fetch_timeout)
        if not doc.fetch_ok:
            # Keep the search-result snippet as a fallback quote-less card.
            enriched.append(
                EvidenceCard(
                    id="",
                    url=url,
                    title="",
                    snippet=snippet_by_url.get(url, ""),
                    quote="",
                    passage="",
                    source_query=task.question[:120],
                    worker_id=worker_id,
                    round=round_idx,
                    source_score=score_source(url, relevance=0.4),
                    confidence=0.4,
                )
            )
            continue

        passages = extract_relevant_passages(
            doc, task.question, focus_terms, top_k=8, min_score=0.12
        )
        verified = _verify_passages(llm, task, passages, url)[:2]
        for p in verified:
            enriched.append(
                EvidenceCard(
                    id="",
                    url=url,
                    title=doc.title or "",
                    snippet=p.quote[:200],
                    quote=p.quote,
                    passage=p.quote,
                    source_query=task.question[:120],
                    worker_id=worker_id,
                    round=round_idx,
                    published_date="",
                    source_score=score_source(url, relevance=p.score),
                    confidence=max(0.5, min(0.95, p.score)),
                )
            )
    return enriched


def _assess_coverage(
    llm: LLMProvider, task: ResearchTask, evidence: list[EvidenceCard]
) -> tuple[float, list[str]]:
    """Ask the model to grade coverage via structured output."""
    if not evidence:
        return 0.0, [task.question]
    if getattr(llm, "_is_mock", False):
        return 0.9, []
    quotes = "\n".join(f"- ({e.url}) {e.quote[:160]}" for e in evidence[:12])
    user = (
        f"Sub-question: {task.question}\n"
        f"Focus: {task.focus}\n\n"
        f"Collected evidence (url + quote):\n{quotes}\n\n"
        "Grade how well this evidence answers the sub-question."
    )
    try:
        res = llm.structured(
            role="worker",
            system=RESEARCHER_SYSTEM,
            user=user,
            schema=CoverageAssessment,
        )
        score = float(getattr(res, "coverage_score", 0.0))
        gaps = list(getattr(res, "gaps", []) or [])
        return max(0.0, min(1.0, score)), gaps
    except Exception:
        return 0.0, [task.question]


def _generate_queries(
    llm: LLMProvider, task: ResearchTask, gaps: list[str], prior: list[str], n: int
) -> list[str]:
    """Have the model write the next round's search queries."""
    user = _QUERY_GEN_USER.format(
        question=task.question,
        focus=task.focus,
        gaps="\n".join(f"- {g}" for g in gaps) or "(none yet)",
        prior="\n".join(f"- {p}" for p in prior) or "(none)",
        n=n,
    )
    try:
        text = llm.text(role="worker", system=RESEARCHER_SYSTEM, user=user)
        qs = [line.strip("- ").strip('"').strip() for line in text.splitlines() if line.strip()]
        return [q for q in qs if len(q) > 2][:n]
    except Exception:
        return [task.question]


def _summarize(
    llm: LLMProvider, task: ResearchTask, evidence: list[EvidenceCard], worker_id: str
) -> str:
    """Synthesize the worker's grounded findings from its evidence quotes."""
    if not evidence:
        return f"No usable evidence was collected for: {task.question}"
    quotes = "\n\n".join(f"[{e.id}] ({e.url}) {e.quote[:300]}" for e in evidence)
    user = (
        f"Sub-question: {task.question}\nFocus: {task.focus}\n\n"
        f"Evidence quotes (cite as [eID]):\n{quotes}\n\n"
        "Write a tight, factual prose summary answering the sub-question using "
        "ONLY these quotes. Cite every concrete claim as [eID]. Note gaps."
    )
    try:
        return llm.text(role="worker", system=RESEARCHER_SYSTEM, user=user)
    except Exception as e:
        from ..runtime.budget import BudgetExhausted

        if isinstance(e, BudgetExhausted):
            # Don't swallow budget exhaustion as a generic "Summary failed":
            # let run_one_task record stop_reason="budget_exhausted" and keep
            # the raw evidence quotes for a best-effort report.
            raise
        return f"Summary failed: {e}"


def run_one_task(llm: LLMProvider, task: ResearchTask, worker_id: str) -> WorkerResult:
    """Run the full iterative deep-read loop for one research task."""
    from ..runtime.budget import BudgetExhausted

    evidence: list[EvidenceCard] = []
    seen_urls: set[str] = set()
    prior_queries: list[str] = []
    gaps: list[str] = []
    coverage = 0.0
    stop_reason = "max_rounds"
    rounds_run = 0

    try:
        for round_idx in range(1, settings.research_rounds + 1):
            rounds_run = round_idx
            queries = _generate_queries(llm, task, gaps, prior_queries, settings.searches_per_round)
            if not queries:
                queries = [task.question]
            prior_queries.extend(queries)

            raw_cards = _search_round(llm, queries, worker_id)
            # Only deep-read URLs we have not fetched before; track saturation by
            # canonical URL so query-string variants don't re-fetch.
            from ..tools.cache import canonicalize_url

            new_cards = [c for c in raw_cards if c.url and canonicalize_url(c.url) not in seen_urls]
            new_urls = {canonicalize_url(c.url) for c in new_cards}
            seen_urls.update(canonicalize_url(c.url) for c in raw_cards)
            if round_idx > 1 and not new_urls:
                stop_reason = "no_new_sources"
                break

            enriched = _deep_read(llm, task, new_cards, round_idx, worker_id)
            # Only keep cards backed by a real extracted quote/passage.
            enriched = [e for e in enriched if e.quote or e.passage]
            evidence.extend(enriched)

            # Cap total evidence per worker to control cost/context, then assess
            # coverage honestly — hitting the cap does not imply the question is
            # answered.
            if len(evidence) >= settings.max_passages_per_worker:
                evidence = sorted(
                    evidence,
                    key=lambda e: (e.source_score, e.confidence),
                    reverse=True,
                )[: settings.max_passages_per_worker]
                coverage, gaps = _assess_coverage(llm, task, evidence)
                stop_reason = (
                    "coverage_threshold" if coverage >= settings.min_coverage else "passage_cap"
                )
                break

            coverage, gaps = _assess_coverage(llm, task, evidence)
            if coverage >= settings.min_coverage:
                stop_reason = "coverage_threshold"
                break
    except BudgetExhausted:
        stop_reason = "budget_exhausted"

    # Assign stable global ids within the worker (re-mapped globally later).
    for i, e in enumerate(evidence):
        e.id = f"e{i}"

    try:
        findings = _summarize(llm, task, evidence, worker_id)
    except BudgetExhausted:
        # Budget ran out after evidence collection: keep the raw quotes so the
        # synthesizer can still finalize a best-effort report instead of
        # crashing the whole run (BudgetExhausted is sticky once raised).
        stop_reason = "budget_exhausted"
        if evidence:
            findings = (
                "Summary skipped (research budget exhausted). Raw evidence "
                f"quotes retained for {task.question}"
            )
        else:
            findings = f"No usable evidence was collected for: {task.question}"
    return WorkerResult(
        task_id=task.id,
        worker_id=worker_id,
        findings=findings,
        evidence=evidence,
        gaps=gaps,
        coverage_score=coverage,
        stop_reason=stop_reason,
        follow_up_queries=prior_queries,
        rounds_run=rounds_run,
    )


async def run_research(
    llm: LLMProvider, tasks: list[ResearchTask], store: RunStore
) -> list[WorkerResult]:
    sem = asyncio.Semaphore(max(1, settings.max_parallel))

    async def bound(t: ResearchTask, i: int) -> WorkerResult:
        wid = f"researcher-{i + 1}"
        async with sem:
            store.emit(
                "researcher",
                f"starting {t.id}: {t.question[:50]}",
            )
            res = await asyncio.to_thread(run_one_task, llm, t, wid)
            store.emit(
                "researcher",
                f"done {t.id}: cov={res.coverage_score:.2f} "
                f"sources={len(res.evidence)} stop={res.stop_reason}",
                level="ok",
            )
            return res

    results = await asyncio.gather(*[bound(t, i) for i, t in enumerate(tasks)])
    return list(results)
