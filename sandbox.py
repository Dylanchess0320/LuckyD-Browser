"""
Sandboxed command execution for Windows.
Guards against destructive operations — the heart of principle #3.
"""

import platform
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import NamedTuple

from config import COMMAND_TIMEOUT_SEC, MAX_OUTPUT_CHARS, PROJECT_DIR

# ── Safety: Blocklist ──────────────────────────────────────────────────
# Any command containing these patterns is blocked (case-insensitive)
# NOTE: kept in sync with tools/bash_tool.py's BLOCKED_PATTERNS — these are
# two independent implementations (one gates the Bash tool the agent calls,
# this one gates the sandbox module directly), so a pattern added to one
# without the other reopens exactly the kind of gap that let bash-only
# patterns miss every PowerShell-native destructive cmdlet.
BLOCKLIST = [
    # Destructive filesystem ops
    r"rm\s+-rf\s+/",
    r"rd\s+/s\s+/q\s+c:\\",
    r"format\s",
    r"del\s+/f\s+/s",
    r"deltree",
    # PowerShell-native destructive cmdlets
    r"remove-item\s+.*-recurse\s+.*-force",
    r"remove-item\s+.*-force\s+.*-recurse",
    r"clear-disk",
    r"clear-recyclebin\s+.*-force",
    r"initialize-disk",
    r"format-volume",
    r"remove-partition",
    # Dangerous system ops
    r"shutdown",
    r"restart-computer",
    r"stop-computer",
    r"bcdedit",
    r">\s*/dev/sda",
    r"dd\s+if=",
    r"mkfs",
    # Fork bombs / resource exhaustion
    r":\(\)\s*\{",
    r"while\s*\(\s*1\s*\)",
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
    # Python self-destruct
    r"os\.remove\(",
    r"shutil\.rmtree\(['\"]/['\"]",
]

# Patterns that are allowed (these override the blocklist)
ALLOWLIST = [
    # Allow deleting files in project dir (not system)
    # handled by path checks below
]


class CommandResult(NamedTuple):
    exit_code: int
    stdout: str
    stderr: str
    blocked: bool
    duration_ms: int


def is_safe(command: str, cwd: str | None = None) -> tuple[bool, str]:
    """
    Check if a command is safe to execute.
    Returns (is_safe, reason).
    """
    cmd_lower = command.lower().strip()

    # ── Check blocklist ────────────────────────────────────────────
    for pattern in BLOCKLIST:
        if re.search(pattern, cmd_lower):
            return False, f"BLOCKED: matches dangerous pattern '{pattern}'"

    # ── Check for path escapes ─────────────────────────────────────
    # Prevent writing outside project dir
    dangerous_paths = [
        r"C:\Windows",
        r"C:\WINDOWS",
        r"/Windows",
        r"C:\Program Files",
        r"C:\ProgramData",
        r"/etc/",
        r"/bin/",
        r"/boot/",
        r"/root",
        r"~/",
        r"$HOME",
        r"%SystemRoot%",
        r"%ProgramFiles%",
        r"%AppData%",
    ]

    # Only flag path escapes if they're in write/destructive context
    destructive_ops = r"(>|>>|rm\s|del\s|rd\s|rmdir|mv\s|move\s|copy\s|xcopy)"  # Added delete ops
    if re.search(destructive_ops, cmd_lower):
        for path in dangerous_paths:
            if path.lower() in cmd_lower:
                return False, f"BLOCKED: references system path '{path}'"

    return True, "ok"


