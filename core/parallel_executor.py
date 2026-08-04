"""
Parallel tool execution engine — run independent tool calls concurrently.

The single biggest speedup for agent exploration: instead of reading files
one at a time, fire 5 reads + 2 greps + 1 web fetch simultaneously.

Design:
  - Dependency analysis: tools with no data dependencies run in parallel
  - Semaphore-based concurrency limit (prevents overwhelming the event loop)
  - Timeout per tool, global timeout for the batch
  - Results returned in original call order (deterministic)
  - Failed tools don't block successful ones
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from tools.registry import registry

# ── Tool Dependency Classification ────────────────────────────────────

# Tools that only read state — always safe to run in parallel
READ_ONLY_TOOLS = {
    "Read",
    "Grep",
    "Glob",
    "Brief",
    "LspHover",
    "LspDefinition",
    "LspReferences",
    "LspDocumentSymbols",
    "WebFetch",
    "WebSearch",
    "Http",
    "MemoryRecall",
    "TaskList",
    "TaskGet",
    "TodoRead",
    "GitStatus",
    "GitDiff",
    "GitLog",
    "GitBranch",
    "ShellHistory",
    "CSV",
    "SQLite",
    "DateTime",
    "LspWorkspaceSymbols",
    "LspIncomingCalls",
    "LspOutgoingCalls",
    "LspImplementation",
    "Graphify",
    "SkillList",
    "ListAgents",
    "Config",
    "BrowserSnapshot",
    "BrowserScreenshot",
    "BrowserEvaluate",
    "BrowserIntercept",
}

# Tools that modify state — must run sequentially (or with explicit ordering)
WRITE_TOOLS = {
    "Write",
    "Edit",
    "Bash",
    "PowerShell",
    "GitAdd",
    "GitCommit",
    "GitPush",
    "MemoryRemember",
    "MemoryForget",
    "TaskCreate",
    "TaskUpdate",
    "TaskStop",
    "TodoWrite",
    "BrowserClick",
    "BrowserType",
    "BrowserNavigate",
    "LspRename",
    "BrowserState",
    "BrowserEmulate",
    "BrowserToggleHeadless",
    "BrowserTrace",
    "Notify",
    "Process",
    "Watch",
    "Secrets",
}

# Tools that should NEVER run in parallel (user interaction, side effects)
NEVER_PARALLEL = {
    "AskUserQuestion",
    "EnterPlanMode",
    "ExitPlanMode",
    "OpenInBrowser",
    "SkillRun",
    "SubAgent",
    "AgentHandoff",
    "TeamCreate",
    "SendMessage",
    "ReceiveMessage",
}


@dataclass
class ToolCall:
    """A single tool invocation request."""

    id: str
    name: str
    arguments: dict[str, Any]
    depends_on: list[str] = field(default_factory=list)  # IDs this call depends on


@dataclass
class ToolResult:
    """Result from a single tool execution."""

    id: str
    name: str
    success: bool
    output: str
    duration_ms: float
    error: str | None = None


@dataclass
class BatchResult:
    """Aggregated results from a batch of tool calls."""

    results: list[ToolResult]
    total_duration_ms: float
    parallel_speedup: float  # actual_time / sequential_estimate
    succeeded: int
    failed: int


class ParallelExecutor:
    """Execute multiple tool calls with dependency-aware parallelism.

    Usage:
        executor = ParallelExecutor(max_concurrent=5)
        calls = [
            ToolCall(id="1", name="Read", arguments={"file_path": "a.py"}),
            ToolCall(id="2", name="Read", arguments={"file_path": "b.py"}),
            ToolCall(id="3", name="Grep", arguments={"pattern": "def main"}),
        ]
        batch = await executor.execute_batch(calls)
    """

    def __init__(self, max_concurrent: int = 5, default_timeout_sec: float = 30.0):
        self.max_concurrent = max_concurrent
        self.default_timeout_sec = default_timeout_sec
        self._semaphore = asyncio.Semaphore(max_concurrent)

    def can_parallelize(self, calls: list[ToolCall]) -> tuple[list[ToolCall], list[ToolCall]]:
        """Split calls into (parallelizable, must-be-sequential).

        Rules:
          - NEVER_PARALLEL tools always go sequential
          - WRITE_TOOLS go sequential unless explicitly marked safe
          - READ_ONLY_TOOLS can run in parallel
          - Unknown tools default to sequential (safe)
        """
        parallel: list[ToolCall] = []
        sequential: list[ToolCall] = []

        for call in calls:
            if call.name in NEVER_PARALLEL:
                sequential.append(call)
            elif call.name in WRITE_TOOLS:
                # Write tools can only parallelize if they don't depend on each other
                if not call.depends_on:
                    # Still risky — default to sequential for writes
                    sequential.append(call)
                else:
                    sequential.append(call)
            elif call.name in READ_ONLY_TOOLS:
                parallel.append(call)
            else:
                # Unknown tool — be conservative
                sequential.append(call)

        return parallel, sequential

    async def execute_batch(self, calls: list[ToolCall]) -> BatchResult:
        """Execute a batch of tool calls with maximum parallelism.

        Returns results in the same order as input calls.
        """
        if not calls:
            return BatchResult(
                results=[], total_duration_ms=0, parallel_speedup=1.0, succeeded=0, failed=0
            )

        start = time.monotonic()
        parallel_calls, sequential_calls = self.can_parallelize(calls)

        # Build dependency graph for parallel calls
        # Simple approach: if no depends_on, all parallel calls run together
        results: dict[str, ToolResult] = {}

        # Execute parallel batch
        if parallel_calls:
            parallel_results = await asyncio.gather(
                *[self._execute_one(call) for call in parallel_calls],
                return_exceptions=True,
            )
            for call, result in zip(parallel_calls, parallel_results, strict=False):
                if isinstance(result, Exception):
                    results[call.id] = ToolResult(
                        id=call.id,
                        name=call.name,
                        success=False,
                        output="",
                        duration_ms=0,
                        error=str(result),
                    )
                else:
                    results[call.id] = result

        # Execute sequential calls in order
        for call in sequential_calls:
            result = await self._execute_one(call)
            results[call.id] = result

        # Reassemble in original order
        ordered_results = [results[call.id] for call in calls]

        elapsed = (time.monotonic() - start) * 1000
        sequential_estimate = sum(r.duration_ms for r in ordered_results)
        speedup = sequential_estimate / elapsed if elapsed > 0 else 1.0

        return BatchResult(
            results=ordered_results,
            total_duration_ms=elapsed,
            parallel_speedup=speedup,
            succeeded=sum(1 for r in ordered_results if r.success),
            failed=sum(1 for r in ordered_results if not r.success),
        )

    async def _execute_one(self, call: ToolCall) -> ToolResult:
        """Execute a single tool call with semaphore and timeout."""
        start = time.monotonic()
        async with self._semaphore:
            try:
                tool_fn = registry.get(call.name)
                if tool_fn is None:
                    return ToolResult(
                        id=call.id,
                        name=call.name,
                        success=False,
                        output="",
                        duration_ms=0,
                        error=f"Tool '{call.name}' not found in registry",
                    )

                # Execute with timeout
                if asyncio.iscoroutinefunction(tool_fn):
                    output = await asyncio.wait_for(
                        tool_fn(**call.arguments),
                        timeout=self.default_timeout_sec,
                    )
                else:
                    # Sync function — run in thread pool
                    loop = asyncio.get_event_loop()
                    output = await asyncio.wait_for(
                        loop.run_in_executor(None, lambda: tool_fn(**call.arguments)),
                        timeout=self.default_timeout_sec,
                    )

                duration = (time.monotonic() - start) * 1000
                return ToolResult(
                    id=call.id,
                    name=call.name,
                    success=True,
                    output=str(output),
                    duration_ms=duration,
                )

            except asyncio.TimeoutError:
                duration = (time.monotonic() - start) * 1000
                return ToolResult(
                    id=call.id,
                    name=call.name,
                    success=False,
                    output="",
                    duration_ms=duration,
                    error=f"Timed out after {self.default_timeout_sec}s",
                )
            except Exception as e:
                duration = (time.monotonic() - start) * 1000
                return ToolResult(
                    id=call.id,
                    name=call.name,
                    success=False,
                    output="",
                    duration_ms=duration,
                    error=str(e),
                )


# ── Convenience: auto-parallelize tool calls from LLM response ────────


def extract_tool_calls(response_text: str) -> list[ToolCall]:
    """Parse tool calls from LLM response text (JSON format).

    Expects the LLM to output tool calls in a structured format.
    Returns empty list if no valid tool calls found.
    """
    import json
    import re

    calls: list[ToolCall] = []

    # Look for JSON arrays containing tool call objects
    # Pattern: [{"name": "...", "arguments": {...}}, ...]
    pattern = r'\[(\s*\{[^]]*"name"[^]]*\}\s*,?)+\s*\]'
    matches = re.findall(pattern, response_text, re.DOTALL)

    for i, match in enumerate(matches):
        try:
            parsed = json.loads(f"[{match}]")
            if isinstance(parsed, list):
                for j, item in enumerate(parsed):
                    if isinstance(item, dict) and "name" in item:
                        calls.append(
                            ToolCall(
                                id=f"{i}_{j}",
                                name=item["name"],
                                arguments=item.get("arguments", {}),
                                depends_on=item.get("depends_on", []),
                            )
                        )
        except json.JSONDecodeError:
            continue

    return calls


# ── Singleton ─────────────────────────────────────────────────────────

_executor: ParallelExecutor | None = None


def get_executor() -> ParallelExecutor:
    """Get or create the global parallel executor."""
    global _executor
    if _executor is None:
        _executor = ParallelExecutor(max_concurrent=5)
    return _executor
