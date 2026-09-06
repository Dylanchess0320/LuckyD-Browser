"""LangGraph orchestration: planner -> research -> synthesize -> critique loop.

The critique loop runs up to `max_iterations` times; if the critic keeps
rejecting, the finalizer emits the best draft with a warning. All node I/O is
typed via the SwarmState TypedDict + Pydantic schemas.

Modernized for langgraph>=1.0: uses START entrypoint and explicit path maps.
"""

from __future__ import annotations

import asyncio
from typing import TypedDict

from .models.router import get_llm
from .runtime.events import EventEmitter
from .runtime.run_store import RunStore
from .runtime.tui import SwarmDisplay
from .schemas import (
    CitationAudit,
    Critique,
    CritiqueIssue,
    EvidenceCard,
    ResearchPlan,
    ResearchReport,
    WorkerResult,
)
from .tools.search_base import LLMProvider
from .workers.critic import run_critic
from .workers.finalizer import run_finalizer
from .workers.planner import run_planner
from .workers.researcher import run_research
from .workers.synthesizer import run_synthesizer
from .workers.verifier import run_verify


class SwarmState(TypedDict, total=False):
    query: str
    plan: ResearchPlan | None
    results: list[WorkerResult]
    evidence: list[EvidenceCard]
    report: ResearchReport | None
    critique: Critique | None
    citation_audit: CitationAudit | None
    final_markdown: str | None
    iterations: int


def build_graph(llm: LLMProvider, store: RunStore, display=None):
    """Build and compile the swarm graph. Returns a CompiledGraph."""
    from langgraph.graph import END, START, StateGraph

    def init_node(state: SwarmState) -> dict:
        store.emit("init", f"starting research: {state['query'][:60]}")
        return {"iterations": 0, "evidence": [], "results": []}

    def planner_node(state: SwarmState) -> dict:
        plan = run_planner(llm, state["query"], store)
        if display is not None:
            display.attach_plan(plan)
        return {"plan": plan}

    async def research_node(state: SwarmState) -> dict:
        plan: ResearchPlan = state["plan"]  # type: ignore[assignment]
        results = await run_research(llm, plan.tasks, store)
        return {"results": results}

    def synthesize_node(state: SwarmState) -> dict:
        prev = ""
        c = state.get("critique")
        if c and c.suggested_fixes:
            prev = "\n".join(f"- {f}" for f in c.suggested_fixes)
        report, evidence = run_synthesizer(
            llm, state["query"], state["results"], store, previous_critique=prev
        )
        if display is not None:
            display.set_evidence_count(len(evidence))
        return {
            "report": report,
            "evidence": evidence,
            "iterations": state.get("iterations", 0) + 1,
        }

    def critique_node(state: SwarmState) -> dict:
        critique = run_critic(llm, state["query"], state["report"], state["evidence"], store)
        store.save_critique(critique, state.get("iterations", 0))
        return {"critique": critique}

    def verify_node(state: SwarmState) -> dict:
        # Claim-level audit: if any factual claim is unsupported or mis-cited,
        # fold the failures into the critique WITH actionable suggested_fixes
        # so the synthesizer gets concrete guidance on the next revision.
        audit = run_verify(llm, state["report"], state.get("evidence", []), store)
        store.save_citation_audit(audit)
        out: dict = {"citation_audit": audit}
        if audit.verdict == "fail":
            c: Critique = state["critique"]  # type: ignore[assignment]
            issues = list(c.issues)
            fixes = list(c.suggested_fixes)
            issues.append(
                CritiqueIssue(
                    severity="high",
                    description=f"Citation audit failed: {audit.summary}",
                    evidence_id=None,
                )
            )
            for cs in audit.claims:
                if cs.verdict == "supported":
                    continue
                ev = next((e for e in state.get("evidence", []) if e.id in cs.evidence_ids), None)
                quote = ev.quote[:120] if ev else "(no quote)"
                fixes.append(
                    f'Fix or remove claim "{cs.claim[:80]}": verifier verdict '
                    f"{cs.verdict} ({cs.reason[:80]}); cited quote: {quote}"
                )
            out["critique"] = c.model_copy(
                update={"status": "revise", "issues": issues, "suggested_fixes": fixes}
            )
        return out

    def finalize_node(state: SwarmState) -> dict:
        report: ResearchReport = state["report"]  # type: ignore[assignment]
        audit = state.get("citation_audit")
        md = run_finalizer(state["query"], report, state["evidence"], store, audit)
        store.emit("finalizer", "run complete", level="ok")
        return {"final_markdown": md}

    def route_after_critique(state: SwarmState) -> str:
        c: Critique = state["critique"]  # type: ignore[assignment]
        from .config import settings

        if c.status == "pass":
            return "verify"
        if state.get("iterations", 0) >= settings.max_iterations:
            store.emit("critique", "max iterations reached; finalizing best draft", level="warn")
            return "finalize"
        return "synthesize"

    def route_after_verify(state: SwarmState) -> str:
        from .config import settings

        audit: CitationAudit = state["citation_audit"]  # type: ignore[assignment]
        if audit.verdict == "pass":
            return "finalize"
        if state.get("iterations", 0) >= settings.max_iterations:
            store.emit(
                "verifier", "max iterations; finalizing despite audit failures", level="warn"
            )
            return "finalize"
        return "synthesize"

    g = StateGraph(SwarmState)
    g.add_node("init", init_node)
    g.add_node("planner", planner_node)
    g.add_node("research", research_node)
    g.add_node("synthesize", synthesize_node)
    g.add_node("critique", critique_node)
    g.add_node("verify", verify_node)
    g.add_node("finalize", finalize_node)

    g.add_edge(START, "init")
    g.add_edge("init", "planner")
    g.add_edge("planner", "research")
    g.add_edge("research", "synthesize")
    g.add_edge("synthesize", "critique")
    g.add_conditional_edges(
        "critique",
        route_after_critique,
        path_map={"verify": "verify", "finalize": "finalize", "synthesize": "synthesize"},
    )
    g.add_conditional_edges(
        "verify",
        route_after_verify,
        path_map={"finalize": "finalize", "synthesize": "synthesize"},
    )
    g.add_edge("finalize", END)

    return g.compile()


