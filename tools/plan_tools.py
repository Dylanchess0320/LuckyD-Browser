"""
Plan mode tools: EnterPlanMode and ExitPlanMode.
Enables read-only exploration phase before writing code.
"""

from __future__ import annotations

import contextlib
import re
from datetime import datetime, timezone
from pathlib import Path

from config import DATA_DIR

from .base import ToolBase, ToolOutput
from .registry import register_tool

# ── plan mode state ────────────────────────────────────────────────

_plan_mode: bool = False
_plan_context: dict = {}
_awaiting_approval: bool = False


def is_plan_mode() -> bool:
    return _plan_mode


def get_plan_context() -> dict:
    return _plan_context


def is_awaiting_approval() -> bool:
    """True after ExitPlanMode until ApprovePlan — writes stay blocked."""
    return _awaiting_approval


def _plans_dir() -> Path:
    return DATA_DIR / "plans"


def _save_plan_artifact(plan: str) -> Path | None:
    """Persist the proposed plan as markdown; None when the write fails."""
    try:
        target = _plans_dir()
        target.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        entered = _plan_context.get("entered_at", "")
        examined = _plan_context.get("files_examined", []) or []
        path = target / f"plan_{stamp}.md"
        path.write_text(
            f"# Plan ({stamp})\n\n"
            f"_Exploration started: {entered}_\n\n"
            f"## Files examined\n\n"
            + "".join(f"- {f}\n" for f in examined)
            + f"\n## Proposed plan\n\n{plan}\n",
            encoding="utf-8",
        )
        return path
    except Exception:
        return None


def _is_approved(path: Path) -> bool:
    return path.with_suffix(path.suffix + ".approved").exists()


def _resolve_plan(plan_file: Path) -> Path | None:
    """Resolve an explicit plan path; None when missing/already approved."""
    candidate = plan_file if plan_file.is_absolute() else _plans_dir() / plan_file.name
    if candidate.is_file() and not _is_approved(candidate):
        return candidate
    return None


def _newest_unapproved_plan() -> Path | None:
    """Newest plan_*.md without an approval sidecar, or None."""
    try:
        plans = sorted(
            _plans_dir().glob("plan_*.md"), key=lambda p: p.stat().st_mtime, reverse=True
        )
    except Exception:
        return None
    for plan in plans:
        if not _is_approved(plan):
            return plan
    return None


_STEP_LINE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+(?:\[[ xX]\]\s+)?(.+?)\s*$")


def _extract_steps(markdown: str) -> list[str]:
    """Candidate task steps: bullets/numbered lines under ## Proposed plan."""
    in_plan = False
    steps: list[str] = []
    for line in markdown.splitlines():
        if line.startswith("## "):
            in_plan = line.strip().lower() == "## proposed plan"
            continue
        if not in_plan:
            continue
        match = _STEP_LINE.match(line)
        if match and match.group(1):
            steps.append(match.group(1))
    return steps


class EnterPlanModeTool(ToolBase):
    name = "EnterPlanMode"
    description = "Enter plan mode: a read-only exploration and design phase BEFORE writing any code. In plan mode, you MUST NOT write or edit files. Explore the codebase, consider approaches, and when ready call ExitPlanMode."
    aliases = ["PlanMode", "DesignPhase"]
    parameters = {}

    async def execute(self) -> ToolOutput:
        global _plan_mode, _plan_context
        _plan_mode = True
        _plan_context = {
            "entered_at": datetime.now(timezone.utc).isoformat(),
            "files_examined": [],
        }
        return ToolOutput(
            text="""Plan mode ACTIVE. You are in a read-only exploration phase.

Rules:
  - DO NOT write or edit any files
  - Explore the codebase thoroughly
  - Identify similar features and architectural approaches
  - Consider multiple approaches and their trade-offs
  - Use AskUserQuestion to clarify requirements
  - Design a concrete implementation strategy
  - When ready, call ExitPlanMode to present the plan for approval""",
            title="Plan Mode Active",
            metadata={"plan_mode": True},
        )


class ExitPlanModeTool(ToolBase):
    name = "ExitPlanMode"
    description = "Exit plan mode and present your implementation plan to the user for approval. Only call when you have a complete, concrete plan ready."
    aliases = ["ExitPlan", "ProposePlan"]
    parameters = {
        "plan": {
            "type": "string",
            "description": "Your complete implementation plan. Be specific: list files, functions, and approaches.",
        },
    }

    async def execute(self, plan: str) -> ToolOutput:
        global _plan_mode, _awaiting_approval
        _plan_mode = False
        plan_path = _save_plan_artifact(plan)
        _awaiting_approval = True
        saved_note = f"\nPlan saved to: {plan_path}" if plan_path else "\n(Plan file not saved.)"
        return ToolOutput(
            text=(
                f"Plan mode EXITED. Here is the proposed plan:\n\n{plan}\n\n---\n"
                "Waiting for user approval before proceeding. "
                "Writes are blocked until the user approves and you call ApprovePlan."
                f"{saved_note}"
            ),
            title="Plan Proposed",
            metadata={"plan_mode": False, "plan": plan, "plan_path": str(plan_path or "")},
        )


class ApprovePlanTool(ToolBase):
    name = "ApprovePlan"
    description = (
        "Approve the proposed plan AFTER the user accepts it. Marks the plan "
        "approved (unblocking writes) and creates tracked tasks from its steps, "
        "chained in order. Only call once the user has said yes."
    )
    aliases = ["AcceptPlan", "ConfirmPlan"]
    parameters = {
        "plan_file": {
            "type": "string",
            "description": "Plan artifact to approve (defaults to the newest unapproved one)",
        },
        "note": {
            "type": "string",
            "description": "Approval note recorded alongside the plan (optional)",
        },
    }

    async def execute(self, plan_file: str = "", note: str = "") -> ToolOutput:
        global _awaiting_approval
        from tools.task_tools import TaskCreateTool

        target = _resolve_plan(Path(plan_file)) if plan_file else _newest_unapproved_plan()
        if target is None:
            _awaiting_approval = False
            return ToolOutput(text="No unapproved plan found — nothing to approve.", error=True)
        try:
            steps = _extract_steps(target.read_text(encoding="utf-8"))
        except Exception as exc:
            return ToolOutput(text=f"Could not read plan {target.name}: {exc}", error=True)
        created: list[str] = []
        prev: str | None = None
        for step in steps[:20]:
            out = await TaskCreateTool().execute(
                subject=step[:120], blocked_by=[prev] if prev else []
            )
            if out.error is False:
                prev = out.metadata["task_id"]
                created.append(prev)
        with contextlib.suppress(Exception):
            target.with_suffix(target.suffix + ".approved").write_text(
                f"Approved. {note}\n".strip() + "\n", encoding="utf-8"
            )
        _awaiting_approval = False
        return ToolOutput(
            text=(
                f"Plan approved: {target.name} "
                f"({len(created)} tracked tasks created, chained in order). "
                "Writes are unblocked."
            ),
            title="Plan Approved",
            metadata={"plan": str(target), "task_ids": created},
        )


register_tool(EnterPlanModeTool())
register_tool(ExitPlanModeTool())
register_tool(ApprovePlanTool())
