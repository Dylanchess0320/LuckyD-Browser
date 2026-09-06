"""Capability stubs for future workers (browser automation, code execution).

These are intentionally NOT enabled by default — unsafe to run arbitrary code
or drive a browser autonomously without sandboxing + human approval. The swarm
planner can still request them; the stubs return a clear 'not available' result
so the graph never crashes.
"""

from __future__ import annotations


def browser_worker(url: str) -> str:
    raise NotImplementedError(
        "Browser worker is a stub. Implement with Playwright/Browser-Use in a "
        "sandboxed, permissioned container before enabling."
    )


def code_worker(code: str) -> str:
    raise NotImplementedError(
        "Code worker is a stub. Implement with a sandboxed executor "
        "(e.g. Docker / e2b / restricted subprocess) before enabling."
    )
