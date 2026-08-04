"""
Benchmark harness — measure agent performance on standard coding tasks.

Runs a suite of tasks against the agent and measures:
  - Success rate (did it complete the task?)
  - Latency (how long did it take?)
  - Token usage (how much did it cost?)
  - Quality score (self-reflection rating)
  - Regression detection (compare against baseline)

Usage:
    python -m tests.benchmarks --suite quick
    python -m tests.benchmarks --suite full --baseline results/baseline.json
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class BenchmarkTask:
    """A single benchmark task."""

    id: str
    name: str
    description: str
    prompt: str
    expected_output: str | None = None  # If set, check for exact/substring match
    expected_files: list[str] = field(default_factory=list)  # Files that should be created/modified
    timeout_sec: int = 60
    tags: list[str] = field(default_factory=list)  # e.g., ["code_gen", "refactor", "debug"]
    difficulty: str = "medium"  # easy, medium, hard


@dataclass
class TaskResult:
    """Result of running a single benchmark task."""

    task_id: str
    success: bool
    output: str
    latency_ms: float
    tokens_used: int = 0
    cost_usd: float = 0.0
    quality_score: float = 0.0
    error: str | None = None
    reflection_score: float | None = None


@dataclass
class BenchmarkSuite:
    """A collection of benchmark tasks."""

    name: str
    tasks: list[BenchmarkTask]
    description: str = ""


@dataclass
class BenchmarkReport:
    """Aggregated results from running a benchmark suite."""

    suite_name: str
    total_tasks: int
    succeeded: int
    failed: int
    success_rate: float
    total_latency_ms: float
    avg_latency_ms: float
    total_tokens: int
    total_cost_usd: float
    avg_quality_score: float
    results: list[TaskResult] = field(default_factory=list)
    timestamp: str = ""

    def compare_to(self, baseline: BenchmarkReport) -> dict[str, Any]:
        """Compare this report to a baseline, identifying regressions."""
        comparison = {
            "success_rate_delta": self.success_rate - baseline.success_rate,
            "latency_delta_ms": self.avg_latency_ms - baseline.avg_latency_ms,
            "cost_delta_usd": self.total_cost_usd - baseline.total_cost_usd,
            "quality_delta": self.avg_quality_score - baseline.avg_quality_score,
            "regressions": [],
            "improvements": [],
        }

        # Per-task comparison
        baseline_by_id = {r.task_id: r for r in baseline.results}
        for result in self.results:
            baseline_result = baseline_by_id.get(result.task_id)
            if baseline_result:
                if baseline_result.success and not result.success:
                    comparison["regressions"].append(
                        {
                            "task_id": result.task_id,
                            "issue": "was passing, now failing",
                            "error": result.error,
                        }
                    )
                elif not baseline_result.success and result.success:
                    comparison["improvements"].append(
                        {
                            "task_id": result.task_id,
                            "issue": "was failing, now passing",
                        }
                    )
                elif result.latency_ms > baseline_result.latency_ms * 2:
                    comparison["regressions"].append(
                        {
                            "task_id": result.task_id,
                            "issue": f"2x slower ({baseline_result.latency_ms:.0f}ms → {result.latency_ms:.0f}ms)",
                        }
                    )

        return comparison


# ── Pre-built Benchmark Suites ─────────────────────────────────────────

QUICK_SUITE = BenchmarkSuite(
    name="quick",
    description="Fast smoke tests — 5 tasks, ~2 minutes",
    tasks=[
        BenchmarkTask(
            id="q1_read",
            name="Read a file",
            description="Read a Python file and report its contents",
            prompt="Read the file core/config.py and tell me the first 3 settings defined",
            expected_output=None,
            timeout_sec=10,
            tags=["file_io", "read"],
            difficulty="easy",
        ),
        BenchmarkTask(
            id="q2_grep",
            name="Search for pattern",
            description="Find all function definitions in a file",
            prompt="Use Grep to find all 'def ' lines in core/agent.py",
            expected_output="def ",
            timeout_sec=10,
            tags=["search", "grep"],
            difficulty="easy",
        ),
        BenchmarkTask(
            id="q3_math",
            name="Simple calculation",
            description="Do a basic math operation",
            prompt="What is 17 * 23 + 456? Just give me the number.",
            expected_output="847",
            timeout_sec=10,
            tags=["reasoning", "math"],
            difficulty="easy",
        ),
        BenchmarkTask(
            id="q4_write",
            name="Write a file",
            description="Create a simple Python file",
            prompt="Write a file called /tmp/test_benchmark.py with a function that returns 'hello'",
            expected_files=["/tmp/test_benchmark.py"],
            timeout_sec=15,
            tags=["file_io", "write"],
            difficulty="easy",
        ),
        BenchmarkTask(
            id="q5_list",
            name="List directory",
            description="List files in a directory",
            prompt="Use Glob to list all .py files in the core/ directory",
            expected_output=".py",
            timeout_sec=10,
            tags=["file_io", "list"],
            difficulty="easy",
        ),
    ],
)

CODE_GEN_SUITE = BenchmarkSuite(
    name="code_gen",
    description="Code generation tasks — write functions, classes, scripts",
    tasks=[
        BenchmarkTask(
            id="cg1_fibonacci",
            name="Fibonacci function",
            description="Write a fibonacci function",
            prompt="Write a Python function `fibonacci(n)` that returns the nth Fibonacci number. Include memoization. Save it to /tmp/fib.py",
            expected_files=["/tmp/fib.py"],
            timeout_sec=30,
            tags=["code_gen", "algorithms"],
            difficulty="easy",
        ),
        BenchmarkTask(
            id="cg2_api_client",
            name="Simple API client",
            description="Write a basic HTTP API client class",
            prompt="Write a Python class `APIClient` with methods get/post/put/delete using the `requests` library. Include error handling. Save to /tmp/api_client.py",
            expected_files=["/tmp/api_client.py"],
            timeout_sec=45,
            tags=["code_gen", "api"],
            difficulty="medium",
        ),
        BenchmarkTask(
            id="cg3_sql_schema",
            name="SQL schema design",
            description="Design a database schema for a blog",
            prompt="Write SQL CREATE TABLE statements for a blog database with users, posts, comments, and tags. Include foreign keys and indexes. Save to /tmp/schema.sql",
            expected_files=["/tmp/schema.sql"],
            timeout_sec=30,
            tags=["code_gen", "sql"],
            difficulty="medium",
        ),
        BenchmarkTask(
            id="cg4_async_scraper",
            name="Async web scraper",
            description="Write an async web scraper",
            prompt="Write an async Python function that fetches multiple URLs concurrently using aiohttp, with a semaphore limit of 5. Save to /tmp/scraper.py",
            expected_files=["/tmp/scraper.py"],
            timeout_sec=45,
            tags=["code_gen", "async"],
            difficulty="hard",
        ),
    ],
)

DEBUG_SUITE = BenchmarkSuite(
    name="debug",
    description="Debugging tasks — find and fix bugs",
    tasks=[
        BenchmarkTask(
            id="db1_off_by_one",
            name="Off-by-one error",
            description="Fix an off-by-one error in a loop",
            prompt="This code has a bug:\n\n```python\ndef sum_first_n(n):\n    total = 0\n    for i in range(1, n):\n        total += i\n    return total\n```\n\nIt should sum 1 to n inclusive. Fix it and explain the bug.",
            expected_output="range(1, n + 1)",
            timeout_sec=15,
            tags=["debug", "logic"],
            difficulty="easy",
        ),
        BenchmarkTask(
            id="db2_race_condition",
            name="Race condition",
            description="Identify and fix a race condition",
            prompt="This async code has a race condition:\n\n```python\nimport asyncio\ncounter = 0\n\nasync def increment():\n    global counter\n    temp = counter\n    await asyncio.sleep(0)\n    counter = temp + 1\n\nasync def main():\n    await asyncio.gather(*[increment() for _ in range(100)])\n    print(counter)  # Expected 100, often less\n```\n\nFix it using asyncio.Lock.",
            expected_output="asyncio.Lock",
            timeout_sec=30,
            tags=["debug", "async", "concurrency"],
            difficulty="hard",
        ),
    ],
)

REFACTOR_SUITE = BenchmarkSuite(
    name="refactor",
    description="Refactoring tasks — improve code quality",
    tasks=[
        BenchmarkTask(
            id="rf1_extract_function",
            name="Extract function",
            description="Extract repeated code into a function",
            prompt="Refactor this code to extract the validation logic into a reusable function:\n\n```python\ndef create_user(data):\n    if not data.get('name') or len(data['name']) < 2:\n        raise ValueError('Invalid name')\n    if not data.get('email') or '@' not in data['email']:\n        raise ValueError('Invalid email')\n    # ... create user\n\ndef update_user(data):\n    if not data.get('name') or len(data['name']) < 2:\n        raise ValueError('Invalid name')\n    if not data.get('email') or '@' not in data['email']:\n        raise ValueError('Invalid email')\n    # ... update user\n```",
            expected_output="def validate",
            timeout_sec=30,
            tags=["refactor", "dry"],
            difficulty="medium",
        ),
    ],
)

ALL_SUITES = {
    "quick": QUICK_SUITE,
    "code_gen": CODE_GEN_SUITE,
    "debug": DEBUG_SUITE,
    "refactor": REFACTOR_SUITE,
}


# ── Benchmark Runner ───────────────────────────────────────────────────


class BenchmarkRunner:
    """Run benchmark suites and generate reports.

    Usage:
        runner = BenchmarkRunner(agent_fn=my_agent.run)
        report = await runner.run_suite("quick")
        runner.save_report(report, "results/quick_baseline.json")
    """

    def __init__(
        self,
        agent_fn: Callable | None = None,
        reflection_engine=None,
        output_dir: str = "benchmark_results",
    ):
        self.agent_fn = agent_fn
        self.reflection_engine = reflection_engine
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def run_suite(self, suite_name: str) -> BenchmarkReport:
        """Run all tasks in a benchmark suite."""
        suite = ALL_SUITES.get(suite_name)
        if suite is None:
            raise ValueError(f"Unknown suite: {suite_name}. Available: {list(ALL_SUITES.keys())}")

        results: list[TaskResult] = []

        for task in suite.tasks:
            print(f"  [{task.id}] {task.name}...", end=" ", flush=True)
            result = await self._run_task(task)
            results.append(result)
            status = "✓" if result.success else "✗"
            print(f"{status} ({result.latency_ms:.0f}ms)")

        # Aggregate
        succeeded = sum(1 for r in results if r.succeeded)
        total_latency = sum(r.latency_ms for r in results)
        total_tokens = sum(r.tokens_used for r in results)
        total_cost = sum(r.cost_usd for r in results)
        quality_scores = [r.quality_score for r in results if r.quality_score > 0]

        report = BenchmarkReport(
            suite_name=suite_name,
            total_tasks=len(results),
            succeeded=succeeded,
            failed=len(results) - succeeded,
            success_rate=succeeded / len(results) if results else 0,
            total_latency_ms=total_latency,
            avg_latency_ms=total_latency / len(results) if results else 0,
            total_tokens=total_tokens,
            total_cost_usd=total_cost,
            avg_quality_score=sum(quality_scores) / len(quality_scores) if quality_scores else 0,
            results=results,
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
        )

        return report

    async def _run_task(self, task: BenchmarkTask) -> TaskResult:
        """Run a single benchmark task."""
        start = time.monotonic()

        try:
            if self.agent_fn is None:
                # No agent — simulate
                return TaskResult(
                    task_id=task.id,
                    success=True,
                    output="[simulated]",
                    latency_ms=0,
                    error=None,
                )

            # Run the agent
            output = await self._call_agent(task.prompt)
            latency = (time.monotonic() - start) * 1000

            # Check success
            success = self._check_success(task, output)

            # Optional: run reflection
            reflection_score = None
            if self.reflection_engine:
                try:
                    reflection = await self.reflection_engine.reflect(task.prompt, output)
                    reflection_score = reflection.overall_score
                except Exception:
                    pass

            return TaskResult(
                task_id=task.id,
                success=success,
                output=output[:500],  # Truncate for report
                latency_ms=latency,
                reflection_score=reflection_score,
            )

        except Exception as e:
            latency = (time.monotonic() - start) * 1000
            return TaskResult(
                task_id=task.id,
                success=False,
                output="",
                latency_ms=latency,
                error=f"{type(e).__name__}: {e}",
            )

    async def _call_agent(self, prompt: str) -> str:
        """Call the agent function."""
        if self.agent_fn is None:
            return "[no agent configured]"

        result = self.agent_fn(prompt)
        if asyncio.iscoroutine(result):
            return await result
        return str(result)

    def _check_success(self, task: BenchmarkTask, output: str) -> bool:
        """Check if a task was completed successfully."""
        # Check expected output (substring match)
        if task.expected_output and task.expected_output.lower() not in output.lower():
            return False

        # Check expected files
        return all(Path(file_path).exists() for file_path in task.expected_files)

    def save_report(self, report: BenchmarkReport, filename: str | None = None):
        """Save a benchmark report to JSON."""
        if filename is None:
            filename = f"{report.suite_name}_{time.strftime('%Y%m%d_%H%M%S')}.json"

        path = self.output_dir / filename
        data = asdict(report)
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        print(f"\nReport saved to {path}")

    def load_baseline(self, filename: str) -> BenchmarkReport | None:
        """Load a baseline report for comparison."""
        path = self.output_dir / filename
        if not path.exists():
            return None

        data = json.loads(path.read_text(encoding="utf-8"))
        # Reconstruct TaskResult objects
        results = [TaskResult(**r) for r in data.pop("results", [])]
        return BenchmarkReport(results=results, **data)


# ── CLI ────────────────────────────────────────────────────────────────


async def main():
    """CLI entry point for running benchmarks."""
    import argparse

    parser = argparse.ArgumentParser(description="Run agent benchmarks")
    parser.add_argument(
        "--suite", default="quick", choices=list(ALL_SUITES.keys()), help="Benchmark suite to run"
    )
    parser.add_argument("--baseline", help="Baseline report JSON for comparison")
    parser.add_argument(
        "--output", default="benchmark_results", help="Output directory for reports"
    )
    args = parser.parse_args()

    print(f"Running benchmark suite: {args.suite}")
    print(f"Tasks: {len(ALL_SUITES[args.suite].tasks)}")
    print()

    runner = BenchmarkRunner(output_dir=args.output)
    report = await runner.run_suite(args.suite)

    print(f"\n{'='*50}")
    print(f"Results: {report.succeeded}/{report.total_tasks} passed ({report.success_rate:.0%})")
    print(f"Avg latency: {report.avg_latency_ms:.0f}ms")
    print(f"Total cost: ${report.total_cost_usd:.4f}")
    if report.avg_quality_score > 0:
        print(f"Avg quality: {report.avg_quality_score:.2f}")

    # Compare to baseline
    if args.baseline:
        baseline = runner.load_baseline(args.baseline)
        if baseline:
            comparison = report.compare_to(baseline)
            print("\nBaseline comparison:")
            print(f"  Success rate: {comparison['success_rate_delta']:+.0%}")
            print(f"  Latency: {comparison['latency_delta_ms']:+.0f}ms")
            if comparison["regressions"]:
                print(f"  ⚠ Regressions: {len(comparison['regressions'])}")
                for reg in comparison["regressions"]:
                    print(f"    - {reg['task_id']}: {reg['issue']}")
            if comparison["improvements"]:
                print(f"  ✓ Improvements: {len(comparison['improvements'])}")

    runner.save_report(report)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())


import asyncio
