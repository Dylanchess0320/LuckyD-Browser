"""Planner: decompose the query into focused research tasks."""

from __future__ import annotations

from ..prompts import PLANNER_SYSTEM, PLANNER_USER
from ..runtime.run_store import RunStore
from ..schemas import ResearchPlan
from ..tools.search_base import LLMProvider


def run_planner(llm: LLMProvider, query: str, store: RunStore) -> ResearchPlan:
    store.emit("planner", "decomposing query into research tasks")
    plan = llm.structured(
        role="planner",
        system=PLANNER_SYSTEM,
        user=PLANNER_USER.format(query=query),
        schema=ResearchPlan,
        temperature=0.4,
    )
    store.save_plan(plan)
    store.emit("planner", f"{len(plan.tasks)} tasks planned", level="ok")
    return plan
