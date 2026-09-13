"""Night-2 tests: agent toolbelt (tools/) behavior contracts.

Covers: registry alias resolution, Diff/Process/Notify/Watch,
DateTime/Sleep, SQLite/CSV/Secrets, Config, PlanMode, Todo/ShellHistory,
MemoryRemember/Recall/Forget, git tools, Task tools, Brief, AskUserQuestion,
SkillList/SkillRun, Graphify.
"""

from __future__ import annotations

import contextlib
import json

import pytest

# Import every tool module so registration happens.
import tools.ask_question_tool
import tools.brief_tool
import tools.config_tool
import tools.data_tools
import tools.datetime_tools
import tools.git_tools
import tools.graphify_tool
import tools.memory_tools
import tools.plan_tools
import tools.session_tools
import tools.skill_tools
import tools.task_tools
import tools.utility_tools  # noqa: F401
from tools.registry import registry


def _ok(result) -> bool:
    return not bool(getattr(result, "error", False))


def _tool(name):
    t = registry.get(name)
    assert t is not None, f"tool {name} not registered"
    return t


# ── registry ─────────────────────────────────────────────────────────


class TestRegistryAliases:
    def test_alias_resolution(self):
        assert registry.get("Compare") is registry.get("Diff")
        assert registry.get("gits") is registry.get("GitStatus")
        assert registry.get("wait") is registry.get("Sleep")
        assert registry.get("runskill") is registry.get("SkillRun")
        assert registry.get("no-such-tool-xyz") is None

    def test_openai_schema_shape(self):
        for s in registry.openai_tools():
            assert s["type"] == "function"
            assert "name" in s["function"]
            assert "parameters" in s["function"]


# ── Diff ─────────────────────────────────────────────────────────────


class TestDiffTool:
    async def test_string_vs_string(self):
        r = await _tool("Diff").execute(
            mode="string_vs_string", string_a="a\nb\n", string_b="a\nc\n"
        )
        assert _ok(r)
        assert "-b" in r.text and "+c" in r.text

    async def test_identical(self):
        r = await _tool("Diff").execute(
            mode="string_vs_string", string_a="same\n", string_b="same\n"
        )
        assert _ok(r)
        assert "no differences" in r.text

    async def test_file_vs_proposed(self, tmp_path):
        f = tmp_path / "x.txt"
        f.write_text("line1\nline2\n")
        r = await _tool("Diff").execute(
            mode="file_vs_proposed",
            file_path=str(f),
            proposed_content="line1\nCHANGED\n",
        )
        assert _ok(r)
        assert "+CHANGED" in r.text

    async def test_file_vs_file(self, tmp_path):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("1\n2\n")
        b.write_text("1\n3\n")
        r = await _tool("Diff").execute(mode="file_vs_file", file_path=str(a), file_path_b=str(b))
        assert _ok(r)
        assert "-2" in r.text and "+3" in r.text

    async def test_missing_file(self):
        r = await _tool("Diff").execute(
            mode="file_vs_proposed", file_path="/nope/does-not-exist.txt"
        )
        assert not _ok(r)

    async def test_unknown_mode(self):
        r = await _tool("Diff").execute(mode="sideways")
        assert not _ok(r)
        assert "Unknown mode" in r.text

    async def test_context_lines(self):
        r = await _tool("Diff").execute(
            mode="string_vs_string",
            string_a="\n".join(f"l{i}" for i in range(20)),
            string_b="\n".join(f"l{i}" for i in range(20)).replace("l5", "CHANGED"),
            context_lines=0,
        )
        assert _ok(r)
        assert "@@" in r.text


# ── Process ──────────────────────────────────────────────────────────


@pytest.fixture
def clean_processes():
    from tools import utility_tools

    utility_tools._processes.clear()
    utility_tools._process_outputs.clear()
    yield
    for pid in list(utility_tools._processes):
        with contextlib.suppress(Exception):
            utility_tools._processes[pid].kill()
    utility_tools._processes.clear()
    utility_tools._process_outputs.clear()


