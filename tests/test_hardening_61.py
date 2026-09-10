"""Regression tests for LuckyD 6.1 trust/scheduling hardening.

1. Legacy ``hook.auto_approve_all`` bypasses closed: the flag is inert
   (deprecation warning, reads False), env flags no longer auto-approve,
   every policy skip is audited, and the explicit per-run
   ``auto_approve_low_risk`` mode stays scoped to low-risk tools.
2. Shared run lock (core/run_lock.py) between scheduled and interactive runs.
3. Approval-hook coverage on the HQ schedule dashboard routes.
4. Zero-valued schedule-update fields are preserved (explicit ``is None``).
"""

from __future__ import annotations

import asyncio
import http.client
import json
import logging
import os
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import core.trust as trust
from core.approval_hook import ApprovalHook
from core.hooks import HookContext
from core.scheduler import ScheduleStore, build_schedule

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def isolated_trust(tmp_path, monkeypatch):
    """Point trust storage at a temp dir and reset singletons."""
    monkeypatch.setenv("LUCKYD_DATA_DIR", str(tmp_path))
    trust._audit_log = None
    trust._policy = None
    yield tmp_path
    trust._audit_log = None
    trust._policy = None


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "luckyd"
    monkeypatch.setenv("LUCKYD_DATA_DIR", str(d))
    return d


@pytest.fixture()
def store(data_dir):
    return ScheduleStore()


def _ctx() -> HookContext:
    return HookContext(turn=1, messages=[], config={})


def _run(coro):
    return asyncio.run(coro)


# ── 1. legacy auto_approve_all bypasses closed ─────────────────────────────


class TestLegacyAutoApproveClosed:
    def _hook(self, **kw) -> ApprovalHook:
        kw.setdefault("timeout_ms", 500)
        return ApprovalHook(session_id="t61", **kw)

    def test_auto_approve_all_is_inert(self, isolated_trust, caplog):
        hook = self._hook()
        with caplog.at_level(logging.WARNING, logger="core.approval_hook"):
            hook.auto_approve_all = True  # legacy usage: must warn and do nothing
        assert hook.auto_approve_all is False
        assert any("deprecated" in r.message for r in caplog.records)
        # A dangerous tool still requires approval — nothing is silently granted.
        decision, _ = hook.evaluate_policy("Bash", {"_id": "1", "command": "x"}, _ctx())
        assert decision == "needs_approval"

    def test_env_flags_no_longer_auto_approve(self, isolated_trust, monkeypatch):
        monkeypatch.setenv("CODING_AGENT_AUTO_APPROVE", "1")
        monkeypatch.setenv("CODING_AGENT_YOLO", "true")
        hook = self._hook()
        decision, _ = hook.evaluate_policy("Bash", {"_id": "1", "command": "x"}, _ctx())
        assert decision == "needs_approval"

    def test_auto_approve_low_risk_is_scoped_and_audited(self, isolated_trust):
        hook = self._hook()
        hook.auto_approve_low_risk = True
        decision, _ = hook.evaluate_policy("Read", {"_id": "1", "path": "x"}, _ctx())
        assert decision == "approved"
        # …but medium/high-risk tools still go through the trust policy.
        for tool in ("Bash", "Edit", "ScheduleCreate"):
            decision, _ = hook.evaluate_policy(tool, {"_id": tool}, _ctx())
            assert decision == "needs_approval", tool
        recs = trust.get_audit_log().recent(tool="Read", decision="auto")
        assert recs and "low-risk" in recs[0]["summary"]

    def test_every_policy_skip_is_audited(self, isolated_trust):
        trust.get_policy().set_mode("auto")
        hook = self._hook()
        decision, _ = hook.evaluate_policy("Bash", {"_id": "1", "command": "x"}, _ctx())
        assert decision == "approved"
        recs = trust.get_audit_log().recent(tool="Bash", decision="auto")
        assert recs and recs[0]["summary"]
        # Per-scope allow-list skips are audited too (previously silent).
        trust.get_policy().set_mode("ask")
        trust.get_policy().set_scope_policy("read", "allow")
        decision, _ = hook.evaluate_policy("Read", {"_id": "2", "path": "x"}, _ctx())
        assert decision == "approved"
        recs = trust.get_audit_log().recent(tool="Read", decision="auto")
        assert any("allowed by trust policy" in r["summary"] for r in recs)


