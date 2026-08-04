"""
Diff, Verify, Process management, Notification, and Watch tools.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
import uuid
from pathlib import Path

from .base import ToolBase, ToolOutput
from .registry import register_tool

# ── Diff Tool ──


class DiffTool(ToolBase):
    name = "Diff"
    description = "Show a unified diff between two versions of content without writing files."
    aliases = ["ShowDiff", "Compare"]
    parameters = {
        "mode": {
            "type": "string",
            "enum": ["file_vs_proposed", "file_vs_file", "string_vs_string"],
            "description": "file_vs_proposed, file_vs_file, or string_vs_string",
        },
        "file_path": {"type": "string", "description": "Path to existing file (for file modes)"},
        "proposed_content": {
            "type": "string",
            "description": "Proposed new content (for file_vs_proposed)",
        },
        "file_path_b": {"type": "string", "description": "Second file path (for file_vs_file)"},
        "string_a": {"type": "string", "description": "First string (for string_vs_string)"},
        "string_b": {"type": "string", "description": "Second string (for string_vs_string)"},
        "context_lines": {"type": "integer", "description": "Lines of context (default: 3)"},
        "label_a": {"type": "string", "description": "Label for before side"},
        "label_b": {"type": "string", "description": "Label for after side"},
    }

    async def execute(
        self,
        mode: str,
        file_path: str = "",
        proposed_content: str = "",
        file_path_b: str = "",
        string_a: str = "",
        string_b: str = "",
        context_lines: int = 3,
        label_a: str = "",
        label_b: str = "",
    ) -> ToolOutput:
        import difflib

        try:
            if mode == "file_vs_proposed":
                path = Path(file_path).expanduser().resolve()
                if not path.exists():
                    return ToolOutput(text=f"File not found: {file_path}", error=True)
                old = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
                new = proposed_content.splitlines(keepends=True)
                label_a = label_a or str(path)
                label_b = label_b or "proposed"
            elif mode == "file_vs_file":
                path_a = Path(file_path).expanduser().resolve()
                path_b = Path(file_path_b).expanduser().resolve()
                old = path_a.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
                new = path_b.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
                label_a = label_a or str(path_a)
                label_b = label_b or str(path_b)
            elif mode == "string_vs_string":
                old = string_a.splitlines(keepends=True)
                new = string_b.splitlines(keepends=True)
                label_a = label_a or "before"
                label_b = label_b or "after"
            else:
                return ToolOutput(text=f"Unknown mode: {mode}", error=True)

            diff = difflib.unified_diff(old, new, fromfile=label_a, tofile=label_b, n=context_lines)
            result = "".join(diff)
            if not result:
                result = "(no differences)"

            return ToolOutput(
                text=result[:8000],
                title=f"Diff: {label_a} → {label_b}",
                metadata={"mode": mode},
            )
        except Exception as e:
            return ToolOutput(text=f"Diff error: {e}", error=True)


# ── Process Management Tool ──

_processes: dict[str, subprocess.Popen] = {}
_process_outputs: dict[str, list[str]] = {}


class ProcessTool(ToolBase):
    name = "Process"
    description = "Start a background process and read its output later. Use for dev servers, build watchers, log tailers."
    aliases = ["Background", "Daemon"]
    parameters = {
        "op": {
            "type": "string",
            "enum": ["start", "read", "status", "kill", "list"],
            "description": "start, read, status, kill, or list",
        },
        "command": {"type": "string", "description": "Shell command to run (required for start)"},
        "process_id": {"type": "string", "description": "Process ID returned by start"},
        "cwd": {"type": "string", "description": "Working directory for the process"},
        "clear": {
            "type": "boolean",
            "description": "Clear output buffer after reading (default: True)",
        },
    }

    async def execute(
        self,
        op: str,
        command: str = "",
        process_id: str = "",
        cwd: str = "",
        clear: bool = True,
    ) -> ToolOutput:
        try:
            if op == "list":
                lines = []
                for pid, proc in _processes.items():
                    running = proc.poll() is None
                    lines.append(
                        f"  {pid}: {'🟢 running' if running else '🔴 exited'} | {proc.args}"
                    )
                return ToolOutput(
                    text="\n".join(lines) if lines else "No processes.",
                    title=f"Processes ({len(_processes)})",
                )

            elif op == "start":
                pid = str(uuid.uuid4())[:8]
                work_dir = cwd or os.getcwd()
                proc = subprocess.Popen(
                    command,
                    shell=True,
                    cwd=work_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                _processes[pid] = proc
                _process_outputs[pid] = []
                return ToolOutput(
                    text=f"Started [{pid}]: {command}",
                    title=f"▶️ Started {pid}",
                    metadata={"process_id": pid, "command": command},
                )

            elif op == "read":
                if process_id not in _processes:
                    return ToolOutput(text=f"Process not found: {process_id}", error=True)
                proc = _processes[process_id]

                # Non-blocking read via thread (cross-platform)
                def _collect_output(p):
                    collected = []
                    if p.stdout:
                        try:
                            import select

                            while True:
                                r, _, _ = select.select([p.stdout], [], [], 0.1)
                                if not r:
                                    break
                                line = p.stdout.readline()
                                if not line:
                                    break
                                collected.append(line)
                        except OSError:
                            # Windows: select only works on sockets
                            import time

                            deadline = time.time() + 2.0
                            while time.time() < deadline:
                                line = p.stdout.readline()
                                if not line:
                                    break
                                collected.append(line)
                    return collected

                new_lines = await asyncio.to_thread(_collect_output, proc)
                for line in new_lines:
                    _process_outputs.setdefault(process_id, []).append(line)

                output = "".join(_process_outputs.get(process_id, [])[-50:]) or "(no output)"
                if clear and process_id in _process_outputs:
                    _process_outputs[process_id] = []

                return ToolOutput(
                    text=output[:4000],
                    title=f"\U0001f4c4 Output [{process_id}]",
                    metadata={"process_id": process_id},
                )

            elif op == "status":
                if process_id not in _processes:
                    return ToolOutput(text=f"Process not found: {process_id}", error=True)
                proc = _processes[process_id]
                running = proc.poll() is None
                exit_code = proc.returncode if not running else None
                text = f"[{process_id}]: {'🟢 Running' if running else f'🔴 Exited ({exit_code})'}"
                return ToolOutput(
                    text=text,
                    title=f"Status [{process_id}]",
                    metadata={"running": running, "exit_code": exit_code},
                )

            elif op == "kill":
                if process_id not in _processes:
                    return ToolOutput(text=f"Process not found: {process_id}", error=True)
                proc = _processes.pop(process_id, None)
                if proc:
                    proc.kill()
                _process_outputs.pop(process_id, None)
                return ToolOutput(text=f"Killed: {process_id}", title=f"🛑 Killed {process_id}")

            return ToolOutput(text=f"Unknown op: {op}", error=True)
        except Exception as e:
            return ToolOutput(text=f"Process error: {e}", error=True)


# ── Notification Tool ──


class NotifyTool(ToolBase):
    name = "Notify"
    description = "Send a desktop notification to alert the user that something finished."
    aliases = ["Alert", "Toast"]
    parameters = {
        "message": {"type": "string", "description": "The notification body text (1-2 sentences)"},
        "title": {"type": "string", "description": "Notification title (default: 'LuckyD Code')"},
        "duration": {"type": "integer", "description": "How long in seconds (default: 5)"},
        "level": {"type": "string", "description": "info, success, warning, or error"},
    }

    async def execute(
        self,
        message: str,
        title: str = "LuckyD Code",
        duration: int = 5,
        level: str = "info",
    ) -> ToolOutput:
        emoji = {"info": "ℹ️", "success": "✅", "warning": "⚠️", "error": "❌"}.get(  # noqa: RUF001
            level, ""
        )

        # Try Windows toast
        try:
            import platform

            if platform.system() == "Windows":
                from win10toast import ToastNotifier

                toaster = ToastNotifier()
                toaster.show_toast(title, f"{emoji} {message}", duration=duration, threaded=True)
                return ToolOutput(text=f"Notification sent: {emoji} {message}", title="🔔 Notified")
        except ImportError:
            pass

        # Fallback: print
        print(f"\n  {emoji} {title}: {message}")
        return ToolOutput(text=f"{emoji} {message}", title="🔔 Notified")


# ── Watch Tool ──

_watches: dict[str, dict] = {}


class WatchTool(ToolBase):
    name = "Watch"
    description = "Watch a file or path for a condition (exists, changed, contains, deleted), then wait for it."
    aliases = ["WaitFor", "Poll"]
    timeout_sec = 310.0
    parameters = {
        "op": {
            "type": "string",
            "enum": ["arm", "check", "wait", "list", "cancel"],
            "description": "arm, check, wait, list, or cancel",
        },
        "path": {"type": "string", "description": "File path to watch (required for arm)"},
        "condition": {
            "type": "string",
            "enum": ["exists", "changed", "contains", "deleted"],
            "description": "Trigger condition (default: changed)",
        },
        "contains_text": {
            "type": "string",
            "description": "Text to look for (required for contains condition)",
        },
        "watch_id": {"type": "string", "description": "Watch ID returned by arm"},
        "timeout_sec": {
            "type": "integer",
            "description": "How long to block waiting (default: 30)",
        },
    }

    async def execute(
        self,
        op: str,
        path: str = "",
        condition: str = "changed",
        contains_text: str = "",
        watch_id: str = "",
        timeout_sec: int = 30,
    ) -> ToolOutput:
        try:
            if op == "list":
                items = []
                for wid, w in _watches.items():
                    items.append(f"  {wid}: {w['path']} [{w['condition']}] armed={w['armed']}")
                return ToolOutput(
                    text="\n".join(items) if items else "No active watches.",
                    title=f"Watches ({len(_watches)})",
                )

            elif op == "arm":
                wid = str(uuid.uuid4())[:8]
                file_path = Path(path).expanduser().resolve()
                snapshot = {}
                if file_path.exists():
                    snapshot["mtime"] = file_path.stat().st_mtime
                    snapshot["size"] = file_path.stat().st_size

                _watches[wid] = {
                    "path": str(file_path),
                    "condition": condition,
                    "contains_text": contains_text,
                    "snapshot": snapshot,
                    "armed": True,
                    "fired": False,
                }
                return ToolOutput(
                    text=f"Armed watch [{wid}] on {file_path} [{condition}]",
                    title=f"🔭 Watching {wid}",
                    metadata={"watch_id": wid},
                )

            elif op == "check":
                if watch_id not in _watches:
                    return ToolOutput(text=f"Watch not found: {watch_id}", error=True)
                w = _watches[watch_id]
                file_path = Path(w["path"])
                fired = False

                if w["condition"] == "exists":
                    fired = file_path.exists()
                elif w["condition"] == "deleted":
                    fired = not file_path.exists()
                elif w["condition"] == "changed":
                    if file_path.exists():
                        mtime = file_path.stat().st_mtime
                        size = file_path.stat().st_size
                        fired = mtime != w["snapshot"].get("mtime") or size != w["snapshot"].get(
                            "size"
                        )
                elif w["condition"] == "contains" and file_path.exists():
                    content = file_path.read_text(errors="replace")
                    fired = w.get("contains_text", "") in content

                w["fired"] = fired
                return ToolOutput(
                    text=f"Watch [{watch_id}]: {'✅ FIRED' if fired else '⏳ Waiting'}",
                    title=f"Watch {watch_id}",
                    metadata={"fired": fired, "watch_id": watch_id},
                )

            elif op == "wait":
                if watch_id not in _watches:
                    return ToolOutput(text=f"Watch not found: {watch_id}", error=True)
                deadline = time.time() + min(timeout_sec, 300)
                while time.time() < deadline:
                    fired = False
                    w = _watches[watch_id]
                    file_path = Path(w["path"])
                    if w["condition"] == "exists":
                        fired = file_path.exists()
                    elif w["condition"] == "deleted":
                        fired = not file_path.exists()
                    elif w["condition"] == "changed":
                        if file_path.exists():
                            mtime = file_path.stat().st_mtime
                            size = file_path.stat().st_size
                            fired = mtime != w["snapshot"].get("mtime") or size != w[
                                "snapshot"
                            ].get("size")
                    elif w["condition"] == "contains" and file_path.exists():
                        content = file_path.read_text(errors="replace")
                        fired = w.get("contains_text", "") in content

                    if fired:
                        w["fired"] = True
                        return ToolOutput(
                            text=f"Watch [{watch_id}] fired!",
                            title=f"🔔 Watch {watch_id} Fired",
                            metadata={"watch_id": watch_id},
                        )
                    await asyncio.sleep(1.0)

                return ToolOutput(
                    text=f"Watch [{watch_id}] timed out after {timeout_sec}s",
                    title=f"⏰ Watch {watch_id} Timeout",
                    error=True,
                )

            elif op == "cancel":
                if watch_id not in _watches:
                    return ToolOutput(text=f"Watch not found: {watch_id}", error=True)
                _watches.pop(watch_id, None)
                return ToolOutput(
                    text=f"Cancelled watch: {watch_id}", title=f"✗ Cancelled {watch_id}"
                )

            return ToolOutput(text=f"Unknown op: {op}", error=True)
        except Exception as e:
            return ToolOutput(text=f"Watch error: {e}", error=True)


# Auto-register
register_tool(DiffTool())
register_tool(ProcessTool())
register_tool(NotifyTool())
register_tool(WatchTool())