class TestProcessTool:
    async def test_lifecycle(self, clean_processes):
        tool = _tool("Process")
        r = await tool.execute(op="start", command="echo proc-hello")
        assert _ok(r)
        pid = r.metadata["process_id"]

        r = await tool.execute(op="status", process_id=pid)
        assert _ok(r)
        assert pid in r.text

        import asyncio

        for _ in range(50):
            await asyncio.sleep(0.1)
            s = await tool.execute(op="status", process_id=pid)
            if "Exited" in s.text:
                break
        r = await tool.execute(op="read", process_id=pid)
        assert _ok(r)
        assert "proc-hello" in r.text

        # Second read clears the buffer (clear=True default)
        r2 = await tool.execute(op="read", process_id=pid)
        assert _ok(r2)
        assert "proc-hello" not in r2.text

        r = await tool.execute(op="kill", process_id=pid)
        assert _ok(r)
        r = await tool.execute(op="status", process_id=pid)
        assert not _ok(r)

    async def test_list_empty(self, clean_processes):
        r = await _tool("Process").execute(op="list")
        assert _ok(r)
        assert "No processes" in r.text

    async def test_unknown_id(self):
        for op in ("read", "status", "kill"):
            r = await _tool("Process").execute(op=op, process_id="deadbeef")
            assert not _ok(r)

    async def test_unknown_op(self):
        r = await _tool("Process").execute(op="explode")
        assert not _ok(r)


# ── Notify ───────────────────────────────────────────────────────────


class TestNotifyTool:
    async def test_fallback_text(self, capsys):
        r = await _tool("Notify").execute(message="hello", level="success")
        assert _ok(r)
        assert "hello" in r.text
        assert "✅" in r.text

    async def test_unknown_level(self):
        r = await _tool("Notify").execute(message="hi", level="bogus")
        assert _ok(r)


# ── Watch ────────────────────────────────────────────────────────────


@pytest.fixture
def clean_watches():
    from tools import utility_tools

    utility_tools._watches.clear()
    yield
    utility_tools._watches.clear()


class TestWatchTool:
    async def test_changed_fires(self, tmp_path, clean_watches):
        f = tmp_path / "w.txt"
        f.write_text("v1")
        tool = _tool("Watch")
        r = await tool.execute(op="arm", path=str(f), condition="changed")
        wid = r.metadata["watch_id"]

        r = await tool.execute(op="check", watch_id=wid)
        assert _ok(r) and "FIRED" not in r.text

        f.write_text("v1-more")
        r = await tool.execute(op="check", watch_id=wid)
        assert "FIRED" in r.text

        r = await tool.execute(op="cancel", watch_id=wid)
        assert _ok(r)
        r = await tool.execute(op="check", watch_id=wid)
        assert not _ok(r)

    async def test_exists_condition(self, tmp_path, clean_watches):
        tool = _tool("Watch")
        target = tmp_path / "later.txt"
        r = await tool.execute(op="arm", path=str(target), condition="exists")
        wid = r.metadata["watch_id"]
        r = await tool.execute(op="check", watch_id=wid)
        assert "Waiting" in r.text
        target.write_text("now here")
        r = await tool.execute(op="check", watch_id=wid)
        assert "FIRED" in r.text

    async def test_contains_condition(self, tmp_path, clean_watches):
        f = tmp_path / "c.txt"
        f.write_text("nothing yet")
        tool = _tool("Watch")
        r = await tool.execute(op="arm", path=str(f), condition="contains", contains_text="magic")
        wid = r.metadata["watch_id"]
        r = await tool.execute(op="check", watch_id=wid)
        assert "Waiting" in r.text
        f.write_text("the magic word")
        r = await tool.execute(op="check", watch_id=wid)
        assert "FIRED" in r.text

    async def test_wait_times_out_fast(self, tmp_path, clean_watches):
        f = tmp_path / "never.txt"
        f.write_text("x")
        tool = _tool("Watch")
        r = await tool.execute(op="arm", path=str(f), condition="contains", contains_text="zzz")
        wid = r.metadata["watch_id"]
        r = await tool.execute(op="wait", watch_id=wid, timeout_sec=0)
        assert not _ok(r)
        assert "timed out" in r.text

    async def test_wait_fires(self, tmp_path, clean_watches):
        f = tmp_path / "yes.txt"
        f.write_text("go")
        tool = _tool("Watch")
        r = await tool.execute(op="arm", path=str(f), condition="contains", contains_text="go")
        wid = r.metadata["watch_id"]
        r = await tool.execute(op="wait", watch_id=wid, timeout_sec=5)
        assert _ok(r) and "fired" in r.text

    async def test_list_and_unknown(self, tmp_path, clean_watches):
        tool = _tool("Watch")
        r = await tool.execute(op="list")
        assert "No active watches" in r.text
        f = tmp_path / "z.txt"
        f.write_text("z")
        r = await tool.execute(op="arm", path=str(f))
        assert r.metadata["watch_id"] in (await tool.execute(op="list")).text
        r = await tool.execute(op="check", watch_id="nope")
        assert not _ok(r)
        r = await tool.execute(op="wait", watch_id="nope")
        assert not _ok(r)
        r = await tool.execute(op="cancel", watch_id="nope")
        assert not _ok(r)
        r = await tool.execute(op="bogus")
        assert not _ok(r)