# ── 2. shared run lock ─────────────────────────────────────────────────────


class TestRunLock:
    def test_threads_serialize(self, data_dir):
        from core.run_lock import run_exclusive

        events: list[tuple[str, float]] = []

        def worker():
            # Hold the lock across the whole critical section (list.append
            # is thread-safe; no extra guard needed).
            with run_exclusive():
                events.append(("enter", time.monotonic()))
                time.sleep(0.2)
                events.append(("exit", time.monotonic()))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        assert len(events) == 4
        enters = sorted(t for k, t in events if k == "enter")
        exits = sorted(t for k, t in events if k == "exit")
        assert enters[1] >= exits[0], "lock did not serialize threads"

    def test_reentrant_same_thread(self, data_dir):
        from core.run_lock import run_exclusive

        # Nested: same thread must not deadlock.
        with run_exclusive(), run_exclusive():
            pass

    def test_acquire_timeout(self, data_dir):
        from core.run_lock import run_exclusive

        release = threading.Event()
        acquired = threading.Event()

        def holder():
            with run_exclusive():
                acquired.set()
                release.wait(timeout=10)

        t = threading.Thread(target=holder, daemon=True)
        t.start()
        assert acquired.wait(5)
        try:
            with pytest.raises(TimeoutError), run_exclusive(timeout=0.3):
                pass  # pragma: no cover
        finally:
            release.set()
            t.join(timeout=5)

    def test_cross_process_serialization(self, data_dir):
        """A second *process* holding the lock blocks acquisition here."""
        from core.run_lock import run_exclusive

        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "from core.run_lock import run_exclusive\n"
                "import time\n"
                "with run_exclusive():\n"
                "    time.sleep(4)\n",
            ],
            env={**os.environ, "LUCKYD_DATA_DIR": str(data_dir)},
            cwd=str(REPO_ROOT),
        )
        try:
            time.sleep(1.0)  # let the child acquire the lock
            assert child.poll() is None
            with pytest.raises(TimeoutError), run_exclusive(timeout=1.0):
                pass  # pragma: no cover — must time out, not acquire
        finally:
            child.wait(timeout=10)


class TestScheduledRunSerialization:
    def _two_schedules(self, store):
        s1 = build_schedule(name="a", prompt="p", kind="every", every_minutes=60)
        s2 = build_schedule(name="b", prompt="p", kind="every", every_minutes=60)
        store.create(s1)
        store.create(s2)
        return s1, s2

    def test_concurrent_scheduled_runs_serialize(self, store, monkeypatch, data_dir):
        """(a) two scheduled runs at once must not overlap."""
        import core.schedule_runner as runner

        windows: list[list] = []
        wlock = threading.Lock()

        async def fake(sched, session_id):
            with wlock:
                windows.append([time.monotonic(), None])
                idx = len(windows) - 1
            await asyncio.sleep(0.3)
            with wlock:
                windows[idx][1] = time.monotonic()
            return ("done", 1)

        monkeypatch.setattr(runner, "_agent_run_inner", fake)
        s1, s2 = self._two_schedules(store)
        results: dict = {}

        def runit(sid, key):
            results[key] = runner.run_schedule(store, sid, reason="test", retry_delay_sec=0)

        t1 = threading.Thread(target=runit, args=(s1.id, "a"))
        t2 = threading.Thread(target=runit, args=(s2.id, "b"))
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)
        assert results["a"]["status"] == "ok"
        assert results["b"]["status"] == "ok"
        (_e1, x1), (e2, _x2) = sorted((w[0], w[1]) for w in windows)
        assert e2 >= x1, "concurrent scheduled runs overlapped"

    def test_scheduled_and_interactive_serialize(self, store, monkeypatch, data_dir):
        """(b) a scheduled run waits while an interactive run holds the lock."""
        import core.schedule_runner as runner
        from core.run_lock import run_exclusive

        entered = threading.Event()

        async def fake(sched, session_id):
            entered.set()
            return ("done", 1)

        monkeypatch.setattr(runner, "_agent_run_inner", fake)
        (s1, _s2) = self._two_schedules(store)
        outcome: dict = {}

        with run_exclusive():  # simulate an interactive run holding the lock
            t = threading.Thread(
                target=lambda: outcome.update(
                    rec=runner.run_schedule(store, s1.id, reason="test", retry_delay_sec=0)
                ),
                daemon=True,
            )
            t.start()
            assert not entered.wait(0.8), (
                "scheduled run started while an interactive run held the lock"
            )
        t.join(timeout=30)
        assert entered.is_set()
        assert outcome["rec"]["status"] == "ok"


