"""LuckyD 9.5 — agent-terminal crash regression tests.

Root cause (2026-09-16): ``BashTool.execute`` raised
``AttributeError: 'NoneType' object has no attribute 'lower'`` when the agent
issued a Bash tool call with a null/non-string ``command``. The exception
escaped the tool and took down the agent's terminal session instead of
coming back as an error result. These tests lock in: no input to the shell
tools may ever raise — every failure mode returns a ``ToolOutput`` with
``error=True``.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

from tools.base import ToolOutput
from tools.bash_tool import BashTool, PowerShellTool

IS_WINDOWS = sys.platform == "win32"


async def test_bash_none_command_returns_error_result() -> None:
    """The 9.5 crash: execute(None) must NOT raise AttributeError."""
    res = await BashTool().execute(None)  # type: ignore[arg-type]
    assert isinstance(res, ToolOutput)
    assert res.error is True
    assert res.text


async def test_bash_nonstring_command_returns_error_result() -> None:
    for bad in (123, ["echo", "hi"], {"command": "echo hi"}, b"echo hi"):
        res = await BashTool().execute(bad)  # type: ignore[arg-type]
        assert isinstance(res, ToolOutput), bad
        assert res.error is True, bad


def test_bash_is_safe_rejects_nonstring_without_raising() -> None:
    tool = BashTool()
    for bad in (None, 123, ["echo hi"], b"echo hi"):
        safe, reason = tool._is_safe(bad)  # type: ignore[arg-type]
        assert safe is False
        assert reason


async def test_powershell_none_command_returns_error_result() -> None:
    """PowerShellTool already caught this via except Exception; lock it in."""
    res = await PowerShellTool().execute(None)  # type: ignore[arg-type]
    assert isinstance(res, ToolOutput)
    assert res.error is True


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX killpg tree-kill path")
async def test_bash_timeout_kills_process_tree_and_returns_error() -> None:
    """Timeout path: error result (never a raise) and the whole tree reaped."""
    script = "/tmp/test95_sleeper.py"
    with open(script, "w") as fh:
        fh.write(
            "import os, subprocess, time\n"
            "open('/tmp/test95_sleeper.pid','w').write(str(os.getpid()))\n"
            "g = subprocess.Popen(['sleep','60'])\n"
            "open('/tmp/test95_gchild.pid','w').write(str(g.pid))\n"
            "time.sleep(60)\n"
        )
    try:
        res = await BashTool().execute(f"python3 {script}", timeout=2000)
        assert isinstance(res, ToolOutput)
        assert res.error is True
        assert "timed out" in res.text
        await asyncio.sleep(1.0)

        def _state(pid: str) -> str:
            try:
                with open(f"/proc/{pid}/stat") as fh:
                    return fh.read().split()[2]
            except FileNotFoundError:
                return "gone"

        def _read_pid(path: str) -> str:
            with open(path) as fh:
                return fh.read().strip()

        parent = _read_pid("/tmp/test95_sleeper.pid")
        child = _read_pid("/tmp/test95_gchild.pid")
        assert _state(parent) in ("gone", "Z"), f"parent leaked: {parent}"
        assert _state(child) in ("gone", "Z"), f"grandchild leaked: {child}"
    finally:
        for p in (script, "/tmp/test95_sleeper.pid", "/tmp/test95_gchild.pid"):
            if os.path.exists(p):
                os.remove(p)
