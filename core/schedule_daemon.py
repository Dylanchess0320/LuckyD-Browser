"""
Scheduler daemon — background thread that fires due schedules.

Lives inside the HQ web server process (started on boot) or runs standalone
via `python main.py schedule --daemon`. Ticks every `poll_sec`, collects due
schedules, and runs them sequentially through core/schedule_runner (which
holds the shared run lock from core/run_lock.py, so interactive runs and
scheduled runs never overlap).
"""

from __future__ import annotations

import threading
import time
from typing import Any


class SchedulerService:
    def __init__(self, store=None, poll_sec: float = 30.0):
        from core.scheduler import ScheduleStore

        self.store = store or ScheduleStore()
        self.poll_sec = poll_sec
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_tick: str = ""
        self.last_error: str = ""

    def run_once(self) -> list[dict[str, Any]]:
        """Fire all currently-due schedules. Returns their run records."""
        from core.schedule_runner import run_schedule

        records = []
        try:
            due = self.store.due()
            self.last_tick = time.strftime("%Y-%m-%dT%H:%M:%S")
            for sched in due:
                try:
                    records.append(run_schedule(self.store, sched.id, reason="due"))
                except Exception as e:
                    self.last_error = f"{sched.name}: {type(e).__name__}: {e}"
        except Exception as e:
            self.last_error = f"tick failed: {type(e).__name__}: {e}"
        return records

    def start(self) -> SchedulerService:
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="luckyd-scheduler", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_sec):
            self.run_once()


# Singleton for the web server / CLI to share.
_service: SchedulerService | None = None
_service_lock = threading.Lock()


def get_service(store=None) -> SchedulerService:
    global _service
    with _service_lock:
        if _service is None:
            _service = SchedulerService(store=store).start()
        return _service


def main() -> None:
    """Standalone daemon: `python -m core.schedule_daemon`."""
    import signal

    svc = SchedulerService(poll_sec=30.0).start()
    print("LuckyD scheduler daemon running (Ctrl+C to stop).")
    stop = threading.Event()

    def _sig(*_):
        stop.set()

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)
    while not stop.wait(5):
        pass
    svc.stop()
    print("Scheduler stopped.")


if __name__ == "__main__":
    main()
