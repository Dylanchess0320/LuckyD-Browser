"""P1: ExitPlanMode persists the plan as a markdown artifact.

The approved plan used to live only in chat history; it is now written
to data/plans/plan_<stamp>.md (with exploration context) and its path
is reported + returned in metadata.
"""

from __future__ import annotations

import pytest

import tools.plan_tools as plans
from tools.plan_tools import EnterPlanModeTool, ExitPlanModeTool


@pytest.fixture
def _clean_plan_state(monkeypatch):
    monkeypatch.setattr(plans, "_plan_mode", False)
    monkeypatch.setattr(plans, "_plan_context", {})
    monkeypatch.setattr(plans, "_awaiting_approval", False)
    yield
    monkeypatch.setattr(plans, "_plan_mode", False)
    monkeypatch.setattr(plans, "_plan_context", {})
    monkeypatch.setattr(plans, "_awaiting_approval", False)


class TestPlanArtifact:
    async def test_exit_writes_plan_file(self, tmp_path, monkeypatch, _clean_plan_state):
        monkeypatch.setattr(plans, "DATA_DIR", tmp_path)
        await EnterPlanModeTool().execute()
        out = await ExitPlanModeTool().execute(plan="## Steps\n1. Ship it")
        assert out.error is False
        assert plans.is_plan_mode() is False
        files = list((tmp_path / "plans").glob("plan_*.md"))
        assert len(files) == 1
        text = files[0].read_text(encoding="utf-8")
        assert "## Steps" in text
        assert "1. Ship it" in text
        assert out.metadata["plan_path"] == str(files[0])
        assert "Plan saved to:" in out.text

    async def test_failed_write_still_exits(self, tmp_path, monkeypatch, _clean_plan_state):
        monkeypatch.setattr(plans, "DATA_DIR", tmp_path)
        blocker = tmp_path / "plans"
        blocker.write_text("not a directory", encoding="utf-8")
        await EnterPlanModeTool().execute()
        out = await ExitPlanModeTool().execute(plan="whatever")
        assert out.error is False
        assert plans.is_plan_mode() is False
        assert "not saved" in out.text
        assert out.metadata["plan_path"] == ""
