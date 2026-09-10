"""
Run lock — shared mutual exclusion between scheduled and interactive runs.

Scheduled runs (core/schedule_runner.py) and interactive agent runs (the CLI
in main.py, the HQ web server) mutate the same state: workspace files, the
trust store, the schedule store, the audit log. This module provides one
cross-process advisory lock — a lock file in the data dir (fcntl on POSIX,
msvcrt on Windows) — so both execution paths serialize around run execution.

The lock is re-entrant *within a process*: a thread running in a process
that already holds the lock may re-acquire it. This keeps nested calls safe
— e.g. an interactive agent run invoking the ScheduleRunNow tool, which
itself calls run_schedule() on the same thread. Threads (and processes)
that don't already hold it block until it is released.

No new dependencies: fcntl/msvcrt from the standard library.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import threading
import time
from pathlib import Path

#: Default lock name guarding agent-run execution / shared state mutation.
RUN_LOCK_NAME = "agent-run"


def _lock_path(name: str) -> Path:
    from core.trust import _data_dir

    d = _data_dir() / "locks"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{name}.lock"


class _LockState:
    """Per-name lock state, shared process-wide."""

    def __init__(self, name: str):
        self.name = name
        self._guard = threading.Lock()
        self._owner: int | None = None  # thread ident of the owning thread
        self._count = 0
        self._fd: int | None = None

    # ── OS-level locking ──────────────────────────────────────────────

    def _os_acquire(self, timeout: float | None) -> int | None:
        """Blocking OS lock. Returns the fd, or None on timeout."""
        path = _lock_path(self.name)
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
        deadline = None if timeout is None else time.monotonic() + timeout
        try:
            if os.name == "nt":
                import msvcrt

                while True:
                    try:
                        # Lock the first byte, non-blocking; poll on contention.
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                        return fd
                    except OSError:
                        if deadline is not None and time.monotonic() >= deadline:
                            os.close(fd)
                            return None
                        time.sleep(0.05)
            else:
                import fcntl

                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        return fd
                    except OSError:
                        if deadline is not None and time.monotonic() >= deadline:
                            os.close(fd)
                            return None
                        time.sleep(0.05)
        except BaseException:
            os.close(fd)
            raise

    def _os_release(self, fd: int) -> None:
        try:
            if os.name == "nt":
                import msvcrt

                with contextlib.suppress(OSError):
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        finally:
            # Closing the fd releases any flock/msvcrt lock held on it.
            os.close(fd)

    # ── bookkeeping ───────────────────────────────────────────────────

    def is_owner(self, ident: int) -> bool:
        with self._guard:
            return self._owner == ident

    def acquire(self, timeout: float | None = None, owner: int | None = None) -> bool:
        """Acquire the lock for ``owner`` (defaults to the calling thread).

        Re-entrant when the owner already holds it. Returns False on timeout.
        """
        me = threading.get_ident() if owner is None else owner
        with self._guard:
            if self._owner == me:
                self._count += 1
                return True
        fd = self._os_acquire(timeout)
        if fd is None:
            return False
        with self._guard:
            if self._owner is None:
                self._owner = me
                self._count = 1
                self._fd = fd
            elif self._owner == me:
                # Lost a race with our own re-entrant claim; drop the spare fd.
                self._count += 1
                os.close(fd)
            else:
                # Defensive: the OS lock is exclusive, so this is unreachable
                # unless the lock was stolen between flock() and the guard.
                os.close(fd)
                return False
        return True

    def release(self, owner: int | None = None) -> None:
        me = threading.get_ident() if owner is None else owner
        with self._guard:
            if self._owner != me:
                raise RuntimeError(f"run lock {self.name!r} released by non-owner")
            self._count -= 1
            if self._count > 0:
                return
            fd, self._fd = self._fd, None
            self._owner = None
        if fd is not None:
            self._os_release(fd)


_states: dict[str, _LockState] = {}
_states_guard = threading.Lock()


def _state_for(name: str) -> _LockState:
    with _states_guard:
        state = _states.get(name)
        if state is None:
            state = _LockState(name)
            _states[name] = state
        return state


class _ExclusiveRun:
    """Context manager (sync + async) for the shared run lock."""

    def __init__(self, name: str = RUN_LOCK_NAME, timeout: float | None = None):
        self._name = name
        self._timeout = timeout
        self._state = _state_for(name)

    def _timeout_error(self) -> TimeoutError:
        return TimeoutError(f"timed out acquiring run lock {self._name!r}")

    # -- sync --
    def __enter__(self) -> _ExclusiveRun:
        if not self._state.acquire(self._timeout):
            raise self._timeout_error()
        return self

    def __exit__(self, *exc) -> bool:
        self._state.release()
        return False

    # -- async: the blocking OS wait runs in a worker thread, but ownership
    # is claimed for the event-loop thread so nested sync use on that thread
    # (e.g. an agent tool calling run_schedule()) stays re-entrant. --
    async def __aenter__(self) -> _ExclusiveRun:
        me = threading.get_ident()
        if self._state.is_owner(me):
            self._state.acquire(0, owner=me)
            return self
        ok = await asyncio.to_thread(self._state.acquire, self._timeout, me)
        if not ok:
            raise self._timeout_error()
        return self

    async def __aexit__(self, *exc) -> bool:
        self._state.release(threading.get_ident())
        return False


def run_exclusive(name: str = RUN_LOCK_NAME, timeout: float | None = None) -> _ExclusiveRun:
    """Return a context manager holding the shared run lock.

    Use ``with run_exclusive():`` in sync code and
    ``async with run_exclusive():`` in async code around run execution /
    shared-state mutation. ``timeout`` (seconds) bounds how long acquisition
    waits; None waits indefinitely. A stale lock cannot wedge the system:
    the OS releases the lock file when the holding process dies.
    """
    return _ExclusiveRun(name, timeout)