# ── DateTime / Sleep ─────────────────────────────────────────────────


class TestDateTimeSleep:
    async def test_default(self):
        r = await _tool("DateTime").execute()
        assert _ok(r)
        assert r.text.strip()
        assert "iso" in r.metadata

    async def test_custom_format(self):
        r = await _tool("DateTime").execute(format="%Y-%m-%d")
        import datetime as dt

        assert _ok(r)
        assert r.text == dt.datetime.now().strftime("%Y-%m-%d")

    async def test_sleep_clamps(self, monkeypatch):
        import tools.datetime_tools as m

        seen = []
        monkeypatch.setattr(m.asyncio, "sleep", lambda s: seen.append(s) or _noop_coro())
        r = await _tool("Sleep").execute(seconds=0.0)
        assert _ok(r) and r.metadata["seconds"] == 0.1
        r = await _tool("Sleep").execute(seconds=9999)
        assert r.metadata["seconds"] == 300.0
        r = await _tool("Sleep").execute(seconds=2.0)
        assert r.metadata["seconds"] == 2.0
        assert seen == [0.1, 300.0, 2.0]


async def _noop_coro():
    return None


# ── SQLite ───────────────────────────────────────────────────────────


class TestSQLiteTool:
    async def _db(self, tmp_path):
        import sqlite3

        db = tmp_path / "t.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO users (name) VALUES ('ann'), ('bob')")
        conn.commit()
        conn.close()
        return str(db)

    async def test_query(self, tmp_path):
        db = await self._db(tmp_path)
        r = await _tool("SQLite").execute(op="query", db_path=db, sql="SELECT * FROM users")
        assert _ok(r)
        assert "ann" in r.text and "bob" in r.text
        assert r.metadata["row_count"] == 2

    async def test_tables_schema_info(self, tmp_path):
        db = await self._db(tmp_path)
        r = await _tool("SQLite").execute(op="tables", db_path=db)
        assert _ok(r) and "users" in r.text
        r = await _tool("SQLite").execute(op="schema", db_path=db, table="users")
        assert _ok(r) and "CREATE TABLE" in r.text
        r = await _tool("SQLite").execute(op="info", db_path=db)
        assert _ok(r) and "Tables: 1" in r.text

    async def test_execute(self, tmp_path):
        db = await self._db(tmp_path)
        r = await _tool("SQLite").execute(
            op="execute", db_path=db, sql="INSERT INTO users (name) VALUES ('zed')"
        )
        assert _ok(r)
        r = await _tool("SQLite").execute(
            op="query", db_path=db, sql="SELECT COUNT(*) AS n FROM users"
        )
        assert "3" in r.text

    async def test_errors(self, tmp_path):
        r = await _tool("SQLite").execute(
            op="query", db_path=str(tmp_path / "missing.db"), sql="SELECT 1"
        )
        assert not _ok(r)
        db = await self._db(tmp_path)
        r = await _tool("SQLite").execute(op="query", db_path=db)
        assert not _ok(r)
        r = await _tool("SQLite").execute(op="query", db_path=db, sql="SELECT * FROM nope")
        assert not _ok(r)
        r = await _tool("SQLite").execute(op="bogus", db_path=db)
        assert not _ok(r)

    async def test_query_empty_result(self, tmp_path):
        db = await self._db(tmp_path)
        r = await _tool("SQLite").execute(
            op="query", db_path=db, sql="SELECT * FROM users WHERE name='nobody'"
        )
        assert _ok(r)
        assert "empty result" in r.text


# ── CSV ──────────────────────────────────────────────────────────────