def execute(command: str, cwd: str | None = None, timeout: int | None = None) -> CommandResult:
    """
    Execute a shell command safely.
    Returns a CommandResult with exit_code, stdout, stderr, and blocked flag.
    """
    if timeout is None:
        timeout = COMMAND_TIMEOUT_SEC

    cwd = cwd or str(PROJECT_DIR)

    # Safety check
    safe, reason = is_safe(command, cwd)
    if not safe:
        return CommandResult(-1, "", reason, True, 0)

    import time

    start = time.time()

    try:
        # Use the native shell: PowerShell on Windows, bash on Linux/macOS
        if platform.system() == "Windows":
            shell_cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command]
        else:
            shell_cmd = ["bash", "-c", command]

        proc = subprocess.run(
            shell_cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
        elapsed = int((time.time() - start) * 1000)

        stdout = proc.stdout or ""
        stderr = proc.stderr or ""

        # Truncate
        if len(stdout) > MAX_OUTPUT_CHARS:
            stdout = (
                stdout[:MAX_OUTPUT_CHARS]
                + f"\n... [truncated {len(stdout) - MAX_OUTPUT_CHARS} chars]"
            )
        if len(stderr) > MAX_OUTPUT_CHARS:
            stderr = (
                stderr[:MAX_OUTPUT_CHARS]
                + f"\n... [truncated {len(stderr) - MAX_OUTPUT_CHARS} chars]"
            )

        return CommandResult(proc.returncode, stdout, stderr, False, elapsed)

    except subprocess.TimeoutExpired:
        elapsed = int((time.time() - start) * 1000)
        return CommandResult(-1, "", f"TIMEOUT: command exceeded {timeout}s limit", False, elapsed)
    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        return CommandResult(-1, "", f"ERROR: {e}", False, elapsed)


def execute_batch(commands: list[str], cwd: str | None = None) -> list[CommandResult]:
    """Execute a list of commands sequentially, stopping on first failure."""
    results = []
    for cmd in commands:
        result = execute(cmd, cwd=cwd)
        results.append(result)
        if result.exit_code != 0 and not result.blocked:
            break  # stop on first failure
    return results


# ── E2B-style Isolated Execution ───────────────────────────────────────

import tempfile
from contextlib import contextmanager


@contextmanager
def isolated_workspace(prefix: str = "agent_sandbox_"):
    """Create an isolated temporary workspace for safe code execution.

    The workspace is a fresh temp directory that gets cleaned up on exit.
    Commands run inside this directory, preventing accidental damage
    to the real project files.

    Usage:
        with isolated_workspace() as workspace:
            result = execute("python -c 'print(1+1)'", cwd=workspace)
            print(result.stdout)  # "2"
        # workspace is automatically cleaned up
    """
    workspace = tempfile.mkdtemp(prefix=prefix)
    try:
        yield workspace
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def execute_isolated(
    command: str,
    timeout: int | None = None,
    copy_files: list[str] | None = None,
) -> CommandResult:
    """Execute a command in an isolated workspace.

    Args:
        command: The shell command to run
        timeout: Max execution time in seconds
        copy_files: List of file paths to copy into the sandbox before running

    Returns:
        CommandResult with output from the isolated execution
    """
    with isolated_workspace() as workspace:
        # Copy requested files into the sandbox
        if copy_files:
            for file_path in copy_files:
                src = Path(file_path)
                if src.exists():
                    dst = Path(workspace) / src.name
                    if src.is_file():
                        shutil.copy2(src, dst)
                    elif src.is_dir():
                        shutil.copytree(src, dst, dirs_exist_ok=True)

        return execute(command, cwd=workspace, timeout=timeout)


def execute_python_isolated(
    code: str,
    timeout: int | None = None,
    copy_files: list[str] | None = None,
) -> CommandResult:
    """Execute Python code in an isolated workspace.

    Writes the code to a temp file and runs it with the system Python.
    """
    with isolated_workspace() as workspace:
        # Write the code to a file
        script_path = Path(workspace) / "_sandbox_script.py"
        script_path.write_text(code, encoding="utf-8")

        # Copy any additional files
        if copy_files:
            for file_path in copy_files:
                src = Path(file_path)
                if src.exists():
                    dst = Path(workspace) / src.name
                    if src.is_file():
                        shutil.copy2(src, dst)

        # Run the script
        return execute(f'python "{script_path}"', cwd=workspace, timeout=timeout)


def execute_with_rollback(
    command: str,
    cwd: str | None = None,
    timeout: int | None = None,
    checkpoint_dir: str | None = None,
) -> tuple[CommandResult, str | None]:
    """Execute a command with automatic rollback on failure.

    Creates a checkpoint of the working directory before execution.
    If the command fails, restores from the checkpoint.

    Returns:
        (result, checkpoint_path) — checkpoint_path is None if no checkpoint was created
    """
    cwd = cwd or str(PROJECT_DIR)

    # Create checkpoint
    checkpoint_path = None
    if checkpoint_dir:
        checkpoint_path = Path(checkpoint_dir) / f"checkpoint_{int(time.time())}"
        try:
            shutil.copytree(
                cwd,
                checkpoint_path,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(".git", "__pycache__", "node_modules", ".venv"),
            )
        except Exception:
            checkpoint_path = None

    # Execute
    result = execute(command, cwd=cwd, timeout=timeout)

    # Rollback on failure
    if result.exit_code != 0 and not result.blocked and checkpoint_path:
        try:
            shutil.rmtree(cwd, ignore_errors=True)
            shutil.copytree(checkpoint_path, cwd, dirs_exist_ok=True)
        except Exception:
            pass  # Rollback failed — leave as-is

    return result, str(checkpoint_path) if checkpoint_path else None


# ── Resource Limits ────────────────────────────────────────────────────


class ResourceLimits:
    """Track and enforce resource limits for sandboxed execution."""

    def __init__(
        self,
        max_memory_mb: int = 512,
        max_cpu_percent: int = 80,
        max_disk_mb: int = 1024,
        max_network_requests: int = 0,  # 0 = blocked
    ):
        self.max_memory_mb = max_memory_mb
        self.max_cpu_percent = max_cpu_percent
        self.max_disk_mb = max_disk_mb
        self.max_network_requests = max_network_requests

    def check_command(self, command: str) -> tuple[bool, str]:
        """Check if a command would violate resource limits."""
        cmd_lower = command.lower()

        # Network check
        if self.max_network_requests == 0:
            network_indicators = [
                "curl",
                "wget",
                "http",
                "fetch",
                "requests.get",
                "requests.post",
                "urllib",
                "aiohttp",
                "socket.connect",
            ]
            for indicator in network_indicators:
                if indicator in cmd_lower:
                    return False, f"BLOCKED: network access not allowed (found '{indicator}')"

        # Disk check
        if self.max_disk_mb < 100:
            write_indicators = [">", ">>", "write", "save", "download"]
            for indicator in write_indicators:
                if indicator in cmd_lower:
                    return False, f"BLOCKED: disk write not allowed (limit: {self.max_disk_mb}MB)"

        return True, "ok"

    def apply_to_process(self, proc):
        """Apply resource limits to a running process (Unix only)."""
        import platform

        if platform.system() != "Windows":
            try:
                import resource

                # Memory limit (soft + hard)
                mem_bytes = self.max_memory_mb * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
                # CPU time limit
                cpu_seconds = int(self.max_cpu_percent / 100 * 60)  # rough
                resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
            except Exception:
                pass  # Limits not supported on this platform


# ── Pre-built Sandboxes ────────────────────────────────────────────────

SANDBOX_PRESETS = {
    "strict": ResourceLimits(
        max_memory_mb=256,
        max_cpu_percent=50,
        max_disk_mb=100,
        max_network_requests=0,
    ),
    "standard": ResourceLimits(
        max_memory_mb=512,
        max_cpu_percent=80,
        max_disk_mb=1024,
        max_network_requests=0,
    ),
    "permissive": ResourceLimits(
        max_memory_mb=2048,
        max_cpu_percent=100,
        max_disk_mb=10240,
        max_network_requests=10,
    ),
}


def get_sandbox_preset(name: str = "standard") -> ResourceLimits:
    """Get a pre-configured sandbox preset."""
    return SANDBOX_PRESETS.get(name, SANDBOX_PRESETS["standard"])
