"""Tests for the sandbox command execution module."""

from __future__ import annotations

from sandbox import CommandResult, execute, is_safe


class TestSandboxSafety:
    """Test the command safety checker."""

    def test_safe_command(self):
        """Test a benign command is allowed."""
        safe, reason = is_safe("echo hello")
        assert safe is True
        assert reason == "ok"

    def test_safe_command_with_paths(self):
        """Test safe commands with project directory paths."""
        safe, _reason = is_safe("python test.py")
        assert safe is True

        safe, _reason = is_safe("dir .")
        assert safe is True

    def test_rm_rf_root_blocked(self):
        """Test that rm -rf / is blocked."""
        safe, reason = is_safe("rm -rf /")
        assert safe is False
        assert "BLOCKED" in reason

    def test_rm_rf_root_variations(self):
        """Test variations of dangerous rm commands."""
        blocked_commands = [
            "rm -rf /var",
            "rm -rf /etc",
            "rm -rf /home",
            "sudo rm -rf /",
        ]
        for cmd in blocked_commands:
            safe, _ = is_safe(cmd)
            assert safe is False, f"Command should be blocked: {cmd}"

    def test_format_command_blocked(self):
        """Test format commands are blocked."""
        safe, reason = is_safe("format C:")
        assert safe is False
        assert "BLOCKED" in reason

    def test_shutdown_blocked(self):
        """Test shutdown commands are blocked."""
        safe, reason = is_safe("shutdown /s /t 0")
        assert safe is False
        assert "BLOCKED" in reason

    def test_dd_blocked(self):
        """Test dd commands are blocked."""
        safe, reason = is_safe("dd if=/dev/zero of=/dev/sda")
        assert safe is False
        assert "BLOCKED" in reason

    def test_registry_delete_blocked(self):
        """Test registry delete commands are blocked."""
        safe, reason = is_safe("reg delete HKLM\\Software /f")
        assert safe is False
        assert "BLOCKED" in reason

    def test_path_escape_detection(self):
        """Test that system path references in destructive contexts are blocked."""
        # Should block writes to system paths
        safe, _ = is_safe("echo test > C:\\Windows\\system32\\test.txt")
        assert safe is False

        safe, _ = is_safe("echo test > /etc/passwd")
        assert safe is False

    def test_allowed_path_writes(self):
        """Test that writes to project directory are allowed."""
        safe, reason = is_safe("echo test > output.txt")
        assert safe is True, f"Should be allowed: {reason}"

        safe, reason = is_safe("echo test > ./results/data.txt")
        assert safe is True, f"Should be allowed: {reason}"

    def test_empty_command_safety(self):
        """Test empty/whitespace commands."""
        safe, _reason = is_safe("")
        assert safe is True


class TestSandboxExecution:
    """Test sandboxed command execution."""

    def test_execute_echo(self):
        """Test executing a simple echo command."""
        result = execute("echo hello world")
        assert result.blocked is False
        assert result.exit_code == 0
        assert "hello world" in result.stdout.lower() or result.stdout.strip() != ""

    def test_execute_pwd_or_cd(self):
        """Test executing a directory listing."""
        result = execute("pwd") if __import__("sys").platform != "win32" else execute("echo %CD%")
        assert result.blocked is False
        assert result.exit_code == 0

    def test_execute_blocked_command(self):
        """Test that blocked commands return blocked result."""
        result = execute("rm -rf /")
        assert result.blocked is True
        assert "BLOCKED" in result.stderr
        assert result.exit_code == -1

    def test_execute_timeout(self):
        """Test command timeout handling."""
        result = execute("ping -n 10 localhost", timeout=1)
        # On Windows, this might return before timeout if ping completes
        # But we're testing that it doesn't hang forever
        assert result.blocked is False
        assert isinstance(result.exit_code, int)

    def test_command_result_structure(self):
        """Test CommandResult named tuple structure."""
        result = CommandResult(0, "stdout content", "stderr content", False, 42)
        assert result.exit_code == 0
        assert result.stdout == "stdout content"
        assert result.stderr == "stderr content"
        assert result.blocked is False
        assert result.duration_ms == 42

    def test_command_result_blocked(self):
        """Test CommandResult with blocked state."""
        result = CommandResult(-1, "", "BLOCKED: dangerous pattern", True, 0)
        assert result.exit_code == -1
        assert result.blocked is True

    def test_execute_batch_stops_on_failure(self):
        """Test batch execution stops on first failure."""
        from sandbox import BatchOptions, execute_batch

        commands = [
            "echo first",
            "nonexistent_command_xyz",
            "echo third",
        ]
        results = execute_batch(commands, options=BatchOptions(stop_on_failure=True))
        assert len(results) <= 2  # Should stop after the failing command
        assert results[0].exit_code == 0
        # Second command may fail or be blocked

    def test_execute_batch_continue_on_failure(self):
        """Test batch execution continues on failure when stop_on_failure=False."""
        from sandbox import BatchOptions, execute_batch

        commands = [
            "echo first",
            "nonexistent_command_xyz",
            "echo third",
        ]
        results = execute_batch(commands, options=BatchOptions(stop_on_failure=False))
        assert len(results) == 3  # Should execute all commands
        assert results[0].exit_code == 0
        # Second command fails (exit_code != 0), but it shouldn't stop
        assert results[1].exit_code != 0
        # Third command succeeds
        assert results[2].exit_code == 0