class TestCSVTool:
    async def test_read(self, tmp_path):
        f = tmp_path / "d.csv"
        f.write_text("name,age\nann,30\nbob,25\n")
        r = await _tool("CSV").execute(file_path=str(f))
        assert _ok(r)
        assert "ann" in r.text and r.metadata["row_count"] == 2

    async def test_limit_and_delimiter(self, tmp_path):
        f = tmp_path / "d.csv"
        f.write_text("a;b\n1;2\n3;4\n5;6\n")
        r = await _tool("CSV").execute(file_path=str(f), delimiter=";", limit=2)
        assert _ok(r)
        assert r.metadata["row_count"] == 2

    async def test_missing(self):
        r = await _tool("CSV").execute(file_path="/nope/missing.csv")
        assert not _ok(r)


# ── Secrets (redaction contract) ─────────────────────────────────────


class TestSecretsTool:
    async def _env(self, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text(
            "# comment\nAPI_KEY=super-secret-value-123\nOTHER=xyz\nBLANK=\n"
        )
        monkeypatch.chdir(tmp_path)

    async def test_list_env(self, tmp_path, monkeypatch):
        await self._env(tmp_path, monkeypatch)
        r = await _tool("Secrets").execute(op="list_env")
        assert _ok(r)
        assert "API_KEY" in r.text and "OTHER" in r.text
        assert "super-secret-value-123" not in r.text

    async def test_check_env(self, tmp_path, monkeypatch):
        await self._env(tmp_path, monkeypatch)
        r = await _tool("Secrets").execute(op="check_env", key="API_KEY")
        assert _ok(r) and r.metadata["found"] is True
        assert "super-secret-value-123" not in r.text
        r = await _tool("Secrets").execute(op="check_env", key="MISSING")
        assert _ok(r) and r.metadata["found"] is False

    async def test_get_env_hides_value(self, tmp_path, monkeypatch):
        await self._env(tmp_path, monkeypatch)
        r = await _tool("Secrets").execute(op="get_env", key="API_KEY")
        assert _ok(r)
        assert "super-secret-value-123" not in r.text
        assert "super-secret-value-123" not in json.dumps(r.metadata)
        assert r.metadata["length"] == len("super-secret-value-123")
        r = await _tool("Secrets").execute(op="get_env", key="NOPE")
        assert not _ok(r)

    async def test_prefix_key_does_not_match(self, tmp_path, monkeypatch):
        await self._env(tmp_path, monkeypatch)
        r = await _tool("Secrets").execute(op="get_env", key="API")
        assert not _ok(r), "prefix of a key must not match API_KEY"

    async def test_unimplemented_ops(self, tmp_path, monkeypatch):
        await self._env(tmp_path, monkeypatch)
        for op in ("get_key", "set_key", "del_key"):
            r = await _tool("Secrets").execute(op=op)
            assert not _ok(r)

    async def test_missing_env_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        r = await _tool("Secrets").execute(op="list_env")
        assert not _ok(r)

    async def test_missing_key_param(self, tmp_path, monkeypatch):
        await self._env(tmp_path, monkeypatch)
        r = await _tool("Secrets").execute(op="check_env")
        assert not _ok(r)
        r = await _tool("Secrets").execute(op="get_env")
        assert not _ok(r)


# ── Config ───────────────────────────────────────────────────────────


@pytest.fixture
def clean_config():
    import tools.config_tool as m

    saved = dict(m._overrides)
    m._overrides.clear()
    yield
    m._overrides.clear()
    m._overrides.update(saved)


class TestConfigTool:
    async def test_read_default(self, clean_config):
        r = await _tool("Config").execute(setting="model")
        assert _ok(r) and "deepseek" in r.text

    async def test_write_and_read(self, clean_config):
        r = await _tool("Config").execute(setting="temperature", value=0.3)
        assert _ok(r)
        r = await _tool("Config").execute(setting="temperature")
        assert _ok(r) and "0.3" in r.text

    async def test_unknown_setting(self, clean_config):
        r = await _tool("Config").execute(setting="launch_codes")
        assert not _ok(r)
        assert "Unknown setting" in r.text


# ── Plan mode ────────────────────────────────────────────────────────


@pytest.fixture
def clean_plan():
    import tools.plan_tools as m

    m._plan_mode = False
    m._plan_context = {}
    yield
    m._plan_mode = False
    m._plan_context = {}


class TestPlanTools:
    async def test_enter_exit(self, clean_plan):
        import tools.plan_tools as m

        r = await _tool("EnterPlanMode").execute()
        assert _ok(r) and m.is_plan_mode()
        assert m.get_plan_context()["entered_at"]
        r = await _tool("ExitPlanMode").execute(plan="ship it")
        assert _ok(r) and not m.is_plan_mode()
        assert "ship it" in r.text


# ── Session: todos + shell history ───────────────────────────────────


@pytest.fixture
def clean_session():
    import tools.session_tools as m

    saved_todos, saved_hist = dict(m._todos), list(m._shell_history)
    m._todos.clear()
    m._shell_history.clear()
    yield
    m._todos.clear()
    m._todos.update(saved_todos)
    m._shell_history.clear()
    m._shell_history.extend(saved_hist)


class TestSessionTools:
    async def test_todo_roundtrip(self, clean_session):
        w = _tool("TodoWrite")
        r = await w.execute(
            todos=[
                {"id": "t1", "content": "do the thing", "status": "in_progress"},
                {"id": "t2", "content": "later", "status": "pending", "priority": "high"},
            ]
        )
        assert _ok(r) and "do the thing" in r.text
        r = await _tool("TodoRead").execute()
        assert _ok(r) and "t1" in r.text and "high" in r.text

    async def test_todo_empty(self, clean_session):
        r = await _tool("TodoRead").execute()
        assert _ok(r) and "No todos" in r.text

    async def test_shell_history_filters(self, clean_session):
        from tools.session_tools import record_shell_command

        record_shell_command("echo hi", 0, "hi", shell_name="bash")
        record_shell_command("false", 1, "", shell_name="bash")
        record_shell_command("Get-ChildItem", 0, "", shell_name="powershell")

        tool = _tool("ShellHistory")
        r = await tool.execute()
        assert _ok(r) and r.metadata["count"] == 3

        r = await tool.execute(failed_only=True)
        assert r.metadata["count"] == 1 and "false" in r.text

        r = await tool.execute(shell_filter="powershell")
        assert r.metadata["count"] == 1 and "Get-ChildItem" in r.text

        r = await tool.execute(search="echo")
        assert r.metadata["count"] == 1

        r = await tool.execute(show_output=True)
        assert "-> hi" in r.text

        r = await tool.execute(last_n=0)
        assert "No matching" in r.text

    async def test_history_truncation(self, clean_session):
        from tools.session_tools import record_shell_command

        for i in range(210):
            record_shell_command(f"cmd{i}", 0)
        from tools import session_tools

        assert len(session_tools._shell_history) == 200


# ── Memory tools ─────────────────────────────────────────────────────


@pytest.fixture
def temp_mem(monkeypatch, tmp_path):
    import tools.memory_tools as mt
    from memory.store import MemoryStore

    store = MemoryStore(tmp_path / "mem")
    monkeypatch.setattr(mt, "get_memory", lambda: store)
    return store


class TestMemoryTools:
    async def test_remember_recall(self, temp_mem):
        r = await _tool("MemoryRemember").execute(
            content="Dylan prefers dark mode", tags=["pref"], source="t"
        )
        assert _ok(r)
        r = await _tool("MemoryRecall").execute(query="dark mode")
        assert _ok(r) and "dark mode" in r.text

    async def test_forget_partial_id(self, temp_mem):
        r = await _tool("MemoryRemember").execute(content="ephemeral fact", source="t")
        mem_id = r.metadata["memory_id"]
        r = await _tool("MemoryForget").execute(memory_id=mem_id[:8])
        assert _ok(r)
        r = await _tool("MemoryForget").execute(memory_id=mem_id[:8])
        assert not _ok(r)

    async def test_forget_empty_id_rejected(self, temp_mem):
        await _tool("MemoryRemember").execute(content="keep me", source="t")
        r = await _tool("MemoryForget").execute(memory_id="")
        assert not _ok(r)
        r = await _tool("MemoryForget").execute(memory_id="   ")
        assert not _ok(r)
        assert temp_mem.get_context("keep me") != "  (no relevant memories)"

    async def test_summary_and_clear(self, temp_mem):
        await _tool("MemoryRemember").execute(content="one", source="t")
        r = await _tool("MemorySummary").execute()
        assert _ok(r) and "1 entries" in r.text
        r = await _tool("MemoryClear").execute()
        assert _ok(r)
        r = await _tool("MemorySummary").execute()
        assert "No memories" in r.text


# ── git tools ────────────────────────────────────────────────────────


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "a.txt").write_text("hello\n")
    monkeypatch.chdir(repo)
    return repo


