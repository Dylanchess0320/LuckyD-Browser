"""Deterministic eval for the CodingAgent loop (v10.7).

Measures the LOOP's reliability — not any model's reasoning — by driving the
real ``CodingAgent.run`` with a scripted deterministic LLM client that
returns canned tool-call sequences. The same scripts run before and after
loop changes, so scores are comparable.

Usage:
    python -m tests.coding_eval                          # run the suite
    python -m tests.coding_eval --save-baseline coding_before
    python -m tests.coding_eval --baseline coding_before.json --fail-on-regression

The suite (CODING_SUITE) covers:
  - code_*: real file-edit-and-verify tasks (create, edit, read->edit,
    read->report, grep, bash, multi-file, write+edit, todo round-trip)
  - rel_*: reliability scenarios: tool-error recovery, unknown-tool
    recovery, 200KB input integrity, oversize-input rejection
  - ctx_*: context-overflow behavior (many-turn task, no silent middle loss)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

# Intentionally fake API key for tests (not a real secret).
TEST_API_KEY = "eval-fake-key-not-a-secret"

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Auto-register built-in tools (populates the tool registry), mirroring agent.py.
import tools.agent_orchestration
import tools.ask_question_tool
import tools.bash_tool
import tools.brief_tool
import tools.browser_tools
import tools.browser_use_tool
import tools.config_tool
import tools.context_tools
import tools.data_tools
import tools.datetime_tools
import tools.deep_research_tool
import tools.delegate
import tools.desktop_tools
import tools.file_tools
import tools.git_tools
import tools.graphify_tool
import tools.harness_tool
import tools.lsp_tools
import tools.mcp_tools
import tools.memory_tools
import tools.minimax_cli
import tools.plan_tools
import tools.schedule_tools
import tools.session_tools
import tools.skill_market_tools
import tools.skill_tools
import tools.subagent_tool
import tools.task_tools
import tools.utility_tools
import tools.web_tools
import tools.webmcp_tools  # noqa: F401
from llm import LLMResult
from tests.benchmarks import (
    ALL_SUITES,
    BenchmarkRunner,
    BenchmarkSuite,
    BenchmarkTask,
)

SUMMARY_MARKERS = ("CONVERSATION SUMMARY SO FAR:", "Structural summary")


def _call(name: str, call_id: str, **kwargs) -> dict:
    """One OpenAI-style tool call dict for the scripted client."""
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(kwargs)},
    }


def _llm(text: str = "", calls: list[dict] | None = None) -> LLMResult:
    return LLMResult(content=text, tool_calls=calls)


class ScriptedLLM:
    """Deterministic stand-in for LLMClient.

    Returns canned tool-call sequences keyed by task id. When a script is
    exhausted it returns a plain "done" final message so the run ends
    instead of hanging.
    """

    def __init__(self, scripts: dict[str, list[LLMResult]]):
        self.scripts = scripts
        self.task_id: str | None = None
        self.calls: dict[str, int] = {}

    def set_task(self, task_id: str) -> None:
        self.task_id = task_id

    async def chat_stream(self, messages, tools=None, **kwargs) -> LLMResult:
        script = self.scripts.get(self.task_id or "", [])
        idx = self.calls.get(self.task_id or "", 0)
        self.calls[self.task_id or ""] = idx + 1
        if idx < len(script):
            return script[idx]
        return LLMResult(content="done")

    async def chat_nonstreaming(self, messages, **kwargs) -> dict:
        return {
            "content": (
                "Earlier turns created and edited files in the eval workspace; "
                "all tool calls succeeded."
            )
        }


def _make_scripted_agent() -> object:
    """A real CodingAgent with a scripted LLM client and no side effects."""
    with patch("llm.ProviderRouter"):
        from core.agent_loop import CodingAgent

        agent = CodingAgent(
            api_key=TEST_API_KEY,
            model="test-model",
            temperature=0.0,
            permission_mode="bypassPermissions",
        )
        # Memory extraction spawns a real background LLM call; neutralize it.
        agent._extract_session_memories = AsyncMock()
        return agent


def build_coding_suite(workspace: Path) -> tuple[BenchmarkSuite, dict[str, list[LLMResult]]]:
    """Build the coding suite and the matching per-task LLM scripts."""
    ws = str(workspace)
    hello_py = str(workspace / "hello.py")
    config_txt = str(workspace / "config.txt")
    app_py = str(workspace / "app.py")
    data_csv = str(workspace / "data.csv")
    a_txt = str(workspace / "a.txt")
    b_txt = str(workspace / "b.txt")
    notes_txt = str(workspace / "notes.txt")

    tasks: list[BenchmarkTask] = []
    scripts: dict[str, list[LLMResult]] = {}

    def add(task_id, name, description, prompt, script, **kwargs):
        tasks.append(
            BenchmarkTask(id=task_id, name=name, description=description, prompt=prompt, **kwargs)
        )
        scripts[task_id] = script

    # ── file edit / verify tasks ──────────────────────────────────────
    add(
        "code_create",
        "Create a file",
        "Write a Python file with exact content",
        f"Create the file {hello_py} with a hello() function returning 'hello'.",
        [
            _llm(
                calls=[
                    _call(
                        "Write",
                        "c1",
                        file_path=hello_py,
                        content='def hello():\n    return "hello"\n',
                    )
                ]
            ),
            _llm("Created hello.py with the hello() function."),
        ],
        expected_files=[hello_py],
        expected_file_contents={hello_py: "def hello():"},
        tags=["file_io", "write"],
        difficulty="easy",
    )
    add(
        "code_edit",
        "Edit a file",
        "Change one setting in an existing file",
        f"In {config_txt}, change 'version: 1' to 'version: 2'.",
        [
            _llm(
                calls=[
                    _call(
                        "Edit",
                        "c1",
                        file_path=config_txt,
                        old_string="version: 1",
                        new_string="version: 2",
                    )
                ]
            ),
            _llm("Bumped config.txt to version 2."),
        ],
        expected_files=[config_txt],
        expected_file_contents={config_txt: "version: 2"},
        tags=["file_io", "edit"],
        difficulty="easy",
    )
    add(
        "code_read_edit_verify",
        "Read, edit, verify",
        "Multi-step: read a file, edit it, confirm the change",
        f"Read {app_py}, change the GREETING constant from 'hi' to 'hello', then confirm.",
        [
            _llm(calls=[_call("Read", "c1", file_path=app_py)]),
            _llm(
                calls=[
                    _call(
                        "Edit",
                        "c2",
                        file_path=app_py,
                        old_string='GREETING = "hi"',
                        new_string='GREETING = "hello"',
                    )
                ]
            ),
            _llm("Greeting updated to hello in app.py."),
        ],
        expected_files=[app_py],
        expected_file_contents={app_py: 'GREETING = "hello"'},
        tags=["file_io", "read", "edit", "multistep"],
        difficulty="medium",
    )
    add(
        "code_read_report",
        "Read and report",
        "Read a file and report a fact from it",
        f"Read {data_csv} and tell me the values in the second row.",
        [
            _llm(calls=[_call("Read", "c1", file_path=data_csv)]),
            _llm("The second row of data.csv is: beta,20,active."),
        ],
        expected_output="beta",
        tags=["file_io", "read"],
        difficulty="easy",
    )
    add(
        "code_grep",
        "Search a pattern",
        "Find function definitions with Grep",
        f"Use Grep to find all 'def ' lines under {ws}.",
        [
            _llm(calls=[_call("Grep", "c1", pattern="def ", path=ws)]),
            _llm("Found definitions: compute_total and helper in main.py."),
        ],
        expected_output="compute_total",
        tags=["search", "grep"],
        difficulty="easy",
    )
    add(
        "code_bash",
        "Run a shell command",
        "Execute a deterministic command and report its output",
        "Run python3 to compute 40+2 and tell me the result.",
        [
            _llm(calls=[_call("Bash", "c1", command='python3 -c "print(40+2)"')]),
            _llm("The answer is 42."),
        ],
        expected_output="42",
        tags=["shell", "bash"],
        difficulty="easy",
    )
    add(
        "code_multi_file",
        "Create two files",
        "Two sequential writes in one task",
        f"Create {a_txt} containing 'alpha' and {b_txt} containing 'beta'.",
        [
            _llm(calls=[_call("Write", "c1", file_path=a_txt, content="alpha\n")]),
            _llm(calls=[_call("Write", "c2", file_path=b_txt, content="beta\n")]),
            _llm("Created both files."),
        ],
        expected_files=[a_txt, b_txt],
        expected_file_contents={a_txt: "alpha", b_txt: "beta"},
        tags=["file_io", "write", "multistep"],
        difficulty="medium",
    )
    add(
        "code_write_edit_same",
        "Write then edit one file",
        "Create a file and then append to it (checkpoint path)",
        f"Create {notes_txt} with 'todo: one', then append a 'done: one' line.",
        [
            _llm(calls=[_call("Write", "c1", file_path=notes_txt, content="todo: one\n")]),
            _llm(
                calls=[
                    _call(
                        "Edit",
                        "c2",
                        file_path=notes_txt,
                        old_string="todo: one\n",
                        new_string="todo: one\ndone: one\n",
                    )
                ]
            ),
            _llm("Notes updated."),
        ],
        expected_files=[notes_txt],
        expected_file_contents={notes_txt: "done: one"},
        tags=["file_io", "write", "edit", "multistep"],
        difficulty="medium",
    )
    add(
        "code_todo_roundtrip",
        "Todo write/read round-trip",
        "Track a todo item and read it back",
        "Add a todo 'Write the report' (in progress), then read the todo list back.",
        [
            _llm(
                calls=[
                    _call(
                        "TodoWrite",
                        "c1",
                        todos=[
                            {
                                "id": "t1",
                                "content": "Write the report",
                                "status": "in_progress",
                            }
                        ],
                    )
                ]
            ),
            _llm(calls=[_call("TodoRead", "c2")]),
            _llm("Todo list shows: Write the report (in_progress)."),
        ],
        expected_output="Write the report",
        tags=["planning", "todo"],
        difficulty="easy",
    )

    # ── reliability scenarios ─────────────────────────────────────────
    add(
        "rel_tool_error_recovery",
        "Tool-error recovery",
        "A failing tool call must not end the task; try a different approach",
        f"Write the file {ws}/recovered.txt with the text 'recovered'.",
        [
            # Turn 1: missing 'content' argument -> tool argument error.
            _llm(
                calls=[_call("Write", "c1", file_path=f"{ws}/recovered.txt")],
            ),
            # Turn 2: corrected call after the loop surfaces the error.
            _llm(
                calls=[
                    _call(
                        "Write",
                        "c2",
                        file_path=f"{ws}/recovered.txt",
                        content="recovered\n",
                    )
                ]
            ),
            _llm("Recovered from the argument error and wrote the file."),
        ],
        expected_files=[f"{ws}/recovered.txt"],
        expected_file_contents={f"{ws}/recovered.txt": "recovered"},
        tags=["reliability", "error_recovery"],
        difficulty="medium",
    )
    add(
        "rel_unknown_tool",
        "Unknown-tool recovery",
        "Calling a nonexistent tool must produce a clear error, then recover",
        f"Write the file {ws}/fixed.txt with the text 'fixed'.",
        [
            _llm(calls=[_call("Writee", "c1", file_path=f"{ws}/fixed.txt")]),
            _llm(calls=[_call("Write", "c2", file_path=f"{ws}/fixed.txt", content="fixed\n")]),
            _llm("Used the correct Write tool after the typo."),
        ],
        expected_files=[f"{ws}/fixed.txt"],
        expected_file_contents={f"{ws}/fixed.txt": "fixed"},
        tags=["reliability", "error_recovery"],
        difficulty="medium",
    )
    big_input = "LARGE-INPUT-MARKER " + "x" * 200_000
    add(
        "rel_large_input",
        "200KB input integrity",
        "A 200KB user message must arrive in the loop intact (no corruption)",
        big_input,
        [_llm("ok")],
        expected_output="INTACT_OK",
        timeout_sec=180,
        tags=["reliability", "large_input"],
        difficulty="medium",
    )
    add(
        "rel_oversize_rejected",
        "Oversize input rejected",
        "A 2MB user message must be rejected with a clear error, never corrupted",
        "OVERSIZE-MARKER " + "y" * 2_000_000,
        [_llm("ok")],
        expected_output="too large",
        timeout_sec=180,
        tags=["reliability", "large_input"],
        difficulty="medium",
    )

    # ── context overflow ──────────────────────────────────────────────
    overflow_script: list[LLMResult] = []
    for i in range(22):
        overflow_script.append(
            _llm(
                calls=[
                    _call(
                        "Write",
                        f"w{i}",
                        file_path=f"{ws}/ctx_{i:02d}.txt",
                        content=f"chunk {i}\n",
                    )
                ]
            )
        )
    overflow_script.append(_llm("All 22 chunks written."))
    add(
        "ctx_overflow",
        "Context overflow",
        "A 22-turn task must complete with the middle history summarized, never silently dropped",
        "Write 22 small chunk files ctx_00.txt through ctx_21.txt into " + ws,
        overflow_script,
        expected_files=[f"{ws}/ctx_00.txt", f"{ws}/ctx_21.txt"],
        expected_file_contents={f"{ws}/ctx_21.txt": "chunk 21"},
        expected_output="SUMMARY_OK",
        timeout_sec=300,
        tags=["reliability", "context"],
        difficulty="hard",
    )

    suite = BenchmarkSuite(
        name="coding",
        description="Deterministic CodingAgent loop eval: file tasks + reliability scenarios",
        tasks=tasks,
    )
    return suite, scripts


class CodingEvalDriver:
    """Wires the real CodingAgent to the scripted LLM and runs the suite."""

    def __init__(self, workspace: Path, output_dir: Path):
        self.workspace = workspace
        self.output_dir = output_dir
        self.suite, self.scripts = build_coding_suite(workspace)
        self.scripted = ScriptedLLM(self.scripts)
        self.current_task_id: str | None = None
        self.last_agent = None
        ALL_SUITES["coding"] = self.suite

    def _seed_workspace(self) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)
        (self.workspace / "config.txt").write_text("version: 1\nmode: dev\n")
        (self.workspace / "app.py").write_text('GREETING = "hi"\nprint(GREETING)\n')
        (self.workspace / "data.csv").write_text("name,age,status\nalpha,10,new\nbeta,20,active\n")
        (self.workspace / "main.py").write_text(
            "def compute_total(items):\n    return sum(items)\n\n\ndef helper():\n    return 1\n"
        )

    async def agent_fn(self, prompt: str) -> str:
        """The real CodingAgent.run, driven by the scripted deterministic LLM."""
        agent = _make_scripted_agent()
        agent.llm_client = self.scripted
        self.scripted.set_task(self.current_task_id or "")
        self.last_agent = agent
        result = await agent.run(prompt)
        return self._post_check(self.current_task_id or "", prompt, agent, result)

    def _post_check(self, task_id: str, prompt: str, agent, result: str) -> str:
        if task_id == "rel_large_input":
            intact = any(
                m.get("role") == "user" and m.get("content") == prompt for m in agent.messages
            )
            if not intact:
                raise AssertionError("200KB user message was corrupted in the loop")
            return "INTACT_OK"
        if task_id == "ctx_overflow":
            summarized = any(
                isinstance(m.get("content"), str)
                and any(marker in m["content"] for marker in SUMMARY_MARKERS)
                for m in agent.messages
            )
            return "SUMMARY_OK" if summarized else "SUMMARY_MISSING"
        return result

    async def run(self) -> object:
        self._seed_workspace()
        runner = _TaskAwareRunner(
            driver=self, agent_fn=self.agent_fn, output_dir=str(self.output_dir)
        )
        return await runner.run_suite("coding")


class _TaskAwareRunner(BenchmarkRunner):
    """BenchmarkRunner that tells the driver which task is running."""

    def __init__(self, driver: CodingEvalDriver, **kwargs):
        super().__init__(**kwargs)
        self.driver = driver

    async def _run_task(self, task):
        self.driver.current_task_id = task.id
        return await super()._run_task(task)


def print_report(report) -> None:
    print(f"\n{'=' * 64}")
    print(
        f"CODING EVAL: {report.succeeded}/{report.total_tasks} passed ({report.success_rate:.0%})"
    )
    print(f"{'task':<28}{'result':<8}{'latency':>10}")
    print("-" * 64)
    for r in report.results:
        status = "ok" if r.success else "FAIL"
        extra = "" if r.success else f"  <- {r.error or 'check failed'}"
        print(f"{r.task_id:<28}{status:<8}{r.latency_ms:>9.0f}ms{extra}")
    print("-" * 64)
    print(
        f"Avg latency: {report.avg_latency_ms:.0f}ms  Total: {report.total_latency_ms / 1000:.1f}s"
    )


async def amain(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic CodingAgent loop eval")
    parser.add_argument("--baseline", help="Baseline report JSON for comparison")
    parser.add_argument("--output", default="benchmark_results", help="Output directory")
    parser.add_argument(
        "--save-baseline",
        default=None,
        metavar="NAME",
        help="Also save this report as NAME.json for future --baseline runs",
    )
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="Exit 2 when the --baseline comparison reports regressions",
    )
    parser.add_argument(
        "--workspace",
        default=None,
        help="Eval workspace dir (default: <output>/coding_ws, cleaned each run)",
    )
    args = parser.parse_args(argv)

    # Keep the eval hermetic: no trajectory files, no stray env influence.
    os.environ["CODING_AGENT_TRAJECTORY"] = "0"

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    workspace = Path(args.workspace) if args.workspace else output_dir / "coding_ws"
    if workspace.exists():
        shutil.rmtree(workspace)

    print(f"Running coding eval ({len(build_coding_suite(workspace)[0].tasks)} tasks)")
    print(f"Workspace: {workspace}\n")

    driver = CodingEvalDriver(workspace, output_dir)
    report = await driver.run()
    print_report(report)

    runner = BenchmarkRunner(output_dir=str(output_dir))
    exit_code = 0
    if args.baseline:
        baseline = runner.load_baseline(args.baseline)
        if baseline:
            comparison = report.compare_to(baseline)
            print("\nBaseline comparison:")
            print(f"  Success rate: {comparison['success_rate_delta']:+.0%}")
            print(f"  Latency: {comparison['latency_delta_ms']:+.0f}ms")
            for reg in comparison["regressions"]:
                print(f"  REGRESSION {reg['task_id']}: {reg['issue']}")
            for imp in comparison["improvements"]:
                print(f"  IMPROVED   {imp['task_id']}: {imp['issue']}")
            if args.fail_on_regression:
                from tests.benchmarks import regression_exit_code

                exit_code = regression_exit_code(comparison)

    runner.save_report(report)
    if args.save_baseline:
        runner.save_report(report, f"{args.save_baseline}.json")
    return exit_code


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(amain(argv))


if __name__ == "__main__":
    raise SystemExit(main())
