"""P2: eval harness gates — per-task timeouts + regression verdicts.

A hung agent used to hang the whole suite (timeout_sec was decorative),
and baseline comparisons never failed a build. Tasks now time out
individually; --fail-on-regression exits 2; --save-baseline persists.
"""

from __future__ import annotations

import asyncio
import json

from tests.benchmarks import (
    ALL_SUITES,
    BenchmarkReport,
    BenchmarkRunner,
    BenchmarkTask,
    TaskResult,
    main,
    regression_exit_code,
)


def _report(pairs):
    results = [TaskResult(task_id=tid, success=ok, output="", latency_ms=1.0) for tid, ok in pairs]
    ok = sum(1 for _, s in pairs if s)
    return BenchmarkReport(
        suite_name="t",
        total_tasks=len(pairs),
        succeeded=ok,
        failed=len(pairs) - ok,
        success_rate=ok / len(pairs),
        total_latency_ms=1.0,
        avg_latency_ms=1.0,
        total_tokens=0,
        total_cost_usd=0.0,
        avg_quality_score=0.0,
        results=results,
    )


class TestTimeout:
    async def test_hung_agent_fails_task_not_suite(self, tmp_path):
        async def _hang(prompt):
            await asyncio.sleep(30)
            return "never"

        runner = BenchmarkRunner(agent_fn=_hang, output_dir=str(tmp_path))
        task = BenchmarkTask(
            id="slow",
            name="slow",
            description="",
            prompt="go",
            timeout_sec=1,
        )
        result = await runner._run_task(task)
        assert result.success is False
        assert "Timed out" in (result.error or "")

    async def test_fast_agent_unaffected(self, tmp_path):
        async def _quick(prompt):
            return "done marker"

        runner = BenchmarkRunner(agent_fn=_quick, output_dir=str(tmp_path))
        task = BenchmarkTask(
            id="fast",
            name="fast",
            description="",
            prompt="go",
            expected_output="marker",
            timeout_sec=30,
        )
        result = await runner._run_task(task)
        assert result.success is True


class TestRegressionVerdict:
    def test_exit_codes(self):
        assert regression_exit_code(None) == 0
        assert regression_exit_code({"regressions": []}) == 0
        assert regression_exit_code({"regressions": [{"task_id": "x"}]}) == 2

    def test_compare_spots_regression(self):
        base = _report([("a", True), ("b", True)])
        now = _report([("a", True), ("b", False)])
        comparison = now.compare_to(base)
        assert [r["task_id"] for r in comparison["regressions"]] == ["b"]
        assert regression_exit_code(comparison) == 2

    def test_baseline_roundtrip(self, tmp_path):
        runner = BenchmarkRunner(output_dir=str(tmp_path))
        report = _report([("a", True)])
        runner.save_report(report, "base.json")
        loaded = runner.load_baseline("base.json")
        assert loaded is not None and loaded.succeeded == 1
        assert loaded.compare_to(report)["regressions"] == []


class TestCli:
    async def test_simulate_run_saves_baseline(self, tmp_path, capsys):
        rc = await main(
            [
                "--suite",
                "quick",
                "--output",
                str(tmp_path),
                "--save-baseline",
                "base",
            ]
        )
        assert rc == 0
        data = json.loads((tmp_path / "base.json").read_text(encoding="utf-8"))
        assert data["suite_name"] == "quick"
        assert data["total_tasks"] == len(ALL_SUITES["quick"].tasks)

    async def test_fail_on_regression_clean(self, tmp_path, capsys):
        runner = BenchmarkRunner(output_dir=str(tmp_path))
        runner.save_report(_report([("nope", True)]), "other.json")
        rc = await main(
            [
                "--suite",
                "quick",
                "--output",
                str(tmp_path),
                "--baseline",
                "other.json",
                "--fail-on-regression",
            ]
        )
        assert rc == 0  # disjoint task ids: nothing to regress