class TestGitTools:
    async def test_status_log_branch(self, git_repo):
        r = await _tool("GitStatus").execute()
        assert _ok(r) and "a.txt" in r.text

        await _tool("GitAdd").execute()
        r = await _tool("GitStatus").execute()
        assert _ok(r)

        r = await _tool("GitCommit").execute(message="first")
        assert _ok(r)

        r = await _tool("GitStatus").execute()
        assert "clean" in r.text

        r = await _tool("GitLog").execute(count=3)
        assert _ok(r) and "first" in r.text

        r = await _tool("GitBranch").execute()
        assert _ok(r)

    async def test_diff(self, git_repo):
        await _tool("GitAdd").execute()
        await _tool("GitCommit").execute(message="first")
        (git_repo / "a.txt").write_text("changed\n")
        r = await _tool("GitDiff").execute()
        assert _ok(r) and "changed" in r.text

    async def test_commit_nothing_fails(self, git_repo):
        r = await _tool("GitCommit").execute(message="empty")
        assert not _ok(r)


# ── Task tools (redirected TASKS_DIR) ────────────────────────────────


@pytest.fixture
def temp_tasks(monkeypatch, tmp_path):
    import tools.task_tools as m

    monkeypatch.setattr(m, "TASKS_DIR", tmp_path)
    return tmp_path