# ── 3. approval-hook coverage on schedule dashboard routes ─────────────────


SENTINEL = "test-token-61"


def _post(port: int, path: str, body: dict | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    payload = json.dumps(body or {}).encode()
    conn.request(
        "POST",
        path,
        body=payload,
        headers={
            "Content-Type": "application/json",
            "Cookie": f"luckyd_hq={SENTINEL}",
        },
    )
    resp = conn.getresponse()
    data = resp.read().decode("utf-8", "replace")
    conn.close()
    return resp.status, json.loads(data or "{}")


def _get(port: int, path: str):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    conn.request("GET", path, headers={"Cookie": f"luckyd_hq={SENTINEL}"})
    resp = conn.getresponse()
    data = resp.read().decode("utf-8", "replace")
    conn.close()
    return resp.status, json.loads(data or "{}")


@pytest.fixture()
def hq_server(tmp_path, monkeypatch):
    import tools.schedule_tools as sched_tools
    import web_server

    monkeypatch.setenv("LUCKYD_DATA_DIR", str(tmp_path / "luckyd"))
    trust._audit_log = None
    trust._policy = None
    sched_tools._store = None
    monkeypatch.setattr(web_server, "_SCHED_STORE", None)
    monkeypatch.setattr(web_server, "_TOKEN", SENTINEL)
    hook = ApprovalHook(session_id="test-hq")
    monkeypatch.setattr(web_server, "_APPROVAL_HOOK", hook)
    server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.HQHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_port, hook
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    trust._audit_log = None
    trust._policy = None


def _valid_create_body(**kw):
    body = {
        "name": "morning check",
        "prompt": "check things",
        "daily_at": "07:30",
        "allow_scopes": ["read", "network"],
        "max_turns": 5,
        "max_runtime_minutes": 2,
        "max_retries": 0,
    }
    body.update(kw)
    return body


def _schedule_id(port: int) -> str:
    # Bypass the HTTP gate to set up fixtures: the tool itself is not the
    # route under test. (The tool takes allow_scopes as a JSON string,
    # unlike the HTTP route which accepts a list.)
    import tools.schedule_tools as sched_tools

    body = _valid_create_body()
    body["allow_scopes"] = json.dumps(body["allow_scopes"])
    out = _run(sched_tools.ScheduleCreateTool().execute(**body))
    assert not out.error, out.text
    return out.metadata["schedule"]["id"]


class TestScheduleRouteApproval:
    def test_create_denied_by_default_policy(self, hq_server):
        port, _hook = hq_server
        status, data = _post(port, "/api/schedules", _valid_create_body())
        assert status == 403, data
        assert data.get("needs_approval") is True
        status, data = _get(port, "/api/schedules")
        assert status == 200 and data["schedules"] == []

    def test_create_allowed_when_policy_explicitly_allows(self, hq_server):
        port, _hook = hq_server
        trust.get_policy().set_mode("auto")
        status, data = _post(port, "/api/schedules", _valid_create_body())
        assert status == 200, data
        assert data["schedule"]["name"] == "morning check"
        # …and zero values survive the HTTP create path.
        assert data["schedule"]["max_retries"] == 0

    def test_mutating_routes_denied_by_default(self, hq_server):
        port, _hook = hq_server
        sid = _schedule_id(port)
        for action in ("update", "delete", "enable", "disable", "run"):
            status, data = _post(port, f"/api/schedules/{sid}/{action}", {"max_retries": 0})
            assert status == 403, (action, data)
            assert data.get("needs_approval") is True, (action, data)
        # Nothing changed: the schedule still exists and is enabled.
        status, data = _get(port, "/api/schedules")
        assert status == 200 and len(data["schedules"]) == 1
        assert data["schedules"][0]["enabled"] is True

    def test_mutating_routes_allowed_in_auto_mode(self, hq_server, monkeypatch):
        import web_server

        port, _hook = hq_server
        trust.get_policy().set_mode("auto")
        sid = _schedule_id(port)

        ran = threading.Event()
        monkeypatch.setattr(web_server, "_sched_run_background", lambda _sid: ran.set())

        status, data = _post(port, f"/api/schedules/{sid}/disable", {})
        assert status == 200, data
        status, data = _post(port, f"/api/schedules/{sid}/enable", {})
        assert status == 200, data
        status, data = _post(port, f"/api/schedules/{sid}/update", {"max_retries": 0})
        assert status == 200, data
        status, data = _post(port, f"/api/schedules/{sid}/run", {})
        assert status == 200 and data.get("started") is True, data
        assert ran.wait(5), "run-now did not trigger the background run"
        status, data = _post(port, f"/api/schedules/{sid}/delete", {})
        assert status == 200, data
        status, data = _get(port, "/api/schedules")
        assert data["schedules"] == []

    def test_denial_is_audited(self, hq_server):
        port, _hook = hq_server
        status, _data = _post(port, "/api/schedules", _valid_create_body())
        assert status == 403
        recs = trust.get_audit_log().recent(tool="ScheduleCreate", decision="denied")
        assert recs and recs[0]["summary"]


# ── 4. zero-valued schedule-update fields ──────────────────────────────────


class TestZeroValuedFields:
    def _tool(self):
        import tools.schedule_tools as sched_tools

        return sched_tools

    def _make(self, **kw):
        sched_tools = self._tool()
        args = dict(name="t", prompt="do the thing", daily_at="07:30")
        args.update(kw)
        out = _run(sched_tools.ScheduleCreateTool().execute(**args))
        assert not out.error, out.text
        return out.metadata["schedule"]["id"]

    def _get_sched(self, sid):
        return self._tool().get_store().get(sid)

    def test_update_max_retries_zero(self, data_dir):
        sched_tools = self._tool()
        sid = self._make()
        out = _run(sched_tools.ScheduleUpdateTool().execute(sid, max_retries=0))
        assert not out.error, out.text
        assert self._get_sched(sid).max_retries == 0

    def test_update_max_turns_zero_rejected_loudly(self, data_dir):
        sched_tools = self._tool()
        sid = self._make()
        out = _run(sched_tools.ScheduleUpdateTool().execute(sid, max_turns=0))
        assert out.error and "max_turns" in out.text
        assert self._get_sched(sid).max_turns == 25  # unchanged

    def test_update_every_minutes_zero_rejected_loudly(self, data_dir):
        sched_tools = self._tool()
        sid = self._make()
        out = _run(sched_tools.ScheduleUpdateTool().execute(sid, every_minutes=0))
        assert out.error and "interval" in out.text

    def test_update_empty_string_means_unset(self, data_dir):
        sched_tools = self._tool()
        sid = self._make()
        out = _run(sched_tools.ScheduleUpdateTool().execute(sid, name="", prompt=""))
        assert not out.error, out.text
        s = self._get_sched(sid)
        assert s.name == "t" and s.prompt == "do the thing"

    def test_update_can_switch_timing_with_empty_cron(self, data_dir):
        sched_tools = self._tool()
        sid = self._make(cron="0 7 * * *", daily_at="")
        out = _run(sched_tools.ScheduleUpdateTool().execute(sid, cron="", every_minutes=30))
        assert not out.error, out.text
        s = self._get_sched(sid)
        assert s.kind == "every" and s.every_minutes == 30

    def test_create_max_retries_zero(self, data_dir):
        sid = self._make(max_retries=0)
        assert self._get_sched(sid).max_retries == 0

    def test_explicit_empty_scopes_not_replaced_with_defaults(self, data_dir):
        scopes = build_schedule(
            name="t",
            prompt="p",
            kind="daily",
            daily_at="07:30",
            allow_scopes=[],
        ).allow_scopes
        assert scopes == []
