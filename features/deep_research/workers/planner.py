"""Planner: decompose the query into focused research tasks."""

from __future__ import annotations

from ..prompts import PLANNER_SYSTEM, PLANNER_USER
from ..runtime.run_store import RunStore
from ..schemas import ResearchPlan
from ..tools.search_base import LLMProvider


def _fallback_plan(query: str) -> ResearchPlan:
    """Deterministic plan when the LLM can't produce valid structured output.

    Small local models (e.g. Ollama 3B) sometimes echo the JSON schema instead
    of data. Rather than failing the whole run, decompose heuristically.
    """
    from ..schemas import ResearchTask

    q = (query or "").strip() or "research topic"
    short = q if len(q) <= 120 else q[:117] + "..."
    return ResearchPlan(
        reasoning=(
            "Fallback decomposition: the planner LLM did not return valid "
            "structured output, so the query was split heuristically."
        ),
        tasks=[
            ResearchTask(
                id="t1",
                question=f"Overview and key facts: {short}",
                focus="overview",
                search_hints=[short],
            ),
            ResearchTask(
                id="t2",
                question=f"Details, comparisons and evidence: {short}",
                focus="details",
                search_hints=[f"{short} comparison", f"{short} evidence"],
            ),
            ResearchTask(
                id="t3",
                question=f"Recent developments and outlook: {short}",
                focus="recent news",
                search_hints=[f"{short} latest", f"{short} 2026"],
            ),
        ],
    )


def run_planner(llm: LLMProvider, query: str, store: RunStore) -> ResearchPlan:
    store.emit("planner", "decomposing query into research tasks")
    try:
        plan = llm.structured(
            role="planner",
            system=PLANNER_SYSTEM,
            user=PLANNER_USER.format(query=query),
            schema=ResearchPlan,
            temperature=0.4,
        )
    except Exception as e:
        # Small/offline models may return schema-echo or invalid JSON.
        # Fall back deterministically so the swarm still researches.
        store.emit(
            "planner",
            f"structured planning failed ({type(e).__name__}); using fallback plan",
            level="warn",
        )
        plan = _fallback_plan(query)
    if not plan.tasks:
        store.emit("planner", "empty plan; using fallback plan", level="warn")
        plan = _fallback_plan(query)
    store.save_plan(plan)
    store.emit("planner", f"{len(plan.tasks)} tasks planned", level="ok")
    return plan