async def run_swarm(
    query: str,
    dry_run: bool = False,
    no_tui: bool = False,
    provider: str | None = None,
    llm: LLMProvider | None = None,
) -> str:
    """Convenience entrypoint: build graph, run, return final markdown."""
    from .runtime.budget import reset_budget
    from .tools.cache import reset_cache

    reset_budget()  # fresh budget per run
    reset_cache()  # pick up --no-cache / settings changes

    active_llm = llm if llm is not None else get_llm(dry_run=dry_run, provider=provider)
    # Resolve 'auto' search backend now so workers see a concrete value.
    try:
        from .config import settings

        pname = getattr(active_llm, "provider_name", "")
        if settings.search_backend == "auto":
            settings.search_backend = settings.effective_search_backend(pname)
    except Exception:
        pass
    emitter = EventEmitter()
    store = RunStore(query=query, emitter=emitter)
    # Display subscribes to the SAME emitter the graph/store publishes to.
    display = SwarmDisplay(query, emitter, enabled=not no_tui) if not no_tui else None
    if display is not None:
        display.start()
    try:
        graph = build_graph(active_llm, store, display=display)
        final_state = await graph.ainvoke({"query": query})
        return final_state.get("final_markdown") or ""
    finally:
        if display is not None:
            display.stop()
        # Emit a budget summary line to the run log.
        from .runtime.budget import get_budget

        usage = get_budget().usage()
        store.emit(
            "budget",
            f"llm_calls={usage.llm_calls} searches={usage.search_calls} "
            f"fetches={usage.fetched_urls} elapsed={usage.elapsed_seconds}s"
            + (f" (stopped: {usage.reason})" if usage.exhausted else ""),
            level="warn" if usage.exhausted else "ok",
        )
        store.close()


def run_swarm_sync(
    query: str,
    dry_run: bool = False,
    provider: str | None = None,
    llm: LLMProvider | None = None,
) -> str:
    """Synchronous wrapper (no TUI) for use inside LuckyD tools/threads."""
    return asyncio.run(run_swarm(query, dry_run=dry_run, no_tui=True, provider=provider, llm=llm))
