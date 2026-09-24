"""P2: plan approval gate linked to todos.

ExitPlanMode now blocks writes until the user approves; ApprovePlan
marks the artifact approved and turns its steps into chained tracked
tasks (step N blocked by N-1, enforced by the P1 ordering gate).
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import tools.file_tools  # noqa: F401 (register Write for the gate test)
import tools.plan_tools as plans
import tools.task_tools as tasks


@pytest.fixture
def _clean_state(monkeypatch, tmp_path):
    monkeypatch.setattr(plans, "_plan_mode", False)
    monkeypatch.setattr(plans, "_plan_context", {})
    monkeypatch.setattr(plans, "_awaiting_approval", False)
    monkeypatch.setattr(plans, "DATA_DIR", tmp_path)
    monkeypatch.setattr(tasks, "TASKS_DIR", tmp_path)
    yield tmp_path
    monkeypatch.setattr(plans, "_plan_mode", False)
    monkeypatch.setattr(plans, "_plan_context", {})
    monkeypatch.setattr(plans, "_awaiting_approval", False)


def _make_agent():
    from core.agent_loop import CodingAgent

    with patch("llm.ProviderRouter"):
        ag = CodingAgent(api_key="test-key-not-a-secret", model="test-model", temperature=0.0)
    ag._result_handler = None
    return ag


PLAN = "## Proposed plan\n\n1. Add the parser\n2. Wire the caller\n- [ ] Update docs\n"


class TestApproveFlow:
    async def test_exit_blocks_writes_until_approved(self, _clean_state):
        from tools.plan_tools import ApprovePlanTool, ExitPlanModeTool

        out = await ExitPlanModeTool().execute(plan=PLAN)
        assert plans.is_awaiting_approval() is True
        assert "ApprovePlan" in out.text

        ag = _make_agent()
        blocked = await ag._execute_tool("Write", {"_id": "w1"})
        assert blocked["content"].startswith("Error")
        assert "awaiting user approval" in blocked["content"]

        ok = await ApprovePlanTool().execute()
        assert ok.error is False
        assert plans.is_awaiting_approval() is False
        assert "unblocked" in ok.text

    async def test_approve_creates_chained_tasks(self, _clean_state):
        from tools.plan_tools import ApprovePlanTool, ExitPlanModeTool

        await ExitPlanModeTool().execute(plan=PLAN)
        out = await ApprovePlanTool().execute(note="ship it")
        assert out.error is False
        assert len(out.metadata["task_ids"]) == 3
        stored = json.loads((_clean_state / "tasks.json").read_text(encoding="utf-8"))
        by_id = {t["id"]: t for t in stored}
        ordered = out.metadata["task_ids"]
        assert by_id[ordered[1]]["blocked_by"] == [ordered[0]]
        assert by_id[ordered[2]]["blocked_by"] == [ordered[1]]
        sidecars = list((_clean_state / "plans").glob("*.approved"))
        assert len(sidecars) == 1
        assert "ship it" in sidecars[0].read_text(encoding="utf-8")

    async def test_double_approve_is_error(self, _clean_state):
        from tools.plan_tools import ApprovePlanTool, ExitPlanModeTool

        await ExitPlanModeTool().execute(plan=PLAN)
        await ApprovePlanTool().execute()
        again = await ApprovePlanTool().execute()
        assert again.error is True

    async def test_reads_and_plan_tools_exempt(self, _clean_state):
        from tools.plan_tools import ExitPlanModeTool

        await ExitPlanModeTool().execute(plan=PLAN)
        ag = _make_agent()
        # ApprovePlan itself must stay callable while awaiting approval.
        out = await ag._execute_tool("ApprovePlan", {"_id": "a1"})
        assert "awaiting user approval" not in out["content"]


class TestStepExtraction:
    def test_bullets_numbers_checkboxes(self):
        md = "## Proposed plan\n\n- first\n* second\n3. third\n- [x] fourth\n"
        assert plans._extract_steps(md) == ["first", "second", "third", "fourth"]

    def test_other_sections_ignored(self):
        md = "## Files examined\n\n- a.py\n\n## Proposed plan\n\n- do it\n"
        assert plans._extract_steps(md) == ["do it"]

    def test_no_plan_section_yields_nothing(self):
        assert plans._extract_steps("just prose\n- not a step\n") == []
