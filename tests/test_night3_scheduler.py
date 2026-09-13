"""Night-3 tests: core/scheduler.py + core/schedule_runner.py.

Covers the cron parser (incl. dom/dow OR semantics and name fields),
schedule validation, next-run computation, the SQLite store (CRUD, history,
digest watermark, due), and run_schedule (unknown/disabled guards, retries,
hooks swap/restore, always-advancing next run).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import core.trust as trust_mod
from core.scheduler import (
    CronSpec,
    Schedule,
    ScheduleStore,
    build_schedule,
    compute_next_run,
    cron_next,
    new_schedule_id,
    validate_schedule_fields,
)


@pytest.fixture()
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("LUCKYD_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(trust_mod, "_policy", None)
    monkeypatch.setattr(trust_mod, "_audit_log", None)
    yield tmp_path


@pytest.fixture()
def store(data_dir, tmp_path):
    return ScheduleStore(path=tmp_path / "sched.db")


def _sched(**kw):
    base = dict(name="night3", prompt="do the thing", kind="every", every_minutes=30)
    base.update(kw)
    return build_schedule(**base)


# ── cron parsing ───────────────────────────────────────────────────────


def test_cron_parse_steps_and_names():
    spec = CronSpec.parse("*/15 9-17 * * mon-fri")
    assert spec.minute == set(range(0, 60, 15))
    assert spec.hour == set(range(9, 18))
    assert spec.dow == {1, 2, 3, 4, 5}
    assert spec.dom_star and spec.dow_star is False


def test_cron_parse_month_names():
    spec = CronSpec.parse("0 0 1 jan,jun *")
    assert spec.month == {1, 6}


def test_cron_parse_rejects_bad_field_count():
    with pytest.raises(ValueError, match="5 fields"):
        CronSpec.parse("* * * *")


def test_cron_parse_rejects_out_of_range():
    with pytest.raises(ValueError, match="out of range"):
        CronSpec.parse("99 * * * *")


def test_cron_parse_rejects_backwards_range():
    with pytest.raises(ValueError, match="backwards"):
        CronSpec.parse("* 17-9 * * *")


def test_cron_parse_rejects_zero_step():
    with pytest.raises(ValueError, match="step"):
        CronSpec.parse("*/0 * * * *")


def test_cron_parse_rejects_empty_part():
    with pytest.raises(ValueError, match="empty"):
        CronSpec.parse("0,,30 * * * *")


def test_cron_dom_dow_or_semantics():
    """Standard cron: when both dom and dow are restricted, either matches."""
    spec = CronSpec.parse("0 12 1 * 1")  # noon on the 1st OR on Mondays
    monday_not_first = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)  # a Monday
    first_not_monday = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)  # a Thursday
    neither = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)  # a Tuesday
    assert monday_not_first.weekday() == 0
    assert spec.matches(monday_not_first)
    assert spec.matches(first_not_monday)
    assert not spec.matches(neither)


def test_cron_dom_star_only_dow_restricted():
    spec = CronSpec.parse("0 12 * * 3")  # noon Wednesdays
    wed = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
    thu = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
    assert wed.weekday() == 2
    assert spec.matches(wed)
    assert not spec.matches(thu)


def test_cron_next_is_strictly_after():
    after = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
    nxt = cron_next("0 12 * * *", after)
    assert nxt == datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


def test_cron_next_unmatchable_raises():
    # Feb 30th never exists: parses, but can never match.
    with pytest.raises(ValueError, match="no cron match"):
        cron_next("0 0 30 2 *", datetime(2026, 9, 13, tzinfo=timezone.utc))


# ── validation ─────────────────────────────────────────────────────────


def test_validate_requires_name_and_prompt():
    with pytest.raises(ValueError, match="name is required"):
        validate_schedule_fields(name="  ", prompt="p", kind="every", every_minutes=30)
    with pytest.raises(ValueError, match="prompt"):
        validate_schedule_fields(name="n", prompt="  ", kind="every", every_minutes=30)


def test_validate_unknown_kind():
    with pytest.raises(ValueError, match="unknown schedule kind"):
        validate_schedule_fields(name="n", prompt="p", kind="weekly")


def test_validate_cron_passthrough():
    with pytest.raises(ValueError, match="5 fields"):
        validate_schedule_fields(name="n", prompt="p", kind="cron", cron="bad")


def test_validate_every_min_interval():
    with pytest.raises(ValueError, match="at least 5"):
        validate_schedule_fields(name="n", prompt="p", kind="every", every_minutes=4)


def test_validate_daily_format_and_range():
    with pytest.raises(ValueError, match="HH:MM"):
        validate_schedule_fields(name="n", prompt="p", kind="daily", daily_at="9am")
    with pytest.raises(ValueError, match="valid 24h"):
        validate_schedule_fields(name="n", prompt="p", kind="daily", daily_at="25:00")
    assert validate_schedule_fields(name="n", prompt="p", kind="daily", daily_at="9:30") == [
        "read",
        "network",
        "memory",
    ]


def test_validate_unknown_and_hard_deny_scopes():
    with pytest.raises(ValueError, match="unknown permission scopes"):
        validate_schedule_fields(
            name="n", prompt="p", kind="every", every_minutes=30, allow_scopes=["nope"]
        )
    with pytest.raises(ValueError, match="can never be granted"):
        validate_schedule_fields(
            name="n", prompt="p", kind="every", every_minutes=30, allow_scopes=["shell"]
        )


def test_validate_numeric_bounds():
    with pytest.raises(ValueError, match="max_turns"):
        validate_schedule_fields(name="n", prompt="p", kind="every", every_minutes=30, max_turns=0)
    with pytest.raises(ValueError, match="max_runtime_sec"):
        validate_schedule_fields(
            name="n", prompt="p", kind="every", every_minutes=30, max_runtime_sec=59
        )
    with pytest.raises(ValueError, match="max_retries"):
        validate_schedule_fields(
            name="n", prompt="p", kind="every", every_minutes=30, max_retries=6
        )


def test_build_schedule_computes_next_run():
    sched = _sched(kind="every", every_minutes=30)
    assert sched.next_run_at
    assert sched.id and len(sched.id) == 8
    nxt = datetime.fromisoformat(sched.next_run_at)
    assert nxt > datetime.now(timezone.utc)
    # allow_scopes is copied, not shared with the default list.
    assert sched.allow_scopes == ["read", "network", "memory"]
    assert new_schedule_id() != new_schedule_id()


def test_schedule_dict_roundtrip():
    sched = _sched(kind="daily", daily_at="08:00")
    back = Schedule.from_dict(sched.to_dict())
    assert back.name == sched.name and back.daily_at == "08:00"
    assert back.allow_scopes == sched.allow_scopes
    # Missing allow_scopes defaults to empty (not shared mutable state).
    d = sched.to_dict()
    d.pop("allow_scopes")
    assert Schedule.from_dict(d).allow_scopes == []


def test_compute_next_run_every():
    sched = _sched(kind="every", every_minutes=30)
    after = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
    assert compute_next_run(sched, after) == after + timedelta(minutes=30)


def test_compute_next_run_daily_before_and_after():
    sched = _sched(kind="daily", daily_at="18:00")
    morning = datetime(2026, 9, 13, 8, 0, tzinfo=timezone.utc)
    evening = datetime(2026, 9, 13, 20, 0, tzinfo=timezone.utc)
    nxt = compute_next_run(sched, morning)
    assert (nxt - morning) < timedelta(days=1)
    assert nxt.replace(tzinfo=timezone.utc).astimezone().hour == 18
    nxt2 = compute_next_run(sched, evening)
    assert (nxt2 - evening) < timedelta(days=1, hours=1)
    assert (nxt2 - evening) > timedelta(hours=12)


def test_compute_next_run_cron():
    sched = _sched(kind="cron", cron="0 12 * * *")
    after = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
    assert compute_next_run(sched, after) == datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


# ── store ──────────────────────────────────────────────────────────────


def test_store_crud(store):
    sched = _sched(name="crud")
    store.create(sched)
    got = store.get(sched.id)
    assert got is not None and got.name == "crud"
    assert got.enabled is True
    assert isinstance(got.enabled, bool)
    sched.enabled = False
    store.update(sched)
    assert store.get(sched.id).enabled is False
    assert store.get(sched.id).updated_at != ""
    assert any(s.id == sched.id for s in store.list())
    assert sched.id not in [s.id for s in store.list(enabled_only=True)]
    assert store.delete(sched.id) is True
    assert store.get(sched.id) is None
    assert store.delete(sched.id) is False


def test_store_get_unknown(store):
    assert store.get("missing") is None


def test_store_run_history(store):
    sched = _sched(name="hist")
    store.create(sched)
    run_id = store.record_run_start(sched.id, sched.name, attempt=1)
    store.record_run_end(run_id, status="ok", summary="done", turns=3, duration_sec=1.5)
    hist = store.history(sched.id)
    assert len(hist) == 1
    assert hist[0]["status"] == "ok" and hist[0]["turns"] == 3
    assert store.history("other-schedule") == []
    # Long summaries/errors are truncated before storage.
    run_id2 = store.record_run_start(sched.id, sched.name)
    store.record_run_end(run_id2, status="error", summary="x" * 5000, error="e" * 5000)
    hist2 = store.history(sched.id, limit=1)
    assert len(hist2[0]["summary"]) <= 2000
    assert len(hist2[0]["error"]) <= 1000


def test_store_digest_watermark(store, monkeypatch):
    # Drive the clock explicitly: _now_iso has second precision, so a run
    # that ends in the same second as mark_digest_seen() is (by design)
    # excluded by the `finished_at > watermark` comparison. Using fixed
    # timestamps keeps the test deterministic without 1s sleeps.
    import core.scheduler as sch

    base = datetime(2026, 9, 13, 8, 0, 0, tzinfo=timezone.utc)
    ticks = {"n": 0}

    def fake_now():
        ticks["n"] += 1
        return (base + timedelta(seconds=ticks["n"])).isoformat(timespec="seconds")

    monkeypatch.setattr(sch, "_now_iso", fake_now)
    sched = _sched(name="digest")
    store.create(sched)
    run_id = store.record_run_start(sched.id, sched.name)
    store.record_run_end(run_id, status="ok", summary="done")
    assert [r["id"] for r in store.digest()] == [run_id]
    store.mark_digest_seen()
    assert store.digest() == []
    run_id2 = store.record_run_start(sched.id, sched.name)
    store.record_run_end(run_id2, status="ok", summary="done2")
    assert [r["id"] for r in store.digest()] == [run_id2]
    # explicit since_iso overrides the watermark
    assert len(store.digest(since_iso="1970-01-01T00:00:00+00:00")) == 2


def test_store_due(store):
    sched = _sched(name="due")
    store.create(sched)
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")
    sched.next_run_at = past
    store.update(sched)
    assert [s.id for s in store.due()] == [sched.id]
    sched.next_run_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(
        timespec="seconds"
    )
    store.update(sched)
    assert store.due() == []
    # Disabled schedules are never due even when overdue.
    sched.next_run_at = past
    sched.enabled = False
    store.update(sched)
    assert store.due() == []
    # Schedules without next_run_at are skipped (not a crash).
    sched.next_run_at = ""
    sched.enabled = True
    store.update(sched)
    assert store.due() == []


def test_store_meta(store):
    assert store.get_meta("missing") is None
    store.set_meta("k", "v")
    assert store.get_meta("k") == "v"


# ── run_schedule ───────────────────────────────────────────────────────


@pytest.fixture()
def fake_agent(monkeypatch):
    """Replace the real agent execution with a controllable fake."""
    import core.schedule_runner as runner

    calls = {"n": 0, "fail_first": False}

    async def fake(sched, session_id: str):
        calls["n"] += 1
        if calls["fail_first"] and calls["n"] == 1:
            raise RuntimeError("boom")
        return f"done for {sched.name}", 2

    monkeypatch.setattr(runner, "_run_agent_async", fake)
    return calls


def _make_store_with_schedule(tmp_path, **kw):
    st = ScheduleStore(path=tmp_path / "run.db")
    kind = kw.pop("kind", "every")
    params = dict(name="runner", prompt="task", kind=kind, every_minutes=30)
    params.update(kw)
    sched = build_schedule(**params)
    st.create(sched)
    return st, sched


def test_run_schedule_unknown_and_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCKYD_DATA_DIR", str(tmp_path))
    from core.schedule_runner import run_schedule

    st = ScheduleStore(path=tmp_path / "r.db")
    with pytest.raises(ValueError, match="unknown schedule"):
        run_schedule(st, "nope")
    sched = build_schedule(name="off", prompt="p", kind="every", every_minutes=30)
    sched.enabled = False
    st.create(sched)
    with pytest.raises(ValueError, match="disabled"):
        run_schedule(st, sched.id)
    # force= overrides the disabled guard
    import core.schedule_runner as runner

    async def fake(sched, session_id: str):
        return "ok", 1

    monkeypatch.setattr(runner, "_run_agent_async", fake)
    rec = run_schedule(st, sched.id, force=True, retry_delay_sec=0)
    assert rec["status"] == "ok"


def test_run_schedule_success_record(tmp_path, monkeypatch, fake_agent):
    monkeypatch.setenv("LUCKYD_DATA_DIR", str(tmp_path))
    from core.schedule_runner import run_schedule

    st, sched = _make_store_with_schedule(tmp_path)
    rec = run_schedule(st, sched.id, retry_delay_sec=0)
    assert rec["status"] == "ok"
    assert rec["attempt"] == 1
    assert rec["turns"] == 2
    assert rec["reason"] == "due"
    assert rec["next_run_at"]
    refreshed = st.get(sched.id)
    assert refreshed.last_status == "ok"
    assert refreshed.run_count == 1
    assert refreshed.next_run_at == rec["next_run_at"]
    hist = st.history(sched.id)
    assert len(hist) == 1 and hist[0]["status"] == "ok"


def test_run_schedule_retries_then_succeeds(tmp_path, monkeypatch, fake_agent):
    monkeypatch.setenv("LUCKYD_DATA_DIR", str(tmp_path))
    from core.schedule_runner import run_schedule

    fake_agent["fail_first"] = True
    st, sched = _make_store_with_schedule(tmp_path, max_retries=1)
    rec = run_schedule(st, sched.id, retry_delay_sec=0)
    assert rec["status"] == "ok"
    assert rec["attempt"] == 2
    assert fake_agent["n"] == 2
    hist = st.history(sched.id)
    # history() is newest-first; the ok retry (attempt 2) sorts before the
    # error (attempt 1) even when both share a 1-second timestamp.
    assert [h["status"] for h in hist] == ["ok", "error"]
    assert st.get(sched.id).last_status == "ok"


def test_run_schedule_all_attempts_fail(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCKYD_DATA_DIR", str(tmp_path))
    import core.schedule_runner as runner
    from core.schedule_runner import run_schedule

    async def always_fail(sched, session_id: str):
        raise RuntimeError("always")

    monkeypatch.setattr(runner, "_run_agent_async", always_fail)
    st, sched = _make_store_with_schedule(tmp_path, max_retries=2)
    rec = run_schedule(st, sched.id, retry_delay_sec=0)
    assert rec["status"] == "error"
    assert rec["attempt"] == 3
    assert "RuntimeError" in rec["error"]
    refreshed = st.get(sched.id)
    # Schedule still advances — it must not spin forever on failure.
    assert refreshed.last_status == "error"
    assert refreshed.run_count == 1
    assert refreshed.next_run_at


def test_run_schedule_timeout_status(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCKYD_DATA_DIR", str(tmp_path))
    import core.schedule_runner as runner
    from core.schedule_runner import run_schedule

    async def slow(sched, session_id: str):
        await asyncio.sleep(30)

    async def timeout_inner(sched, session_id: str):
        return await asyncio.wait_for(slow(sched, session_id), timeout=0.05)

    monkeypatch.setattr(runner, "_run_agent_async", timeout_inner)
    st, sched = _make_store_with_schedule(tmp_path, max_retries=0)
    rec = run_schedule(st, sched.id, retry_delay_sec=0)
    assert rec["status"] == "timeout"


def test_run_schedule_unmatchable_cron_parks_schedule(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCKYD_DATA_DIR", str(tmp_path))
    import core.schedule_runner as runner
    from core.schedule_runner import run_schedule

    async def fake(sched, session_id: str):
        return "ok", 1

    monkeypatch.setattr(runner, "_run_agent_async", fake)
    st, sched = _make_store_with_schedule(tmp_path, kind="cron", cron="0 12 * * *")
    # Corrupt the cron into something that can never match (e.g. a Feb-29
    # schedule whose next leap day is >1 year out): the runner must park it.
    sched.cron = "0 0 30 2 *"
    st.update(sched)
    run_schedule(st, sched.id, retry_delay_sec=0)
    refreshed = st.get(sched.id)
    assert refreshed.enabled is False  # cron can never match again → parked


def test_run_schedule_hooks_swapped_and_restored(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCKYD_DATA_DIR", str(tmp_path))
    import core.schedule_runner as runner
    from core.hooks import get_hooks
    from core.schedule_runner import run_schedule

    async def fake(sched, session_id: str):
        return "ok", 1

    monkeypatch.setattr(runner, "_run_agent_async", fake)
    before = get_hooks().before_tool

    st, sched = _make_store_with_schedule(tmp_path)
    run_schedule(st, sched.id, retry_delay_sec=0)
    after = get_hooks()
    assert after.before_tool is before  # host hooks restored
    assert len(after.before_tool) == len(before)


def test_run_schedule_empty_output_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("LUCKYD_DATA_DIR", str(tmp_path))
    import core.schedule_runner as runner
    from core.schedule_runner import run_schedule

    async def quiet(sched, session_id: str):
        return "", 0

    monkeypatch.setattr(runner, "_run_agent_async", quiet)
    st, sched = _make_store_with_schedule(tmp_path)
    rec = run_schedule(st, sched.id, retry_delay_sec=0)
    assert rec["summary"] == "(no output)"