class TestTaskTools:
    async def test_crud(self, temp_tasks):
        r = await _tool("TaskCreate").execute(
            subject="write tests", description="cover tools", priority="high", tags=["t"]
        )
        assert _ok(r)
        tid = r.metadata["task_id"]

        r = await _tool("TaskGet").execute(task_id=tid)
        assert _ok(r) and "write tests" in r.text

        r = await _tool("TaskUpdate").execute(task_id=tid, status="in_progress")
        assert _ok(r) and r.metadata["status"] == "in_progress"

        r = await _tool("TaskList").execute(status="in_progress")
        assert tid in r.text

        r = await _tool("TaskList").execute(tag="t")
        assert tid in r.text

        r = await _tool("TaskStop").execute(task_id=tid, action="cancel", reason="done-ish")
        assert _ok(r)
        r = await _tool("TaskGet").execute(task_id=tid)
        assert "cancelled" in r.text

        r = await _tool("TaskStop").execute(task_id=tid, action="delete")
        assert _ok(r)
        r = await _tool("TaskGet").execute(task_id=tid)
        assert not _ok(r)

    async def test_not_found(self, temp_tasks):
        for tool_name, kw in (
            ("TaskGet", {"task_id": "task_nope"}),
            ("TaskUpdate", {"task_id": "task_nope"}),
            ("TaskStop", {"task_id": "task_nope"}),
        ):
            r = await _tool(tool_name).execute(**kw)
            assert not _ok(r)

    async def test_corrupt_tasks_file(self, temp_tasks, monkeypatch):
        (temp_tasks / "tasks.json").write_text("not json{{{")
        r = await _tool("TaskList").execute()
        assert _ok(r) and "No tasks found" in r.text


# ── Brief ────────────────────────────────────────────────────────────


