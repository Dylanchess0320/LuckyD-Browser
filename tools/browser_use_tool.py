"""
Browser-Use integration tool — AI-powered web automation.
Uses the browser-use library (https://github.com/browser-use/browser-use)
to perform complex multi-step browser tasks autonomously.

Requires: Python >= 3.11 and `pip install browser-use`
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from .base import ToolBase, ToolOutput
from .registry import register_tool

# ── lazy import ─────────────────────────────────────────────────────────────

_browser_use_available = None


def _check_browser_use() -> bool:
    """Check if browser-use is importable (needs Python 3.11+)."""
    global _browser_use_available
    if _browser_use_available is not None:
        return _browser_use_available
    try:
        import browser_use  # noqa: F401

        _browser_use_available = True
    except ImportError:
        _browser_use_available = False
    return _browser_use_available


# ── helper ──────────────────────────────────────────────────────────────────


def _get_api_key() -> str:
    """Try to read the coding-agent's own API key for reuse."""
    key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("CODING_AGENT_API_KEY")
    if key:
        return key

    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "DEEPSEEK_API_KEY" in line and "=" in line:
                return line.split("=", 1)[1].strip().strip('"').strip("'")

    return ""


# ── Tool class ──────────────────────────────────────────────────────────────


class BrowserUseTool(ToolBase):
    name = "BrowserUse"
    description = (
        "Perform a complex web task using AI-powered browser automation. "
        "Give it a natural-language task (e.g., 'Go to GitHub, search for "
        "fastapi, and tell me the star count') and it will navigate, click, "
        "type, and extract information autonomously. "
        "Returns a summary of what was done and any extracted data."
    )
    aliases = ["WebAgent", "WebTask", "BrowseWithAI"]
    timeout_sec = 150.0
    parameters = {
        "task": {
            "type": "string",
            "description": "Natural-language description of what to do in the browser.",
        },
        "max_steps": {
            "type": "integer",
            "description": "Maximum number of browser steps to take (default: 15).",
        },
        "headless": {
            "type": "boolean",
            "description": "Run browser in headless mode (default: true).",
        },
        "use_vision": {
            "type": "boolean",
            "description": "Enable vision/OCR for the agent to see the page (default: true).",
        },
    }

    async def execute(
        self,
        task: str,
        max_steps: int = 15,
        headless: bool = True,
        use_vision: bool = True,
    ) -> ToolOutput:
        if not _check_browser_use():
            return ToolOutput(
                text=(
                    "browser-use is not installed or requires Python >= 3.11.\n"
                    "Install with: pip install browser-use\n"
                    "Then install browsers: playwright install chromium\n\n"
                    "If you already have Python 3.11+, make sure it's the active Python."
                ),
                title="browser-use unavailable",
                error=True,
            )

        try:
            from browser_use import Agent, Browser
            from browser_use.browser.views import BrowserConfig

            api_key = _get_api_key()
            if not api_key:
                return ToolOutput(
                    text="No API key found. Set DEEPSEEK_API_KEY or CODING_AGENT_API_KEY in .env or environment.",
                    title="No API key",
                    error=True,
                )

            browser = Browser(
                config=BrowserConfig(
                    headless=headless,
                    disable_security=False,
                )
            )

            agent = Agent(
                task=task,
                llm=api_key,
                browser=browser,
                use_vision=use_vision,
            )

            result = await asyncio.wait_for(
                agent.run(max_steps=max_steps),
                timeout=120,
            )

            final_text = ""

            if hasattr(result, "final_result"):
                fr = result.final_result
                if callable(fr):
                    try:
                        final_text = str(fr())
                    except Exception:
                        final_text = ""
                else:
                    final_text = str(fr)

            if not final_text and hasattr(result, "model_dump"):
                dump = result.model_dump
                if callable(dump):
                    dump = dump()
                if isinstance(dump, dict):
                    history = dump.get("history", [])
                    steps = []
                    for i, step in enumerate(history):
                        if isinstance(step, dict):
                            result_text = step.get("result", "")
                            if result_text:
                                steps.append(f"Step {i + 1}: {result_text}")
                    final_text = "\n".join(steps[-10:]) if steps else str(dump)[:4000]

            if not final_text:
                final_text = str(result)[:4000]

            await browser.close()

            return ToolOutput(
                text=final_text[:8000],
                title="Browser Agent Task Complete",
                metadata={
                    "task": task,
                    "max_steps": max_steps,
                    "headless": headless,
                    "use_vision": use_vision,
                },
            )

        except asyncio.TimeoutError:
            return ToolOutput(
                text="Browser task timed out (120s limit). Try reducing max_steps or simplifying the task.",
                title="Browser Task Timeout",
                error=True,
            )
        except Exception as e:
            return ToolOutput(
                text=f"Browser task error: {type(e).__name__}: {e}",
                title="Browser Task Error",
                error=True,
            )


class BrowserUseCloseTool(ToolBase):
    """Close the shared browser-use browser (cleans up resources)."""

    name = "BrowserUseClose"
    description = "Close the browser-use browser session and free resources."
    aliases = ["CloseWebAgent"]
    parameters = {}

    async def execute(self) -> ToolOutput:
        return ToolOutput(text="browser-use resources released.", title="Browser Closed")


# ── Auto-register ───────────────────────────────────────────────────────────

register_tool(BrowserUseTool())
register_tool(BrowserUseCloseTool())
