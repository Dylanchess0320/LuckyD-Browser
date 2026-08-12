"""
Safe command execution tool with sandboxing, timeout, and output capture.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import platform
import re

from .base import ToolBase, ToolOutput
from .registry import register_tool
from .session_tools import record_shell_command

# Blocked patterns for safety.
# Merged with sandbox.py's blocklist (which had a larger pattern set — registry
# deletion, privilege escalation) so the two independent checks stop diverging,
# and extended with PowerShell-native destructive cmdlets: the previous list
# was written for bash syntax (rm -rf, dd if=) but the shell actually being
# driven on Windows is PowerShell, where the equivalent destructive commands
# (Remove-Item -Recurse -Force, Clear-Disk, etc.) used different names
# entirely and weren't covered by either blocklist.
BLOCKED_PATTERNS = [
    # Destructive filesystem ops
    r"rm\s+-rf\s+/",
    r"rd\s+/s\s+/q\s+c:\\",
    r"format\s+[c-zC-Z]:",
    r"del\s+/[fsq].*\\Windows",
    r"deltree",
    r"mkfs\.",
    r"dd\s+if=",
    r">\s*/dev/",
    # PowerShell-native destructive cmdlets (bash-style patterns above don't
    # match these at all)
    r"remove-item\s+.*-recurse\s+.*-force",
    r"remove-item\s+.*-force\s+.*-recurse",
    r"clear-disk",
    r"clear-recyclebin\s+.*-force",
    r"initialize-disk",
    r"format-volume",
    r"remove-partition",
    # Dangerous system ops
    r"shutdown",
    r"reboot",
    r"restart-computer",
    r"stop-computer",
    r"bcdedit",
    # Fork bombs / resource exhaustion
    r":\(\)\s*\{\s*:\|:&\s*\};:",  # fork bomb
    r"%0\|%0",
    # Privilege escalation
    r"sudo\s",
    r"runas\s+/user:",
    # Network havoc
    r"netsh\s+.*delete",
    r"ipconfig\s+/release",
    r"wmic\s+.*delete",
    # Registry destruction
    r"reg\s+delete\s+hklm",
    r"reg\s+delete\s+/f",
]

# Allowlist for common dev commands
ALLOWED_PREFIXES = [
    "ls",
    "dir",
    "cat",
    "echo",
    "pwd",
    "cd",
    "mkdir",
    "python",
    "python3",
    "py",
    "node",
    "npm",
    "npx",
    "pip",
    "git",
    "gh",
    "curl",
    "wget",
    "dotnet",
    "cargo",
    "go",
    "javac",
    "java",
    "gcc",
    "g++",
    "make",
    "cmake",
    "ninja",
    "docker",
    "docker-compose",
    "kubectl",
    "helm",
    "tar",
    "zip",
    "unzip",
    "gzip",
    "gunzip",
    "cp",
    "copy",
    "mv",
    "move",
    "ren",
    "rename",
    "find",
    "grep",
    "rg",
    "fd",
    "awk",
    "sed",
    "sort",
    "uniq",
    "head",
    "tail",
    "wc",
    "diff",
    "md5sum",
    "sha256sum",
    "chmod",
    "chown",
    "stat",
    "file",
    "which",
    "where",
    "df",
    "du",
    "free",
    "top",
    "ps",
    "kill",
    "tasklist",
    "taskkill",
    "ping",
    "nslookup",
    "ipconfig",
    "ifconfig",
    "netstat",
    "ssh",
    "scp",
    "rsync",
    "type",
    "set",
    "export",
    "printenv",
    "env",
    "npx",
    "yarn",
    "pnpm",
    "tsc",
    "eslint",
    "prettier",
    "code",
    "notepad",
    "start",
    "open",
    "xdg-open",
]

IS_WINDOWS = platform.system() == "Windows"


class BashTool(ToolBase):
    name = "Bash"
    description = "Execute a shell command and return its output. Use for dev tasks, file ops, package installs, git, etc."
    aliases = ["Shell", "Cmd", "Run"]
    timeout_sec = 620.0
    parameters = {
        "command": {"type": "string", "description": "The shell command to execute"},
        "description": {
            "type": "string",
            "description": "Clear description of what this command does",
        },
        "timeout": {
            "type": "integer",
            "description": "Timeout in milliseconds (default 120000, max 600000)",
        },
        "cwd": {"type": "string", "description": "Working directory for the command"},
    }

    def _is_safe(self, cmd: str) -> tuple[bool, str]:
        cmd.strip().lower()

        for pattern in BLOCKED_PATTERNS:
            if re.search(pattern, cmd, re.IGNORECASE):
                return False, f"Blocked dangerous pattern: {pattern}"

        # Check allowlist — the first-word check alone only validated the
        # FIRST command in a chain. `<allowed> && rm -rf /` passed because
        # only "rm" (not the whole chain) is checked against the allowlist;
        # the blocklist regex above still scans the full string so the most
        # obvious bypasses were already caught, but any chained command
        # whose second half wasn't itself in BLOCKED_PATTERNS sailed through
        # unchecked. Split on chain/pipe operators and validate every segment.
        segments = re.split(r"&&|\|\||;|\|(?!\|)", cmd)
        for segment in segments:
            segment = segment.strip()
            if not segment:
                continue
            first_word = segment.split()[0] if segment.split() else ""
            first_word_lower = first_word.lower()
            if first_word_lower in [p.lower() for p in ALLOWED_PREFIXES]:
                continue
            if first_word.startswith("/") or (IS_WINDOWS and first_word[1:2] == ":\\"):
                continue
            return (
                False,
                f"Command prefix '{first_word}' not in allowlist. Wrap unsafe commands in a script file.",
            )
        return True, ""

    async def execute(
        self, command: str, description: str = "", timeout: int = 120000, cwd: str = ""
    ) -> ToolOutput:
        safe, reason = self._is_safe(command)
        if not safe:
            return ToolOutput(
                text=f"SAFETY BLOCK: {reason}\n\nTo bypass, write the command to a .sh/.bat file and run that instead.",
                error=True,
            )

        timeout_sec = min(timeout, 600000) / 1000
        work_dir = cwd or os.getcwd()

        try:
            if IS_WINDOWS:
                proc = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=work_dir,
                )
            else:
                proc = await asyncio.create_subprocess_exec(
                    "bash",
                    "-c",
                    command,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=work_dir,
                )

            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            # Kill the runaway process so it does not leak
            with contextlib.suppress(Exception):
                proc.kill()
                await asyncio.wait_for(proc.wait(), timeout=5)
            return ToolOutput(
                text=f"Command timed out after {timeout_sec:.0f}s",
                error=True,
                metadata={"exit_code": -1, "timed_out": True},
            )
        except Exception as e:
            return ToolOutput(text=f"Error executing command: {e}", error=True)

        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")

        text_parts = []
        if out:
            if len(out) > 12000:
                out = out[:12000] + "\n... [stdout truncated]"
            text_parts.append(out)
        if err:
            if len(err) > 4000:
                err = err[:4000] + "\n... [stderr truncated]"
            text_parts.append(f"\n[stderr]\n{err}")

        output = "\n".join(text_parts).strip() or "(no output)"

        exit_code = proc.returncode
        record_shell_command(command, exit_code, output)

        return ToolOutput(
            text=output,
            title=f"$ {command[:80]}" + ("..." if len(command) > 80 else ""),
            metadata={
                "exit_code": proc.returncode,
                "command": command,
                "cwd": work_dir,
            },
            error=proc.returncode != 0,
        )


class PowerShellTool(ToolBase):
    name = "PowerShell"
    description = "Execute a PowerShell command and return its output."
    aliases = ["PS", "Pwsh"]
    timeout_sec = 620.0
    parameters = {
        "command": {
            "type": "string",
            "description": "PowerShell command or script block to execute",
        },
        "description": {
            "type": "string",
            "description": "Human-readable description of what this command does",
        },
        "timeout": {"type": "integer", "description": "Timeout in milliseconds (default 120000)"},
    }

    async def execute(
        self, command: str, description: str = "", timeout: int = 120000
    ) -> ToolOutput:
        timeout_sec = min(timeout, 600000) / 1000
        try:
            proc = await asyncio.create_subprocess_exec(
                "powershell.exe",
                "-NoProfile",
                "-Command",
                command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            # Kill the runaway process so it does not leak
            with contextlib.suppress(Exception):
                proc.kill()
                await asyncio.wait_for(proc.wait(), timeout=5)
            return ToolOutput(text="PowerShell command timed out", error=True)

        out = stdout.decode("utf-8", errors="replace")[:12000]
        err = stderr.decode("utf-8", errors="replace")[:4000]

        parts = [out] if out else []
        if err:
            parts.append(f"\n[stderr]\n{err}")
        output = "\n".join(parts).strip() or "(no output)"
        exit_code = proc.returncode
        record_shell_command(command, exit_code, output)

        return ToolOutput(
            text=output,
            title=f"PS> {command[:80]}",
            metadata={"exit_code": proc.returncode},
            error=proc.returncode != 0,
        )


register_tool(BashTool())
register_tool(PowerShellTool())
