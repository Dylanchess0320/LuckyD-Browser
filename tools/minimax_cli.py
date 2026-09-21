"""MiniMax external CLI launchers (LuckyD 9.8): `mcode` + `mmx` tools."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

MCODE_INSTALL_HINT = "Install it: npm i -g @minimax-code/cli (provides the `mcode` command)"
MMX_INSTALL_HINT = "Install it: npm i -g @minimax/mmi (provides the `mmx` command)"

_MCODE_EXE = "mcode.cmd" if os.name == "nt" else "mcode"
_MMX_EXE = "mmx.cmd" if os.name == "nt" else "mmx"


def find_mcode_exe() -> str | None:
    """Locate the mcode CLI (PATH + %USERPROFILE%/.minimax-code fallback)."""
    found = shutil.which("mcode") or shutil.which(_MCODE_EXE)
    if found:
        return found
    cand = Path.home() / ".minimax-code" / ("bin" if os.name != "nt" else "") / _MCODE_EXE
    try:
        if cand.is_file():
            return str(cand)
    except OSError:
        pass
    up = os.environ.get("USERPROFILE", "").strip()
    if up:
        cand2 = Path(up) / ".minimax-code" / _MCODE_EXE
        try:
            if cand2.is_file():
                return str(cand2)
        except OSError:
            pass
    return None


def find_mmx_exe() -> str | None:
    """Locate the mmx CLI on PATH (honest None when missing)."""
    return shutil.which("mmx") or shutil.which(_MMX_EXE)


def _run_capture(cmd: list[str], timeout_sec: float, runner: Any = None) -> dict[str, Any]:
    """Run cmd, capturing output. Never raises — errors become error results."""
    try:
        run = runner or subprocess.run
        completed = run(cmd, capture_output=True, text=True, timeout=timeout_sec)
    except FileNotFoundError:
        return {"ok": False, "output": "", "error": f"command not found: {cmd[0]}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "", "error": f"command timed out after {timeout_sec}s"}
    except Exception as exc:
        return {"ok": False, "output": "", "error": f"{type(exc).__name__}: {exc}"}
    output = (
        (getattr(completed, "stdout", "") or "") + (getattr(completed, "stderr", "") or "")
    ).strip()
    code = int(getattr(completed, "returncode", 1) or 0)
    if code != 0:
        return {"ok": False, "output": output, "error": f"exit code {code}"}
    return {"ok": True, "output": output, "error": ""}


def run_mcode(
    prompt: str,
    model: str = "",
    timeout_sec: float = 120.0,
    runner: Any = None,
) -> dict[str, Any]:
    """Run `mcode exec <prompt> [--model p/m]`, capturing output."""
    exe = find_mcode_exe()
    if not exe:
        return {"ok": False, "output": "", "error": f"mcode CLI not found. {MCODE_INSTALL_HINT}"}
    if not (prompt or "").strip():
        return {"ok": False, "output": "", "error": "mcode needs a prompt"}
    cmd = [exe, "exec", prompt.strip()]
    if (model or "").strip():
        cmd += ["--model", model.strip()]
    return _run_capture(cmd, timeout_sec, runner)


def run_mmx(
    text: str,
    model: str = "",
    timeout_sec: float = 120.0,
    runner: Any = None,
) -> dict[str, Any]:
    """Run `mmx text chat --message <text> [--model]`, capturing output."""
    exe = find_mmx_exe()
    if not exe:
        return {"ok": False, "output": "", "error": f"mmx CLI not found. {MMX_INSTALL_HINT}"}
    if not (text or "").strip():
        return {"ok": False, "output": "", "error": "mmx needs a message"}
    cmd = [exe, "text", "chat", "--message", text.strip()]
    if (model or "").strip():
        cmd += ["--model", model.strip()]
    return _run_capture(cmd, timeout_sec, runner)


class McodeTool:
    """Passthrough to the external MiniMax Code CLI (`mcode exec`)."""

    name = "mcode"
    description = (
        "Run the external MiniMax Code CLI headless: `mcode exec <prompt>`. "
        "Missing binary returns an error result with an install hint."
    )
    parameters = {
        "prompt": {"type": "string", "description": "Task for mcode.", "required": True},
        "model": {
            "type": "string",
            "description": "Optional provider/model ref.",
            "required": False,
        },
    }
    aliases: list[str] = []
    permission_level = "NORMAL"
    tracks_files = False
    timeout_sec: float | None = None

    async def execute(self, **kwargs: Any) -> Any:
        from .base import ToolOutput

        result = run_mcode(str(kwargs.get("prompt", "")), str(kwargs.get("model", "") or ""))
        if result["ok"]:
            return ToolOutput(
                text=result["output"] or "(mcode produced no output)", metadata={"tool": "mcode"}
            )
        return ToolOutput(
            text=f"mcode failed: {result['error']}\n{result['output']}".strip(), error=True
        )


class MmxTool:
    """Passthrough to the external MiniMax media CLI (`mmx text chat`)."""

    name = "mmx"
    description = (
        "Run the external MiniMax media CLI: `mmx text chat --message <text>`. "
        "Missing binary returns an error result with an install hint."
    )
    parameters = {
        "text": {"type": "string", "description": "Message for mmx.", "required": False},
        "model": {"type": "string", "description": "Optional model ref.", "required": False},
    }
    aliases: list[str] = []
    permission_level = "NORMAL"
    tracks_files = False
    timeout_sec: float | None = None

    async def execute(self, **kwargs: Any) -> Any:
        from .base import ToolOutput

        result = run_mmx(str(kwargs.get("text", "")), str(kwargs.get("model", "") or ""))
        if result["ok"]:
            return ToolOutput(
                text=result["output"] or "(mmx produced no output)", metadata={"tool": "mmx"}
            )
        return ToolOutput(
            text=f"mmx failed: {result['error']}\n{result['output']}".strip(), error=True
        )


try:
    from .registry import register_tool

    register_tool(McodeTool())  # type: ignore[arg-type]
    register_tool(MmxTool())  # type: ignore[arg-type]
except Exception:
    pass
