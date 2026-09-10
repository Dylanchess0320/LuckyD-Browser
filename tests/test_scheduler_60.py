"""Tests for LuckyD 6.0 scheduled agents — cron, store, guardrails, runner, tools."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import tools.schedule_tools as sched_tools
from core.hooks import HookContext, get_hooks, register_plugin
from core.scheduler import (
    CronSpec,
    ScheduleStore,
    build_schedule,
    compute_next_run,
    cron_next,
    validate_schedule_fields,
)
from core.unattended import UnattendedApprovalHook


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "luckyd"
    monkeypatch.setenv("LUCKYD_DATA_DIR", str(d))
    # Reset the tool module's store singleton so it picks up the tmp dir.
    sched_tools._store = None
    return d


@pytest.fixture()
def store(data_dir):
    return ScheduleStore()


def _ctx() -> HookContext:
    return HookContext(turn=1, messages=[], config={})


def _sched(**kw):
    base = dict(name="t", prompt="do the thing", kind="every", every_minutes=60)
    base.update(kw)
    return build_schedule(**base)


# ── cron ──────────────────────────────────────────────────────────────────


class TestCron:
    def test_parse_basic(self):
        spec = CronSpec.parse("0 7 * * *")
        assert spec.minute == {0} and spec.hour == {7}

    def test_parse_steps_and_lists(self):
        spec = CronSpec.parse("*/15 9-17 * * mon-fri")
        assert spec.minute == set(range(0, 60, 15))
        assert spec.hour == set(range(9, 18))
        assert spec.dow == {1, 2, 3, 4, 5}

    def test_parse_month_names(self):
        assert CronSpec.parse("0 0 1 jan *").month == {1}

    def test_parse_invalid(self):
        for bad in ("0 7 * *", "61 * * * *", "*/0 * * * *", "nope * * * *", "* * * * * *"):
            with pytest.raises(ValueError):
                CronSpec.parse(bad)

    def test_next_daily(self):
        after = datetime(2026, 9, 11, 6, 0, tzinfo=timezone.utc)  # Friday
        nxt = cron_next("30 7 * * *", after)
        assert (nxt.hour, nxt.minute, nxt.day) == (7, 30, 11)

    def test_next_rolls_to_tomorrow(self):
        after = datetime(2026, 9, 11, 8, 0, tzinfo=timezone.utc)
        nxt = cron_next("30 7 * * *", after)
        assert (nxt.hour, nxt.day) == (7, 12)

    def test_next_weekday_only(self):
        # Saturday 2026-09-12 -> next Monday 2026-09-14
        after = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
        nxt = cron_next("0 9 * * mon", after)
        assert (nxt.day, nxt.weekday()) == (14, 0)

    def test_dom_dow_or_semantics(self):
        # 1st of month OR Sunday — 2026-09-13 is a Sunday
        spec = CronSpec.parse("0 0 1 * sun")
        assert spec.matches(datetime(2026, 9, 13, 0, 0))
        assert spec.matches(datetime(2026, 10, 1, 0, 0))
        assert not spec.matches(datetime(2026, 9, 14, 0, 0))


class TestNextRun:
    def test_every(self):
        s = _sched(kind="every", every_minutes=30)
        after = datetime.now(timezone.utc)
        nxt = compute_next_run(s, after)
        assert timedelta(minutes=29) < nxt - after <= timedelta(minutes=30)

    def test_daily(self):
        s = _sched(kind="daily", daily_at="07:30")
        nxt = compute_next_run(s)
        assert (nxt.hour, nxt.minute) == (7, 30)
        assert nxt > datetime.now(timezone.utc)


# ── validation ────────────────────────────────────────────────────────────


class TestValidation:
    def test_hard_deny_scopes_rejected(self):
        with pytest.raises(ValueError, match="never be granted"):
            validate_schedule_fields(
                name="x",
                prompt="y",
                kind="every",
                every_minutes=10,
                allow_scopes=["read", "shell"],
            )

    def test_unknown_scope_rejected(self):
        with pytest.raises(ValueError, match="unknown permission scopes"):
            validate_schedule_fields(
                name="x",
                prompt="y",
                kind="every",
                every_minutes=10,
                allow_scopes=["teleport"],
            )

    def test_interval_floor(self):
        with pytest.raises(ValueError, match="at least 5 minutes"):
            validate_schedule_fields(name="x", prompt="y", kind="every", every_minutes=2)

    def test_bad_cron_rejected(self):
        with pytest.raises(ValueError):
            validate_schedule_fields(name="x", prompt="y", kind="cron", cron="bogus")

    def test_bad_daily_rejected(self):
        with pytest.raises(ValueError):
            validate_schedule_fields(name="x", prompt="y", kind="daily", daily_at="25:99")

    def test_defaults(self):
        scopes = validate_schedule_fields(name="x", prompt="y", kind="every", every_minutes=10)
        assert scopes == ["read", "network", "memory"]

    def test_browser_allowed_explicitly(self):
        scopes = validate_schedule_fields(
            name="x",
            prompt="y",
            kind="every",
            every_minutes=10,
            allow_scopes=["read", "network", "browser"],
        )
        assert "browser" in scopes


# ── store ─────────────────────────────────────────────────────────────────


class TestStore:
    def test_crud(self, store):
        s = _sched(name="morning")
        store.create(s)
        got = store.get(s.id)
        assert got and got.name == "morning"
        assert got.next_run_at, "next run computed at build"

        got.name = "evening"
        store.update(got)
        assert store.get(s.id).name == "evening"

        assert len(store.list()) == 1
        assert store.delete(s.id) is True
        assert store.get(s.id) is None
        assert store.delete(s.id) is False

    def test_due(self, store):
        past = _sched(name="due")
        past.next_run_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        future = _sched(name="future")
        future.next_run_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        off = _sched(name="off")
        off.enabled = False
        off.next_run_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        for s in (past, future, off):
            store.create(s)
        due = store.due()
        assert [s.name for s in due] == ["due"]

    def test_run_history(self, store):
        s = _sched()
        store.create(s)
        rid = store.record_run_start(s.id, s.name)
        store.record_run_end(rid, status="ok", summary="did it", turns=3, duration_sec=4.2)
        hist = store.history(s.id)
        assert len(hist) == 1 and hist[0]["status"] == "ok"
        assert hist[0]["turns"] == 3

    def test_digest_and_watermark(self, store):
        s = _sched()
        store.create(s)
        rid = store.record_run_start(s.id, s.name)
        store.record_run_end(rid, status="ok", summary="fine")
        assert len(store.digest()) == 1
        store.mark_digest_seen()
        assert store.digest() == []


# ── unattended guardrails ──────────────────────────────────────────────────


class TestUnattendedHook:
    def _hook(self):
        return UnattendedApprovalHook({"read", "network", "memory"}, session_id="t")

    def test_allows_listed_scope(self):
        assert self._hook().before_tool("WebSearch", {}, _ctx()) is None

    def test_denies_unlisted_scope(self):
        out = self._hook().before_tool("BrowserNavigate", {"url": "https://x"}, _ctx())
        assert out and "not permitted" in out["content"]

    def test_hard_deny_beats_allowlist(self):
        hook = UnattendedApprovalHook({"read", "shell", "desktop", "system"}, session_id="t")
        for tool in ("Bash", "Write"):
            out = hook.before_tool(tool, {}, _ctx())
            assert out and "not permitted" in out["content"], tool

    def test_never_parks_pending(self):
        hook = self._hook()
        hook.before_tool("Bash", {}, _ctx())
        assert hook.pending_requests() == []

    def test_never_auto_approves(self):
        assert self._hook().auto_approve_all is False


# ── runner (agent stubbed) ────────────────────────────────────────────────


class TestRunner:
    def _stub_agent(self, monkeypatch, behavior):
        import core.schedule_runner as runner

        calls = {"n": 0}

        async def fake(sched, session_id):
            calls["n"] += 1
            return await behavior(calls["n"])

        monkeypatch.setattr(runner, "_agent_run_inner", fake)
        return calls

    def test_success_records_history_and_advances(self, store, monkeypatch):
        import core.schedule_runner as runner

        async def ok(n):
            return ("all done", 4)

        self._stub_agent(monkeypatch, ok)
        s = _sched(name="nightly", kind="every", every_minutes=60)
        store.create(s)
        record = runner.run_schedule(store, s.id, force=True, reason="manual", retry_delay_sec=0)
        assert record["status"] == "ok"
        assert record["attempt"] == 1
        hist = store.history(s.id)
        assert len(hist) == 1 and hist[0]["summary"] == "all done" and hist[0]["turns"] == 4
        updated = store.get(s.id)
        assert updated.run_count == 1 and updated.last_status == "ok"
        assert updated.next_run_at >= s.next_run_at  # recomputed after the run

    def test_retry_then_success(self, store, monkeypatch):
        import core.schedule_runner as runner

        async def flaky(n):
            if n < 3:
                raise RuntimeError("boom")
            return ("recovered", 2)

        calls = self._stub_agent(monkeypatch, flaky)
        s = _sched(name="flaky", max_retries=3)
        store.create(s)
        record = runner.run_schedule(store, s.id, force=True, retry_delay_sec=0)
        assert record["status"] == "ok" and record["attempt"] == 3
        assert calls["n"] == 3
        assert len(store.history(s.id)) == 3

    def test_timeout_status(self, store, monkeypatch):
        import asyncio as _aio

        import core.schedule_runner as runner

        async def slow(n):
            await _aio.sleep(30)
            return ("too late", 1)

        self._stub_agent(monkeypatch, slow)
        s = _sched(name="slow", max_retries=0)
        s.max_runtime_sec = 1  # bypass validation floor for the test
        store.create(s)
        record = runner.run_schedule(store, s.id, force=True, retry_delay_sec=0)
        assert record["status"] == "timeout"
        assert "timed out" in store.history(s.id)[0]["summary"].lower()

    def test_hooks_restored(self, store, monkeypatch):
        import core.schedule_runner as runner
        from core.hooks import reset_hooks

        async def ok(n):
            return ("fine", 1)

        self._stub_agent(monkeypatch, ok)
        reset_hooks()
        register_plugin(UnattendedApprovalHook({"read"}, session_id="sentinel"))
        before = get_hooks()
        s = _sched(name="restore")
        store.create(s)
        runner.run_schedule(store, s.id, force=True, retry_delay_sec=0)
        assert get_hooks() is before

    def test_disabled_requires_force(self, store):
        import core.schedule_runner as runner

        s = _sched(name="off")
        s.enabled = False
        store.create(s)
        with pytest.raises(ValueError, match="disabled"):
            runner.run_schedule(store, s.id)


# ── agent tools ───────────────────────────────────────────────────────────


def _run(coro):
    return asyncio.run(coro)


class TestScheduleTools:
    def test_create_daily(self, data_dir):
        out = _run(
            sched_tools.ScheduleCreateTool().execute(
                name="briefing",
                prompt="summarize the news",
                daily_at="07:30",
                allow_scopes='["read","network"]',
            )
        )
        assert not out.error, out.text
        sched = out.metadata["schedule"]
        assert sched["kind"] == "daily" and sched["next_run_at"]
        assert sched["allow_scopes"] == ["read", "network"]

    def test_create_rejects_shell(self, data_dir):
        out = _run(
            sched_tools.ScheduleCreateTool().execute(
                name="evil",
                prompt="pwn",
                every_minutes=10,
                allow_scopes='["shell"]',
            )
        )
        assert out.error and "never be granted" in out.text

    def test_create_needs_one_timing(self, data_dir):
        out = _run(sched_tools.ScheduleCreateTool().execute(name="x", prompt="y"))
        assert out.error and "exactly one" in out.text

    def test_full_lifecycle(self, data_dir):
        create = sched_tools.ScheduleCreateTool()
        out = _run(create.execute(name="cycle", prompt="p", every_minutes=15))
        sid = out.metadata["schedule"]["id"]

        lst = _run(sched_tools.ScheduleListTool().execute())
        assert "cycle" in lst.text

        dis = _run(sched_tools.ScheduleDisableTool().execute(sid))
        assert not dis.error
        assert sched_tools.get_store().get(sid).enabled is False

        en = _run(sched_tools.ScheduleEnableTool().execute(sid))
        assert not en.error
        assert sched_tools.get_store().get(sid).enabled is True

        hist = _run(sched_tools.ScheduleHistoryTool().execute(sid))
        assert "No runs" in hist.text

        dele = _run(sched_tools.ScheduleDeleteTool().execute(sid))
        assert not dele.error
        assert sched_tools.get_store().get(sid) is None

    def test_digest_empty(self, data_dir):
        out = _run(sched_tools.ScheduleDigestTool().execute())
        assert "Nothing new" in out.text

    def test_tool_permissions_and_scopes(self):
        import core.trust as trust
        from core.approval_hook import _TOOL_PERMISSIONS
        from core.types import ToolPermissionLevel

        assert _TOOL_PERMISSIONS["ScheduleCreate"] == ToolPermissionLevel.REQUIRES_APPROVAL
        assert _TOOL_PERMISSIONS["ScheduleDelete"] == ToolPermissionLevel.REQUIRES_APPROVAL
        assert _TOOL_PERMISSIONS["ScheduleList"] == ToolPermissionLevel.ALWAYS_ALLOW
        assert _TOOL_PERMISSIONS["ScheduleDigest"] == ToolPermissionLevel.ALWAYS_ALLOW
        for name in ("ScheduleCreate", "ScheduleHistory", "ScheduleRunNow"):
            assert trust.scope_of(name) == "agents", name


# ── daemon ────────────────────────────────────────────────────────────────


class TestDaemon:
    def test_run_once_fires_due(self, store, monkeypatch):
        import core.schedule_daemon as daemon
        import core.schedule_runner as runner

        fired = []
        monkeypatch.setattr(
            runner,
            "run_schedule",
            lambda st, sid, **kw: fired.append(sid) or {"status": "ok", "schedule_id": sid},
        )
        s = _sched(name="due-now")
        s.next_run_at = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        store.create(s)
        svc = daemon.SchedulerService(store=store, poll_sec=60)
        records = svc.run_once()
        assert fired == [s.id] and len(records) == 1

    def test_run_once_skips_disabled(self, store, monkeypatch):
        import core.schedule_daemon as daemon
        import core.schedule_runner as runner

        fired = []
        monkeypatch.setattr(
            runner,
            "run_schedule",
            lambda st, sid, **kw: fired.append(sid) or {"status": "ok"},
        )
        s = _sched(name="paused")
        s.enabled = False
        s.next_run_at = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        store.create(s)
        assert daemon.SchedulerService(store=store).run_once() == []
        assert fired == []