class TestBriefTool:
    async def test_text(self):
        r = await _tool("Brief").execute(text="line1\nline2\nline3", focus="length")
        assert _ok(r)
        assert r.metadata["lines"] == 3

    async def test_dir(self, tmp_path):
        (tmp_path / "a.py").write_text("x=1")
        (tmp_path / "sub").mkdir()
        r = await _tool("Brief").execute(dir_path=str(tmp_path))
        assert _ok(r)
        assert "a.py" in r.text and "sub/" in r.text

    async def test_file(self, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("import os\n\n\nclass Foo:\n    def bar(self):\n        pass\n")
        r = await _tool("Brief").execute(file_path=str(f))
        assert _ok(r)
        assert "Classes: 1" in r.text and "Functions: 1" in r.text

    async def test_errors(self, tmp_path):
        r = await _tool("Brief").execute()
        assert not _ok(r)
        r = await _tool("Brief").execute(file_path="/nope/x.py")
        assert not _ok(r)
        r = await _tool("Brief").execute(dir_path=str(tmp_path / "a.py"))
        assert not _ok(r)


# ── AskUserQuestion ──────────────────────────────────────────────────


class TestAskQuestionTool:
    def _q(self):
        return [
            {
                "question": "pick one",
                "header": "Pick",
                "options": [
                    {"label": "yes", "description": "do it"},
                    {"label": "no", "description": "skip it"},
                ],
            }
        ]

    async def test_render(self):
        r = await _tool("AskUserQuestion").execute(questions=self._q())
        assert _ok(r)
        assert "pick one" in r.text and "[1] yes" in r.text and "[2] no" in r.text
        assert "Custom answer" in r.text

    async def test_multi_select_flag(self):
        q = self._q()
        q[0]["multi_select"] = True
        r = await _tool("AskUserQuestion").execute(questions=q)
        assert "[multi-select]" in r.text

    async def test_empty_and_malformed(self):
        r = await _tool("AskUserQuestion").execute(questions=[])
        assert not _ok(r)
        r = await _tool("AskUserQuestion").execute(questions=["not-a-dict"])
        assert not _ok(r)
        # missing options degrades gracefully
        r = await _tool("AskUserQuestion").execute(questions=[{"question": "q", "header": "H"}])
        assert _ok(r) and "[0] Custom answer" in r.text


# ── Skill tools ──────────────────────────────────────────────────────


class TestSkillTools:
    async def test_list_bundled(self):
        r = await _tool("SkillList").execute()
        assert _ok(r)
        assert r.metadata["count"] >= 1
        assert "bundled" in r.text

    async def test_run_known_skill(self):
        names = []
        r = await _tool("SkillList").execute()
        for line in r.text.splitlines():
            if line.startswith("  - **"):
                names.append(line.split("**")[1])
        assert names
        r = await _tool("SkillRun").execute(skill_name=names[0])
        assert _ok(r) and r.text.strip()

    async def test_run_case_insensitive(self):
        r = await _tool("SkillList").execute()
        line = next(ln for ln in r.text.splitlines() if ln.startswith("  - **"))
        name = line.split("**")[1]
        r = await _tool("SkillRun").execute(skill_name=name.upper())
        assert _ok(r)

    async def test_run_unknown(self):
        r = await _tool("SkillRun").execute(skill_name="nope-not-a-skill")
        assert not _ok(r)

    async def test_parse_skill_edge_cases(self, tmp_path):
        import tools.skill_tools as m

        good = tmp_path / "g.md"
        good.write_text("---\nname: test-skill\ndescription: d\nversion: 2.0\n---\nDo stuff\n")
        p = m._parse_skill(good)
        assert p and p["name"] == "test-skill" and p["version"] == 2.0
        assert p["prompt"] == "Do stuff"

        bad = tmp_path / "b.md"
        bad.write_text("no frontmatter here")
        assert m._parse_skill(bad) is None

        badyaml = tmp_path / "y.md"
        badyaml.write_text("---\nname: [unclosed\n---\nbody\n")
        assert m._parse_skill(badyaml) is None

        missing = tmp_path / "missing.md"
        assert m._parse_skill(missing) is None

    async def test_skill_dir_override(self, tmp_path, monkeypatch):
        import tools.skill_tools as m

        (tmp_path / "solo.md").write_text("---\nname: solo\n---\nOnly skill\n")
        monkeypatch.setattr(m, "SKILLS_DIR", tmp_path)
        r = await _tool("SkillList").execute()
        assert _ok(r) and "solo" in r.text


# ── Graphify ─────────────────────────────────────────────────────────


class TestGraphifyTool:
    async def test_info_no_graph(self):
        r = await _tool("Graphify").execute(subcommand="info")
        assert not _ok(r)
        assert "No graph found" in r.text

    async def test_usage_errors(self):
        for sub, kw in (
            ("path", {"arg1": "a"}),
            ("explain", {}),
            ("query", {}),
            ("affected", {}),
        ):
            r = await _tool("Graphify").execute(subcommand=sub, **kw)
            assert not _ok(r)

    async def test_unknown_subcommand(self):
        r = await _tool("Graphify").execute(subcommand="dance")
        assert not _ok(r)
