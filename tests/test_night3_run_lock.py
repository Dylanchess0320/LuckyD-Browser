"""Night-3 tests: core/run_lock.py — the cross-process agent-run lock.

Covers re-entrancy, ownership, timeouts, cross-thread serialization,
async context management, and lock-name independence.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid

import pytest

from core.run_lock import _state_for, run_exclusive


@pytest.fixture()
def data_dir(monkeypatch, tmp_path):
    """Point the trust data dir (and thus the lock files) at tmp."""
    monkeypatch.setenv("LUCKYD_DATA_DIR", str(tmp_path))
    return tmp_path


def _unique(name: str) -> str:
    return f"night3-{name}-{uuid.uuid4().hex[:8]}"


def test_lock_name_default():
    from core.run_lock import RUN_LOCK_NAME

    assert RUN_LOCK_NAME == "agent-run"


def test_basic_acquire_release(data_dir):
    name = _unique("basic")
    with run_exclusive(name):
        st = _state_for(name)
        assert st.is_owner(threading.get_ident())
    assert not st.is_owner(threading.get_ident())


def test_reentrant_same_thread(data_dir):
    name = _unique("reentrant")
    st = _state_for(name)
    assert st.acquire(5)
    assert st.acquire(5)  # re-entrant: no self-deadlock
    st.release()
    assert st.is_owner(threading.get_ident())  # still held once
    st.release()
    assert not st.is_owner(threading.get_ident())


def test_reentrant_via_context_manager(data_dir):
    name = _unique("reentrant-cm")
    with run_exclusive(name, timeout=5), run_exclusive(name, timeout=5):  # same thread: nested ok
        pass


def test_release_by_non_owner_raises(data_dir):
    name = _unique("nonowner")
    st = _state_for(name)
    assert st.acquire(5)
    errors = []

    def _try():
        try:
            st.release()
        except RuntimeError as e:
            errors.append(e)

    t = threading.Thread(target=_try)
    t.start()
    t.join()
    st.release()  # clean up our own claim
    assert errors and "non-owner" in str(errors[0])


def _hold_in_thread(name: str, acquired: threading.Event, release: threading.Event):
    """Acquire the named lock in this thread and hold it until `release` is set."""
    st = _state_for(name)
    assert st.acquire(5)
    acquired.set()
    assert release.wait(10)
    st.release()


def _start_holder(name: str) -> tuple[threading.Thread, threading.Event]:
    acquired = threading.Event()
    release = threading.Event()
    holder = threading.Thread(target=_hold_in_thread, args=(name, acquired, release), daemon=True)
    holder.start()
    assert acquired.wait(5), "holder thread failed to acquire"
    return holder, release


def test_run_exclusive_timeout_raises(data_dir):
    name = _unique("timeout-cm")
    holder, release = _start_holder(name)
    try:
        with pytest.raises(TimeoutError, match=name), run_exclusive(name, timeout=0.2):
            pass  # pragma: no cover
    finally:
        release.set()
        holder.join()


def test_acquire_timeout_returns_false(data_dir):
    name = _unique("timeout")
    holder, release = _start_holder(name)
    st = _state_for(name)

    def _contender():
        return st.acquire(timeout=0.3)

    result = []
    t = threading.Thread(target=lambda: result.append(_contender()))
    t.start()
    t.join()
    release.set()
    holder.join()
    assert result == [False]


def test_cross_thread_serialization(data_dir):
    """Two threads cannot hold the same lock at the same time."""
    name = _unique("serial")
    in_critical = threading.Event()
    contention = []

    def worker_a():
        with run_exclusive(name, timeout=10):
            in_critical.set()
            time.sleep(0.3)
            in_critical.clear()

    def worker_b(out: list):
        time.sleep(0.05)  # let A get the lock first
        start = time.monotonic()
        with run_exclusive(name, timeout=10):
            out.append(("in_critical_was_set", in_critical.is_set(), time.monotonic() - start))

    t_a = threading.Thread(target=worker_a)
    t_b = threading.Thread(target=worker_b, args=(contention,))
    t_a.start()
    t_b.start()
    t_a.join()
    t_b.join()
    (_flag, _was_set, waited) = contention[0]
    # B waited for A to finish and never observed A's critical section.
    assert not _was_set
    assert waited >= 0.15


def test_separate_names_are_independent(data_dir):
    name_a = _unique("indep-a")
    name_b = _unique("indep-b")
    # Different name acquires immediately — no blocking.
    with run_exclusive(name_a, timeout=5), run_exclusive(name_b, timeout=1):
        pass


def test_os_lock_released_on_timeout_path(data_dir):
    """A failed acquisition must close its fd (no leaked lock files held)."""
    name = _unique("fd-leak")
    holder, release = _start_holder(name)
    st = _state_for(name)
    try:
        for _ in range(3):
            assert st.acquire(timeout=0.1) is False
        assert st.is_owner(holder.ident)  # holder thread still owns it
        assert not st.is_owner(threading.get_ident())
    finally:
        release.set()
        holder.join()


def test_async_context_manager_reentrant(data_dir):
    async def _main():
        name = _unique("async")
        async with run_exclusive(name, timeout=5), run_exclusive(name, timeout=5):  # nested ok
            pass

    asyncio.run(_main())


def test_async_timeout_when_locked_elsewhere(data_dir):
    async def _main():
        name = _unique("async-timeout")
        holder, release = _start_holder(name)
        try:
            with pytest.raises(TimeoutError):
                async with run_exclusive(name, timeout=0.3):
                    pass  # pragma: no cover
        finally:
            release.set()
            holder.join()

    asyncio.run(_main())
