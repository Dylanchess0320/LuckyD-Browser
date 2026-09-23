"""Coverage push: tools/bash_tool.py, git_tools.py, schedule_tools.py,
utility_tools.py remaining paths.

bash_tool: sandbox-import fallback, _is_safe fallback scan + segment edge
cases, SAFETY BLOCK, Windows shell branch, spawn failure, stdout/stderr
truncation, _kill_process_tree Windows branches, PowerShell
communicate/timeout/startup paths.
git_tools: _run_git spawn failures, and real tmp_path git repos (status /
diff / add / push / branch / PR incl. a fake `gh` on PATH).
schedule_tools: isolated SQLite store; list/get/update/delete/enable/disable
edge cases, run-now wiring (run_schedule monkeypatched), history + digest.
utility_tools: sandbox-import fallback, Diff dir-read error, Process
start/list/read (incl. select-OSError fallback)/kill, Notify Windows toast,
Watch check/wait/timeout/error paths. No network; harmless commands only.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

import tools.bash_tool as bash_tool
import tools.git_tools as git_tools
import tools.schedule_tools as schedule_tools
import tools.utility_tools as utility_tools
from core.scheduler import ScheduleStore
from tools.bash_tool import BashTool, PowerShellTool
from tools.utility_tools import DiffTool, NotifyTool, ProcessTool, WatchTool

# ── Environment guards ────────────────────────────────────────────────
# A few tests drive real POSIX utilities (`cat`, `ls`, `sleep`, `true`).
# Those are present on the Linux runners and on GitHub's Windows images
# (Git's usr/bin is on PATH there), but not on a stock Windows box — skip
# instead of failing when a utility is genuinely unavailable.
requires_cat = pytest.mark.skipif(shutil.which("cat") is None, reason="needs GNU cat on PATH")
requires_ls = pytest.mark.skipif(shutil.which("ls") is None, reason="needs GNU ls on PATH")
requires_sleep = pytest.mark.skipif(shutil.which("sleep") is None, reason="needs GNU sleep on PATH")
requires_true = pytest.mark.skipif(shutil.which("true") is None, reason="needs GNU true on PATH")
requires_posix_killpg = pytest.mark.skipif(
    os.name == "nt" or shutil.which("sleep") is None,
    reason="POSIX process groups (start_new_session/killpg) only",
)


class _FakeProc:
    """Minimal stand-in for an asyncio subprocess."""

    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
        self._out, self._err = stdout, stderr
        self.returncode = returncode
        self.pid = 424242
        self.killed = False

    async def communicate(self):
        return self._out, self._err

    async def wait(self):
        return self.returncode

    def kill(self):
        self.killed = True


# ═══════════════════════════ bash_tool ═══════════════════════════


def test_bash_sandbox_import_fallback(monkeypatch) -> None:
    """Lines 27-29: sandbox unimportable -> local blocklist fallback."""
    import importlib

    monkeypatch.setitem(sys.modules, "sandbox", None)
    importlib.reload(bash_tool)
    try:
        assert bash_tool._sandbox_is_safe is None
        assert bash_tool.BLOCKED_PATTERNS == []
        ok, _ = bash_tool.BashTool()._is_safe("echo hi")
        assert ok is True
    finally:
        monkeypatch.undo()
        importlib.reload(bash_tool)
    assert bash_tool._sandbox_is_safe is not None


def test_is_safe_fallback_pattern_scan(monkeypatch) -> None:
    monkeypatch.setattr(bash_tool, "_sandbox_is_safe", None)
    monkeypatch.setattr(bash_tool, "BLOCKED_PATTERNS", [r"rm\s+-rf"])
    tool = bash_tool.BashTool()
    ok, reason = tool._is_safe("echo hello && rm -rf /tmp/x")
    assert ok is False and "Blocked dangerous pattern" in reason
    assert tool._is_safe("echo hello") == (True, "")


def test_is_safe_segment_edge_cases(monkeypatch) -> None:
    monkeypatch.setattr(bash_tool, "_sandbox_is_safe", None)
    monkeypatch.setattr(bash_tool, "BLOCKED_PATTERNS", [])
    tool = bash_tool.BashTool()
    assert tool._is_safe("echo hi && ")[0] is True  # line 201: empty segment
    assert tool._is_safe("/bin/echo hi")[0] is True  # line 206: absolute path
    ok, reason = tool._is_safe("echo hi && frobnicate --x")
    assert ok is False and "frobnicate" in reason  # line 208


async def test_execute_safety_block(monkeypatch) -> None:
    monkeypatch.setattr(BashTool, "_is_safe", lambda self, cmd: (False, "test block"))
    out = await BashTool().execute(command="whatever", description="d")
    assert out.error is True
    assert "SAFETY BLOCK" in out.text and "test block" in out.text


async def test_execute_windows_shell_branch(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(bash_tool, "IS_WINDOWS", True)

    async def _fake_shell(cmd, **kw):
        return _FakeProc(b"win-out", b"", 0)

    monkeypatch.setattr(bash_tool.asyncio, "create_subprocess_shell", _fake_shell)
    out = await BashTool().execute(command="echo hi", description="d", cwd=str(tmp_path))
    assert out.error is False and out.text == "win-out"


async def test_execute_spawn_failure(monkeypatch) -> None:
    async def _boom(*a, **k):
        raise RuntimeError("spawn failed")

    # BashTool.execute spawns via create_subprocess_shell on Windows and
    # create_subprocess_exec("bash", "-c", ...) on POSIX — fake both so the
    # spawn-failure handler is exercised on either platform.
    monkeypatch.setattr(bash_tool.asyncio, "create_subprocess_exec", _boom)
    monkeypatch.setattr(bash_tool.asyncio, "create_subprocess_shell", _boom)
    out = await BashTool().execute(command="echo hi", description="d")
    assert out.error is True
    assert "Error executing command: spawn failed" in out.text


@requires_cat
async def test_execute_truncates_long_stdout(tmp_path) -> None:
    big = tmp_path / "big.txt"
    big.write_text("y" * 20000)
    out = await BashTool().execute(
        command=f"cat {big}",
        description="long output",
        timeout=30000,
    )
    assert out.error is False
    assert "[stdout truncated]" in out.text
    assert len(out.text) < 20000


@requires_ls
async def test_execute_truncates_long_stderr() -> None:
    paths = " ".join(f"/nonexistent-path-{i}" for i in range(120))
    out = await BashTool().execute(
        command=f"ls {paths}",
        description="long stderr",
        timeout=30000,
    )
    assert "[stderr]" in out.text and "truncated" in out.text


async def test_execute_echo_roundtrip(tmp_path) -> None:
    out = await BashTool().execute(command="echo hello", description="d", cwd=str(tmp_path))
    assert out.error is False and out.text == "hello"
    assert out.metadata["exit_code"] == 0


async def test_kill_process_tree_windows_taskkill(monkeypatch) -> None:
    monkeypatch.setattr(bash_tool, "IS_WINDOWS", True)
    calls = []

    def _fake_run(*a, **k):
        calls.append(a)
        return subprocess.CompletedProcess(a[0], 0)

    monkeypatch.setattr(bash_tool.subprocess, "run", _fake_run)
    proc = _FakeProc()
    await bash_tool._kill_process_tree(proc)
    assert calls and list(calls[0][0][:4]) == ["taskkill", "/F", "/T", "/PID"]
    assert proc.killed is False  # taskkill path succeeded; proc.kill() skipped


async def test_kill_process_tree_windows_taskkill_fails(monkeypatch) -> None:
    monkeypatch.setattr(bash_tool, "IS_WINDOWS", True)

    def _boom(*a, **k):
        raise FileNotFoundError("no taskkill")

    monkeypatch.setattr(bash_tool.subprocess, "run", _boom)
    proc = _FakeProc()
    await bash_tool._kill_process_tree(proc)
    assert proc.killed is True  # lines 156-158 fallback


@requires_posix_killpg
async def test_kill_process_tree_posix_killpg() -> None:
    proc = await asyncio.create_subprocess_exec(
        "sleep",
        "30",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    await bash_tool._kill_process_tree(proc)
    await asyncio.wait_for(proc.wait(), timeout=10)
    assert proc.returncode is not None


# ── PowerShell ───────────────────────────────────────────────────


async def test_powershell_success(monkeypatch) -> None:
    async def _fake_exec(*a, **k):
        return _FakeProc(b"ps-out", b"warn", 0)

    monkeypatch.setattr(bash_tool.asyncio, "create_subprocess_exec", _fake_exec)
    out = await PowerShellTool().execute(command="Get-ChildItem", timeout=10000)
    assert out.error is False
    assert "ps-out" in out.text and "[stderr]" in out.text


async def test_powershell_timeout(monkeypatch) -> None:
    proc = _FakeProc()

    async def _hang():
        raise asyncio.TimeoutError()

    proc.communicate = _hang

    async def _fake_exec(*a, **k):
        return proc

    monkeypatch.setattr(bash_tool.asyncio, "create_subprocess_exec", _fake_exec)
    out = await PowerShellTool().execute(command="Get-ChildItem", timeout=10000)
    assert out.error is True and "timed out" in out.text


async def test_powershell_startup_failure(monkeypatch) -> None:
    async def _boom(*a, **k):
        raise FileNotFoundError("no powershell")

    monkeypatch.setattr(bash_tool.asyncio, "create_subprocess_exec", _boom)
    out = await PowerShellTool().execute(command="Get-ChildItem", timeout=5000)
    assert out.error is True
    assert "Error executing PowerShell command" in out.text


# ═══════════════════════════ git_tools ═══════════════════════════


async def test_run_git_spawn_file_not_found(monkeypatch) -> None:
    async def _boom(*a, **k):
        raise FileNotFoundError("no git")

    monkeypatch.setattr(git_tools.asyncio, "create_subprocess_exec", _boom)
    out = await git_tools.GitStatus().execute()
    assert out.error is True and "git not found" in out.text


async def test_run_git_spawn_generic_error(monkeypatch) -> None:
    async def _boom(*a, **k):
        raise RuntimeError("weird spawn failure")

    monkeypatch.setattr(git_tools.asyncio, "create_subprocess_exec", _boom)
    out = await git_tools.GitStatus().execute()
    assert out.error is True and "weird spawn failure" in out.text


@pytest.fixture()
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Real git repo; tool calls are redirected into it (cwd override)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "a.txt").write_text("hello\n")

    real_run_git = git_tools._run_git

    async def _redirect(args, cwd="."):
        return await real_run_git(args, cwd=str(repo))

    monkeypatch.setattr(git_tools, "_run_git", _redirect)
    return repo


def _commit(repo: Path, msg: str = "init") -> None:
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)


def _add_remote(repo: Path, tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=repo, check=True)


async def test_git_status_clean(git_repo) -> None:
    _commit(git_repo)
    out = await git_tools.GitStatus().execute()
    assert out.error is False and out.text == "Working tree clean."


async def test_git_status_dirty(git_repo) -> None:
    _commit(git_repo)
    (git_repo / "a.txt").write_text("changed\n")
    out = await git_tools.GitStatus().execute()
    assert out.metadata["dirty"] is True and "a.txt" in out.text


async def test_git_status_not_a_repo(tmp_path, monkeypatch) -> None:
    real = git_tools._run_git
    monkeypatch.setattr(git_tools, "_run_git", lambda args, cwd=".": real(args, str(tmp_path)))
    out = await git_tools.GitStatus().execute()
    assert out.error is True


async def test_git_diff_staged(git_repo) -> None:
    (git_repo / "a.txt").write_text("v2\n")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True)
    out = await git_tools.GitDiff().execute(staged=True)
    assert out.error is False and "v2" in out.text


async def test_git_diff_no_changes(git_repo) -> None:
    _commit(git_repo)
    out = await git_tools.GitDiff().execute()
    assert out.text == "No changes."


async def test_git_diff_not_a_repo(tmp_path, monkeypatch) -> None:
    real = git_tools._run_git
    monkeypatch.setattr(git_tools, "_run_git", lambda args, cwd=".": real(args, str(tmp_path)))
    out = await git_tools.GitDiff().execute()
    assert out.error is True


async def test_git_add_files(git_repo) -> None:
    out = await git_tools.GitAdd().execute(files=["a.txt"])
    assert out.text == "Staged."
    code, staged, _ = await git_tools._run_git(["diff", "--cached", "--name-only"])
    assert code == 0 and "a.txt" in staged


async def test_git_add_not_a_repo(tmp_path, monkeypatch) -> None:
    real = git_tools._run_git
    monkeypatch.setattr(git_tools, "_run_git", lambda args, cwd=".": real(args, str(tmp_path)))
    out = await git_tools.GitAdd().execute()
    assert out.error is True


async def test_git_push_branch(git_repo, tmp_path) -> None:
    _add_remote(git_repo, tmp_path)
    _commit(git_repo)
    out = await git_tools.GitPush().execute(branch="master")
    assert out.error is False


async def test_git_push_no_upstream_fails(git_repo, tmp_path) -> None:
    _add_remote(git_repo, tmp_path)
    _commit(git_repo)
    out = await git_tools.GitPush().execute()
    assert out.error is True  # 'git push' with no upstream configured


async def test_git_branch_not_a_repo(tmp_path, monkeypatch) -> None:
    real = git_tools._run_git
    monkeypatch.setattr(git_tools, "_run_git", lambda args, cwd=".": real(args, str(tmp_path)))
    out = await git_tools.GitBranch().execute()
    assert out.error is True


async def test_git_branch_lists(git_repo) -> None:
    _commit(git_repo)
    out = await git_tools.GitBranch().execute()
    assert out.error is False and "master" in out.text


async def test_git_pr_push_failure(git_repo) -> None:
    _commit(git_repo)  # no remote -> `git push origin HEAD` fails for real
    out = await git_tools.GitPR().execute(title="T")
    assert out.error is True and out.text.startswith("Push failed:")


def _patch_gh(
    monkeypatch,
    stdout: bytes = b"",
    stderr: bytes = b"",
    returncode: int = 0,
    not_found: bool = False,
) -> None:
    """Route `gh ...` spawns to a fake proc; let everything else (git) run for real.

    A fake `gh` shell script on PATH is not executable on Windows (extensionless
    scripts don't run via CreateProcess, and CI runners may also have a real gh
    earlier on PATH), so intercept the spawn instead. The gh success / failure /
    not-found branches in GitPR are still exercised for real.
    """
    real_exec = git_tools.asyncio.create_subprocess_exec

    async def _fake_exec(*args, **kwargs):
        if args and os.path.basename(str(args[0])).lower() in ("gh", "gh.exe"):
            if not_found:
                raise FileNotFoundError("gh")
            return _FakeProc(stdout, stderr, returncode)
        return await real_exec(*args, **kwargs)

    monkeypatch.setattr(git_tools.asyncio, "create_subprocess_exec", _fake_exec)


async def test_git_pr_full_flow_with_fake_gh(git_repo, tmp_path, monkeypatch) -> None:
    _add_remote(git_repo, tmp_path)
    _commit(git_repo)
    _patch_gh(monkeypatch, stdout=b"https://github.com/x/y/pull/1\n")
    out = await git_tools.GitPR().execute(title="T", body="B")
    assert out.error is False
    assert "pull/1" in out.text and out.title == "PR: T"


async def test_git_pr_gh_failure(git_repo, tmp_path, monkeypatch) -> None:
    _add_remote(git_repo, tmp_path)
    _commit(git_repo)
    _patch_gh(monkeypatch, stderr=b"denied\n", returncode=1)
    out = await git_tools.GitPR().execute(title="T")
    assert out.error is True and "gh CLI error: denied" in out.text


# ═══════════════════════════ schedule_tools ═══════════════════════════


@pytest.fixture()
def sched_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ScheduleStore:
    store = ScheduleStore(path=tmp_path / "schedules.db")
    monkeypatch.setattr(schedule_tools, "_store", store)
    return store


async def _make_schedule(name: str = "morning") -> str:
    out = await schedule_tools.ScheduleCreateTool().execute(
        name=name, prompt="do the thing", cron="0 7 * * *"
    )
    assert out.error is False, out.text
    return out.metadata["schedule"]["id"]


async def test_schedule_list_empty(sched_store) -> None:
    out = await schedule_tools.ScheduleListTool().execute()
    assert out.text == "No schedules yet. Create one with ScheduleCreate."


async def test_schedule_get_unknown(sched_store) -> None:
    out = await schedule_tools.ScheduleGetTool().execute(id="nope")
    assert out.error is True and "unknown schedule" in out.text


async def test_schedule_get_ok(sched_store) -> None:
    sid = await _make_schedule()
    out = await schedule_tools.ScheduleGetTool().execute(id=sid)
    assert out.error is False and "morning" in out.text and "do the thing" in out.text


async def test_schedule_update_unknown(sched_store) -> None:
    out = await schedule_tools.ScheduleUpdateTool().execute(id="nope", name="x")
    assert out.error is True and "unknown schedule" in out.text


async def test_schedule_update_skips_none_and_empty(sched_store) -> None:
    sid = await _make_schedule()
    out = await schedule_tools.ScheduleUpdateTool().execute(id=sid, name=None, prompt="")
    assert out.error is False
    s = sched_store.get(sid)
    assert s.name == "morning" and s.prompt == "do the thing"


async def test_schedule_update_name_and_prompt(sched_store) -> None:
    sid = await _make_schedule()
    out = await schedule_tools.ScheduleUpdateTool().execute(
        id=sid, name="evening", prompt="do the other thing"
    )
    assert out.error is False
    s = sched_store.get(sid)
    assert s.name == "evening" and s.prompt == "do the other thing"


async def test_schedule_update_numeric_fields(sched_store) -> None:
    sid = await _make_schedule()
    out = await schedule_tools.ScheduleUpdateTool().execute(
        id=sid, max_turns=10, max_runtime_minutes=5, max_retries=0
    )
    assert out.error is False
    s = sched_store.get(sid)
    assert s.max_turns == 10 and s.max_runtime_sec == 300 and s.max_retries == 0


async def test_schedule_update_timing_switch_recomputes_next_run(sched_store) -> None:
    sid = await _make_schedule()
    out = await schedule_tools.ScheduleUpdateTool().execute(id=sid, every_minutes=30)
    assert out.error is False
    s = sched_store.get(sid)
    assert s.kind == "every" and s.every_minutes == 30
    assert s.next_run_at  # recomputed


async def test_schedule_update_invalid_scopes(sched_store) -> None:
    sid = await _make_schedule()
    out = await schedule_tools.ScheduleUpdateTool().execute(id=sid, allow_scopes="not json")
    assert out.error is True


async def test_schedule_delete_unknown(sched_store) -> None:
    out = await schedule_tools.ScheduleDeleteTool().execute(id="nope")
    assert out.error is True and "unknown schedule" in out.text


async def test_schedule_delete_ok(sched_store) -> None:
    sid = await _make_schedule()
    out = await schedule_tools.ScheduleDeleteTool().execute(id=sid)
    assert out.error is False and sid in out.text
    assert sched_store.get(sid) is None


async def test_schedule_enable_unknown(sched_store) -> None:
    out = await schedule_tools.ScheduleEnableTool().execute(id="nope")
    assert out.error is True and "unknown schedule" in out.text


async def test_schedule_enable_recomputes_missing_next_run(sched_store) -> None:
    sid = await _make_schedule()
    s = sched_store.get(sid)
    s.next_run_at = ""
    sched_store.update(s)
    out = await schedule_tools.ScheduleEnableTool().execute(id=sid)
    assert out.error is False
    assert sched_store.get(sid).next_run_at


async def test_schedule_disable_unknown(sched_store) -> None:
    out = await schedule_tools.ScheduleDisableTool().execute(id="nope")
    assert out.error is True and "unknown schedule" in out.text


async def test_schedule_disable_ok(sched_store) -> None:
    sid = await _make_schedule()
    out = await schedule_tools.ScheduleDisableTool().execute(id=sid)
    assert out.error is False and "morning" in out.text
    assert sched_store.get(sid).enabled is False


async def test_schedule_run_now_success(sched_store, monkeypatch) -> None:
    import core.schedule_runner as runner

    sid = await _make_schedule()

    def _fake_run(store, schedule_id, **kw):
        assert kw.get("force") is True
        return {
            "run_id": "r1",
            "status": "ok",
            "summary": "did it",
            "next_run_at": "tomorrow",
        }

    monkeypatch.setattr(runner, "run_schedule", _fake_run)
    out = await schedule_tools.ScheduleRunNowTool().execute(id=sid)
    assert out.error is False and "finished: ok" in out.text


async def test_schedule_run_now_value_error(sched_store, monkeypatch) -> None:
    import core.schedule_runner as runner

    sid = await _make_schedule()

    def _boom(store, schedule_id, **kw):
        raise ValueError("cannot run now")

    monkeypatch.setattr(runner, "run_schedule", _boom)
    out = await schedule_tools.ScheduleRunNowTool().execute(id=sid)
    assert out.error is True and "cannot run now" in out.text


async def test_schedule_run_now_failed_status(sched_store, monkeypatch) -> None:
    import core.schedule_runner as runner

    sid = await _make_schedule()
    monkeypatch.setattr(
        runner,
        "run_schedule",
        lambda store, schedule_id, **kw: {
            "run_id": "r2",
            "status": "error",
            "summary": "",
            "error": "boom",
            "next_run_at": None,
        },
    )
    out = await schedule_tools.ScheduleRunNowTool().execute(id=sid)
    assert out.error is True and "finished: error" in out.text


async def test_schedule_history_empty(sched_store) -> None:
    out = await schedule_tools.ScheduleHistoryTool().execute()
    assert out.text == "No runs recorded yet."


async def test_schedule_history_with_runs(sched_store) -> None:
    sid = await _make_schedule()
    run_id = sched_store.record_run_start(sid, "morning", attempt=1)
    sched_store.record_run_end(run_id, status="ok", summary="all good", duration_sec=3.0)
    out = await schedule_tools.ScheduleHistoryTool().execute(limit=500)
    assert out.error is False and "all good" in out.text and "morning" in out.text


async def test_schedule_digest_empty(sched_store) -> None:
    out = await schedule_tools.ScheduleDigestTool().execute()
    assert "Nothing new" in out.text


async def test_schedule_digest_with_runs(sched_store) -> None:
    sid = await _make_schedule()
    r1 = sched_store.record_run_start(sid, "morning")
    sched_store.record_run_end(r1, status="ok", summary="fine")
    r2 = sched_store.record_run_start(sid, "morning")
    sched_store.record_run_end(r2, status="error", summary="", error="boom")
    out = await schedule_tools.ScheduleDigestTool().execute()
    assert "Overnight digest: 2 run(s), 1 ok, 1 need attention." in out.text
    assert "✅" in out.text and "⚠️" in out.text
    # Reading marks it seen: second read is empty.
    out2 = await schedule_tools.ScheduleDigestTool().execute()
    assert "Nothing new" in out2.text


# ═══════════════════════════ utility_tools ═══════════════════════════


def test_utility_sandbox_import_fallback(monkeypatch) -> None:
    """Lines 19-20: sandbox unimportable -> _sandbox_is_safe is None."""
    import importlib

    monkeypatch.setitem(sys.modules, "sandbox", None)
    importlib.reload(utility_tools)
    try:
        assert utility_tools._sandbox_is_safe is None
    finally:
        monkeypatch.undo()
        importlib.reload(utility_tools)
    assert utility_tools._sandbox_is_safe is not None


async def test_diff_file_vs_file_directory_errors(tmp_path) -> None:
    d = tmp_path / "adir"
    d.mkdir()
    f = tmp_path / "b.txt"
    f.write_text("x\n")
    out = await DiffTool().execute(mode="file_vs_file", file_path=str(d), file_path_b=str(f))
    assert out.error is True and "Diff error" in out.text


async def test_diff_string_vs_string(tmp_path) -> None:
    out = await DiffTool().execute(mode="string_vs_string", string_a="a\n", string_b="b\n")
    assert out.error is False and "-a" in out.text and "+b" in out.text


# ── ProcessTool ────────────────────────────────────────────────


async def test_process_list_shows_started_process(tmp_path) -> None:
    tool = ProcessTool()
    started = await tool.execute(op="start", command="sleep 30", cwd=str(tmp_path))
    assert started.error is False
    pid = started.metadata["process_id"]
    try:
        listed = await tool.execute(op="list")
        assert pid in listed.text
        assert "Processes (1)" in listed.title
        status = await tool.execute(op="status", process_id=pid)
        assert "Running" in status.text
    finally:
        killed = await tool.execute(op="kill", process_id=pid)
        assert f"Killed: {pid}" in killed.text


@requires_true
async def test_process_read_no_output_uses_select_timeout() -> None:
    tool = ProcessTool()
    started = await tool.execute(op="start", command="true")
    assert started.error is False
    pid = started.metadata["process_id"]
    try:
        await asyncio.sleep(0.5)  # let it exit with no output
        out = await tool.execute(op="read", process_id=pid)
        assert out.text == "(no output)"  # select.select timed out -> break
    finally:
        await tool.execute(op="kill", process_id=pid)


class _FakeStdout:
    def fileno(self):
        raise OSError("not a socket")

    def readline(self):
        return ""


class _FakeProcHandle:
    def __init__(self):
        self.stdout = _FakeStdout()
        self.args = "fake-cmd"

    def poll(self):
        return 0


async def test_process_read_stdout_oserror_fallback() -> None:
    """select.select raising OSError -> blocking-readline fallback path."""
    utility_tools._processes["fake-pid"] = _FakeProcHandle()
    utility_tools._process_outputs["fake-pid"] = []
    try:
        out = await ProcessTool().execute(op="read", process_id="fake-pid")
        assert out.text == "(no output)"
    finally:
        utility_tools._processes.pop("fake-pid", None)
        utility_tools._process_outputs.pop("fake-pid", None)


async def test_process_start_bad_cwd_errors() -> None:
    out = await ProcessTool().execute(op="start", command="echo hi", cwd="/nonexistent-xyz-123")
    assert out.error is True and "Process error" in out.text


async def test_process_start_refused_by_sandbox(monkeypatch) -> None:
    monkeypatch.setattr(utility_tools, "_sandbox_is_safe", lambda cmd, cwd=None: (False, "nope"))
    out = await ProcessTool().execute(op="start", command="echo hi")
    assert out.error is True and "Refused to start background process: nope" in out.text


async def test_process_start_without_sandbox_gate(monkeypatch) -> None:
    monkeypatch.setattr(utility_tools, "_sandbox_is_safe", None)
    tool = ProcessTool()
    started = await tool.execute(op="start", command="true")
    assert started.error is False
    await tool.execute(op="kill", process_id=started.metadata["process_id"])


async def test_process_read_clears_buffer_by_default(tmp_path) -> None:
    tool = ProcessTool()
    started = await tool.execute(op="start", command="echo hello", cwd=str(tmp_path))
    pid = started.metadata["process_id"]
    try:
        await asyncio.sleep(0.5)
        first = await tool.execute(op="read", process_id=pid)
        assert "hello" in first.text
        second = await tool.execute(op="read", process_id=pid)
        assert second.text == "(no output)"  # cleared after first read
        third = await tool.execute(op="read", process_id=pid, clear=False)
        assert third.text == "(no output)"
    finally:
        await tool.execute(op="kill", process_id=pid)


async def test_process_unknown_op() -> None:
    out = await ProcessTool().execute(op="bogus")
    assert out.error is True and "Unknown op" in out.text


# ── NotifyTool ─────────────────────────────────────────────────


async def test_notify_windows_toast_branch(monkeypatch) -> None:
    import platform

    fake = types.ModuleType("win10toast")
    calls = []

    class _Toaster:
        def show_toast(self, title, msg, duration=5, threaded=True):
            calls.append((title, msg, duration, threaded))

    fake.ToastNotifier = _Toaster
    monkeypatch.setitem(sys.modules, "win10toast", fake)
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    out = await NotifyTool().execute(message="done", title="T", level="success")
    assert out.error is False and "Notification sent" in out.text
    assert calls and calls[0][1].startswith("✅")


async def test_notify_fallback_prints(capsys) -> None:
    out = await NotifyTool().execute(message="hello", level="info")
    assert out.error is False and "hello" in out.text
    assert "hello" in capsys.readouterr().out


# ── WatchTool ──────────────────────────────────────────────────


async def _arm_watch(path: str, condition: str = "changed", **kw) -> str:
    out = await WatchTool().execute(op="arm", path=path, condition=condition, **kw)
    assert out.error is False, out.text
    return out.metadata["watch_id"]


async def _cancel(watch_id: str) -> None:
    await WatchTool().execute(op="cancel", watch_id=watch_id)


async def test_watch_check_deleted(tmp_path) -> None:
    f = tmp_path / "w.txt"
    f.write_text("x")
    wid = await _arm_watch(str(f), "deleted")
    try:
        f.unlink()
        out = await WatchTool().execute(op="check", watch_id=wid)
        assert "FIRED" in out.text
    finally:
        await _cancel(wid)


async def test_watch_check_changed(tmp_path) -> None:
    f = tmp_path / "w.txt"
    f.write_text("aaa")
    wid = await _arm_watch(str(f), "changed")
    try:
        f.write_text("aaa with more bytes so size differs")
        out = await WatchTool().execute(op="check", watch_id=wid)
        assert "FIRED" in out.text
    finally:
        await _cancel(wid)


async def test_watch_check_exists_not_fired(tmp_path) -> None:
    wid = await _arm_watch(str(tmp_path / "not-there-yet.txt"), "exists")
    try:
        out = await WatchTool().execute(op="check", watch_id=wid)
        assert "Waiting" in out.text
    finally:
        await _cancel(wid)


async def test_watch_wait_exists_fires_immediately(tmp_path) -> None:
    f = tmp_path / "w.txt"
    f.write_text("x")
    wid = await _arm_watch(str(f), "exists")
    try:
        out = await WatchTool().execute(op="wait", watch_id=wid, timeout_sec=5)
        assert "fired!" in out.text
    finally:
        await _cancel(wid)


async def test_watch_wait_deleted_fires_immediately(tmp_path) -> None:
    wid = await _arm_watch(str(tmp_path / "ghost.txt"), "deleted")
    try:
        out = await WatchTool().execute(op="wait", watch_id=wid, timeout_sec=5)
        assert "fired!" in out.text
    finally:
        await _cancel(wid)


async def test_watch_wait_changed_fires_immediately(tmp_path) -> None:
    f = tmp_path / "w.txt"
    f.write_text("a")
    wid = await _arm_watch(str(f), "changed")
    try:
        f.write_text("a" * 100)
        out = await WatchTool().execute(op="wait", watch_id=wid, timeout_sec=5)
        assert "fired!" in out.text
    finally:
        await _cancel(wid)


async def test_watch_wait_times_out(tmp_path) -> None:
    wid = await _arm_watch(str(tmp_path / "never.txt"), "exists")
    try:
        out = await WatchTool().execute(op="wait", watch_id=wid, timeout_sec=1)
        assert out.error is True and "timed out after 1s" in out.text
    finally:
        await _cancel(wid)


async def test_watch_wait_bad_timeout_errors(tmp_path) -> None:
    f = tmp_path / "w.txt"
    f.write_text("x")
    wid = await _arm_watch(str(f), "exists")
    try:
        out = await WatchTool().execute(op="wait", watch_id=wid, timeout_sec="bogus")
        assert out.error is True and "Watch error" in out.text
    finally:
        await _cancel(wid)


async def test_watch_list_and_unknown_watch() -> None:
    out = await WatchTool().execute(op="check", watch_id="nope")
    assert out.error is True and "Watch not found" in out.text
    listed = await WatchTool().execute(op="list")
    assert "Watches (" in listed.title


# ── git_tools round 2 ──────────────────────────────────────────


async def test_git_log_with_commits(git_repo) -> None:
    _commit(git_repo, "first")
    out = await git_tools.GitLog().execute()
    assert out.error is False and "first" in out.text and "Last 10 commits" in out.title


async def test_git_log_no_commits_text(monkeypatch) -> None:
    async def _empty(args, cwd="."):
        return 0, "", ""

    monkeypatch.setattr(git_tools, "_run_git", _empty)
    out = await git_tools.GitLog().execute()
    assert out.error is False and out.text == "(no commits)"


async def test_git_log_not_a_repo(tmp_path, monkeypatch) -> None:
    real = git_tools._run_git
    monkeypatch.setattr(git_tools, "_run_git", lambda args, cwd=".": real(args, str(tmp_path)))
    out = await git_tools.GitLog().execute()
    assert out.error is True


async def test_git_commit_success(git_repo) -> None:
    await git_tools.GitAdd().execute(files=["a.txt"])
    out = await git_tools.GitCommit().execute(message="add a.txt")
    assert out.error is False and "Committed" in out.title
    code, log, _ = await git_tools._run_git(["log", "--oneline", "-1"])
    assert code == 0 and "add a.txt" in log


async def test_git_commit_nothing_to_commit(git_repo) -> None:
    _commit(git_repo)
    out = await git_tools.GitCommit().execute(message="empty")
    assert out.error is True


async def test_git_commit_not_a_repo(tmp_path, monkeypatch) -> None:
    real = git_tools._run_git
    monkeypatch.setattr(git_tools, "_run_git", lambda args, cwd=".": real(args, str(tmp_path)))
    out = await git_tools.GitCommit().execute(message="x")
    assert out.error is True


async def test_git_pr_gh_not_found(git_repo, tmp_path, monkeypatch) -> None:
    _add_remote(git_repo, tmp_path)
    _commit(git_repo)
    # gh spawn raises FileNotFoundError -> the "gh CLI not found" branch.
    _patch_gh(monkeypatch, not_found=True)
    out = await git_tools.GitPR().execute(title="T")
    assert out.error is True and "gh CLI not found" in out.text


# ── bash_tool round 2 ──────────────────────────────────────────


def test_is_safe_sandbox_reports_unsafe() -> None:
    """The real sandbox flags `rm -rf /` -> _is_safe returns (False, reason)."""
    ok, reason = BashTool()._is_safe("rm -rf /")
    assert ok is False and reason


async def test_execute_sandbox_unsafe_blocks() -> None:
    out = await BashTool().execute(command="rm -rf /", description="d")
    assert out.error is True and out.text.startswith("SAFETY BLOCK")


async def test_execute_timeout_kills_tree(monkeypatch) -> None:
    # Force the POSIX spawn branch: on Windows, execute() would take the
    # create_subprocess_shell branch (running the real command, so no timeout)
    # and _kill_process_tree would take the taskkill branch, which never calls
    # proc.kill(). The Windows taskkill branch is covered separately by
    # test_kill_process_tree_windows_taskkill.
    monkeypatch.setattr(bash_tool, "IS_WINDOWS", False)
    proc = _FakeProc()

    async def _hang():
        await asyncio.sleep(60)

    proc.communicate = _hang

    async def _fake_exec(*a, **k):
        return proc

    monkeypatch.setattr(bash_tool.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(bash_tool.asyncio, "create_subprocess_shell", _fake_exec)
    out = await BashTool().execute(command="echo hi", description="d", timeout=100)
    assert out.error is True
    assert "timed out after 0s" in out.text
    assert out.metadata["exit_code"] == -1
    assert proc.killed is True


@requires_ls
async def test_execute_short_stderr_not_truncated() -> None:
    out = await BashTool().execute(
        command="ls /nonexistent-path-0", description="short stderr", timeout=30000
    )
    assert out.error is True  # ls exits 2, but the short stderr is kept verbatim
    assert out.metadata["exit_code"] == 2
    assert "[stderr]" in out.text and "truncated" not in out.text


async def test_powershell_no_stderr_skips_stderr_block(monkeypatch) -> None:
    async def _fake_exec(*a, **k):
        return _FakeProc(b"ps-out", b"", 0)

    monkeypatch.setattr(bash_tool.asyncio, "create_subprocess_exec", _fake_exec)
    out = await PowerShellTool().execute(command="Get-ChildItem", timeout=10000)
    assert out.error is False and out.text == "ps-out"


# ── utility_tools round 2 ──────────────────────────────────────


async def test_diff_file_vs_proposed(tmp_path) -> None:
    f = tmp_path / "p.txt"
    f.write_text("old\n")
    out = await DiffTool().execute(
        mode="file_vs_proposed", file_path=str(f), proposed_content="new\n"
    )
    assert out.error is False and "-old" in out.text and "+new" in out.text


async def test_diff_file_vs_proposed_missing(tmp_path) -> None:
    out = await DiffTool().execute(
        mode="file_vs_proposed",
        file_path=str(tmp_path / "no.txt"),
        proposed_content="x",
    )
    assert out.error is True and "File not found" in out.text


async def test_diff_file_vs_file_success(tmp_path) -> None:
    a = tmp_path / "a.txt"
    a.write_text("aaa\n")
    b = tmp_path / "b.txt"
    b.write_text("bbb\n")
    out = await DiffTool().execute(mode="file_vs_file", file_path=str(a), file_path_b=str(b))
    assert out.error is False and "-aaa" in out.text and "+bbb" in out.text


async def test_diff_unknown_mode() -> None:
    out = await DiffTool().execute(mode="bogus")
    assert out.error is True and "Unknown mode" in out.text


async def test_diff_identical_strings_no_differences() -> None:
    out = await DiffTool().execute(mode="string_vs_string", string_a="same\n", string_b="same\n")
    assert out.error is False and out.text == "(no differences)"


# ── ProcessTool round 2 ────────────────────────────────────


async def test_process_read_unknown_id() -> None:
    out = await ProcessTool().execute(op="read", process_id="nope")
    assert out.error is True and "Process not found" in out.text


async def test_process_status_unknown_id() -> None:
    out = await ProcessTool().execute(op="status", process_id="nope")
    assert out.error is True and "Process not found" in out.text


async def test_process_kill_unknown_id() -> None:
    out = await ProcessTool().execute(op="kill", process_id="nope")
    assert out.error is True and "Process not found" in out.text


async def test_process_read_no_stdout_handle() -> None:
    """_collect_output with p.stdout falsy -> returns [] immediately."""

    class _NoStdout:
        stdout = None

        def poll(self):
            return None

    utility_tools._processes["nostdout"] = _NoStdout()
    try:
        out = await ProcessTool().execute(op="read", process_id="nostdout")
        assert out.text == "(no output)"
    finally:
        utility_tools._processes.pop("nostdout", None)


@requires_sleep
async def test_process_read_select_timeout_break() -> None:
    """Live process, no output yet -> select() times out -> '(no output)'."""
    tool = ProcessTool()
    started = await tool.execute(op="start", command="sleep 30")
    assert started.error is False
    pid = started.metadata["process_id"]
    try:
        out = await tool.execute(op="read", process_id=pid)
        assert out.text == "(no output)"
    finally:
        await tool.execute(op="kill", process_id=pid)


class _FakeStdoutWithData:
    def __init__(self, lines):
        self._lines = list(lines)

    def fileno(self):
        raise OSError("not a socket")

    def readline(self):
        return self._lines.pop(0) if self._lines else ""


async def test_process_read_oserror_fallback_collects_lines() -> None:
    """OSError from select -> blocking-readline fallback appends real lines."""
    handle = _FakeProcHandle()
    handle.stdout = _FakeStdoutWithData(["l1\n", "l2\n"])
    utility_tools._processes["fake-data"] = handle
    utility_tools._process_outputs["fake-data"] = []
    try:
        out = await ProcessTool().execute(op="read", process_id="fake-data")
        assert "l1" in out.text and "l2" in out.text
    finally:
        utility_tools._processes.pop("fake-data", None)
        utility_tools._process_outputs.pop("fake-data", None)


async def test_process_kill_none_handle() -> None:
    """Registry holds a None handle -> `if proc:` False branch."""
    utility_tools._processes["none-proc"] = None
    try:
        out = await ProcessTool().execute(op="kill", process_id="none-proc")
        assert out.error is False and "Killed: none-proc" in out.text
    finally:
        utility_tools._processes.pop("none-proc", None)


# ── NotifyTool round 2 ─────────────────────────────────────


async def test_notify_windows_import_error_falls_back(monkeypatch, capsys) -> None:
    """Windows without win10toast -> ImportError swallowed -> print fallback."""
    import platform

    monkeypatch.setitem(sys.modules, "win10toast", None)
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    out = await NotifyTool().execute(message="hi")
    assert out.error is False and "hi" in out.text
    assert "hi" in capsys.readouterr().out


# ── WatchTool round 2 ──────────────────────────────────────


async def test_watch_list_shows_armed(tmp_path) -> None:
    f = tmp_path / "w.txt"
    f.write_text("x")
    wid = await _arm_watch(str(f), "exists")
    try:
        out = await WatchTool().execute(op="list")
        assert wid in out.text and "Watches (1)" in out.title
    finally:
        await _cancel(wid)


async def test_watch_check_changed_missing_file(tmp_path) -> None:
    wid = await _arm_watch(str(tmp_path / "ghost.txt"), "changed")
    try:
        out = await WatchTool().execute(op="check", watch_id=wid)
        assert "Waiting" in out.text
    finally:
        await _cancel(wid)


async def test_watch_check_contains(tmp_path) -> None:
    f = tmp_path / "w.txt"
    f.write_text("hello world")
    wid = await WatchTool().execute(
        op="arm", path=str(f), condition="contains", contains_text="world"
    )
    assert wid.error is False
    wid = wid.metadata["watch_id"]
    try:
        out = await WatchTool().execute(op="check", watch_id=wid)
        assert "FIRED" in out.text
        # Rewrite without the text -> not fired.
        f.write_text("nothing here")
        out = await WatchTool().execute(op="check", watch_id=wid)
        assert "Waiting" in out.text
    finally:
        await _cancel(wid)


async def test_watch_wait_unknown_id() -> None:
    out = await WatchTool().execute(op="wait", watch_id="nope", timeout_sec=1)
    assert out.error is True and "Watch not found" in out.text


async def test_watch_wait_changed_missing_file_times_out(tmp_path) -> None:
    wid = await _arm_watch(str(tmp_path / "ghost.txt"), "changed")
    try:
        out = await WatchTool().execute(op="wait", watch_id=wid, timeout_sec=1)
        assert out.error is True and "timed out after 1s" in out.text
    finally:
        await _cancel(wid)


async def test_watch_wait_contains_fires(tmp_path) -> None:
    f = tmp_path / "w.txt"
    f.write_text("the magic word is please")
    out = await WatchTool().execute(
        op="arm", path=str(f), condition="contains", contains_text="please"
    )
    wid = out.metadata["watch_id"]
    try:
        out = await WatchTool().execute(op="wait", watch_id=wid, timeout_sec=5)
        assert "fired!" in out.text
    finally:
        await _cancel(wid)


async def test_watch_cancel_unknown_id() -> None:
    out = await WatchTool().execute(op="cancel", watch_id="nope")
    assert out.error is True and "Watch not found" in out.text


async def test_watch_unknown_op() -> None:
    out = await WatchTool().execute(op="bogus")
    assert out.error is True and "Unknown op" in out.text


async def test_watch_check_contains_missing_file(tmp_path) -> None:
    out = await WatchTool().execute(
        op="arm",
        path=str(tmp_path / "ghost.txt"),
        condition="contains",
        contains_text="x",
    )
    wid = out.metadata["watch_id"]
    try:
        out = await WatchTool().execute(op="check", watch_id=wid)
        assert "Waiting" in out.text
    finally:
        await _cancel(wid)


async def test_watch_wait_contains_missing_file_times_out(tmp_path) -> None:
    out = await WatchTool().execute(
        op="arm",
        path=str(tmp_path / "ghost.txt"),
        condition="contains",
        contains_text="x",
    )
    wid = out.metadata["watch_id"]
    try:
        out = await WatchTool().execute(op="wait", watch_id=wid, timeout_sec=1)
        assert out.error is True and "timed out after 1s" in out.text
    finally:
        await _cancel(wid)
