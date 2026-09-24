"""A6: trajectory log + YAML evals, report-first.

- ``core/trajectory.py`` records an opt-in JSONL audit trail of agent
  runs (turn/tool/model events; token-chunk spam excluded).
- Eval tasks live as YAML in tests/evals/ and load into the existing
  benchmarks harness; CI runs the harness smoke (no live model needed)
  and keeps live-model evals a manual, report-only step.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

EVALS_DIR = Path(__file__).parent / "evals"


def _make_agent(**kwargs):
    with patch("llm.ProviderRouter"):
        from core.agent_loop import CodingAgent

        ag = CodingAgent(api_key="test-key-not-a-secret", model="test-model", **kwargs)

    async def _noop_extract(user_message):
        return None

    async def _noop_refresh(query):
        return False

    ag._extract_session_memories = _noop_extract
    ag._refresh_memory_context = _noop_refresh
    return ag


def _event(type_name, turn=1, payload=None):
    from core.types import AgentEvent, AgentEventType

    return AgentEvent(type=AgentEventType(type_name), payload=payload or {}, turn=turn)


class FakeLLMClient:
    def __init__(self, script):
        self._script = list(script)
        self.model = "fake-model"

    async def chat_stream(self, *args, **kwargs):
        if not self._script:
            return {"content": "default final answer"}
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class TestRecorder:
    def test_disabled_by_default(self, tmp_path, monkeypatch):
        import core.trajectory as traj

        monkeypatch.delenv("CODING_AGENT_TRAJECTORY", raising=False)
        monkeypatch.setattr(traj, "DATA_DIR", tmp_path)
        assert traj.TrajectoryRecorder.attach_if_enabled(_make_agent()) is None
        assert list(tmp_path.iterdir()) == []

    def test_records_events_as_jsonl(self, tmp_path):
        from core.trajectory import TrajectoryRecorder

        rec = TrajectoryRecorder(tmp_path / "t.jsonl")
        rec.record(_event("turn_start", turn=2, payload={"x": 1}))
        rec.record(_event("model_chunk", turn=2, payload={"t": "spam"}))
        rec.record(_event("tool_start", turn=2, payload={"tool": "Read"}))
        rec.close()
        rows = [json.loads(line) for line in (tmp_path / "t.jsonl").read_text().splitlines()]
        kinds = [r["type"] for r in rows]
        assert kinds == ["turn_start", "tool_start"]  # chunk spam skipped
        assert rows[0]["turn"] == 2
        assert rows[1]["payload"] == {"tool": "Read"}

    def test_session_end_finalizes_and_detaches(self, tmp_path):
        from core.trajectory import TrajectoryRecorder

        ag = _make_agent()
        seen = []
        prev = seen.append
        ag.callbacks.on_event = prev
        rec = TrajectoryRecorder.attach(ag, tmp_path / "t.jsonl")
        assert ag.callbacks.on_event is not prev  # chained
        ag.callbacks.on_event(_event("session_end", payload={"ok": True}))
        assert len(seen) == 1  # previous callback preserved
        assert ag.callbacks.on_event is prev  # detached
        assert rec.closed is True

    async def test_run_writes_trajectory_when_enabled(self, tmp_path, monkeypatch):
        import core.trajectory as traj

        monkeypatch.setenv("CODING_AGENT_TRAJECTORY", "1")
        monkeypatch.setattr(traj, "DATA_DIR", tmp_path)
        ag = _make_agent()
        ag.llm_client = FakeLLMClient([{"content": "done and complete."}])
        await ag.run("go", max_turns=2)
        files = list((tmp_path / "trajectories").glob("*.jsonl"))
        assert len(files) == 1
        kinds = [json.loads(line)["type"] for line in files[0].read_text().splitlines()]
        assert "session_start" in kinds
        assert "session_end" in kinds


class TestEvalYaml:
    def test_evals_load_into_suite(self):
        from tests.benchmarks import load_suite_from_yaml

        suite = load_suite_from_yaml(EVALS_DIR)
        assert suite.name == "evals"
        assert len(suite.tasks) >= 3
        by_id = {t.id: t for t in suite.tasks}
        assert by_id["reply-ok"].expected_output == "OK"
        assert all(t.prompt.strip() for t in suite.tasks)

    async def test_harness_smoke_simulate(self, tmp_path):
        from tests.benchmarks import BenchmarkRunner

        runner = BenchmarkRunner(output_dir=str(tmp_path))
        report = await runner.run_suite("quick")
        assert report.total_tasks > 0
        assert report.succeeded == report.total_tasks  # simulate mode
        runner.save_report(report, "smoke.json")
        assert (tmp_path / "smoke.json").exists()
