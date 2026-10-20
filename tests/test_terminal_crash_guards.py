"""Terminal crash guards — session cap + output flood throttle.

Dylan (2026-10-20): the browser died while an agent CLI was coding in
the Agent Mesh / agent terminal, on any CLI. The shared infrastructure
was the problem, not the CLI: every pane hosts a full agent CLI process
tree on a ConPTY inside the browser process, sessions multiplied with
no limit, and one CLI printing at full speed streamed into the
renderer with no back-pressure.

These tests lock in the guards in browser_core/terminal_server.py:
  * at most _max_sessions() concurrent PTY sessions per server — extra
    panes are refused with a clear message and NO spawn;
  * _RateWindow caps sustained output per session (burst, then a
    rolling 1-second rate) so a flooding CLI waits instead of
    saturating the host process and the WebEngine renderer.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

for _mod in (
    "PySide6",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

import browser_core.terminal_server as ts
from browser_core.terminal_server import TERM_COOKIE, TerminalServer, _RateWindow

# ── _max_sessions ─────────────────────────────────────────────────────


def test_max_sessions_default(monkeypatch) -> None:
    monkeypatch.delenv("LUCKYD_TERMINAL_MAX_SESSIONS", raising=False)
    assert ts._max_sessions() == ts._MAX_SESSIONS_DEFAULT
    assert ts._max_sessions() >= 4  # a full mesh page must always fit


def test_max_sessions_env_override(monkeypatch) -> None:
    monkeypatch.setenv("LUCKYD_TERMINAL_MAX_SESSIONS", "9")
    assert ts._max_sessions() == 9


def test_max_sessions_garbage_env_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("LUCKYD_TERMINAL_MAX_SESSIONS", "lots")
    assert ts._max_sessions() == ts._MAX_SESSIONS_DEFAULT
    monkeypatch.setenv("LUCKYD_TERMINAL_MAX_SESSIONS", "0")
    assert ts._max_sessions() == 1  # clamped, never zero/negative


# ── _RateWindow ───────────────────────────────────────────────────────


def test_rate_window_allows_burst_then_waits() -> None:
    w = _RateWindow(rate=1000, burst=500)
    now = 100.0
    assert w.wait_for(1500, now) == 0.0  # exactly the allowance
    w.record(1500, now)
    # Window is full: the next byte must wait for the oldest to age out.
    wait = w.wait_for(1, now)
    assert 0.9 < wait <= 1.0


def test_rate_window_recovers_after_window_slides() -> None:
    w = _RateWindow(rate=1000, burst=500)
    w.record(1500, 100.0)
    assert w.wait_for(1, 100.0) > 0
    assert w.wait_for(1500, 101.1) == 0.0  # old bytes pruned after 1s


def test_rate_window_oversize_single_payload_never_deadlocks() -> None:
    w = _RateWindow(rate=100, burst=50)
    # A payload bigger than the whole allowance still goes (can't wait
    # forever on an empty window) — it is recorded and throttles what
    # follows instead.
    assert w.wait_for(10_000, 0.0) == 0.0
    w.record(10_000, 0.0)
    assert w.wait_for(1, 0.0) > 0


# ── _handle session cap ───────────────────────────────────────────────


class _FakeWS:
    def __init__(self, path="/?cols=80&rows=24&shell=cmd", cookie="tok"):
        self.request = SimpleNamespace(
            path=path,
            headers={"Cookie": f"{TERM_COOKIE}={cookie}"} if cookie else {},
        )
        self.sent: list = []
        self.closed: list = []

    def __iter__(self):
        return iter(())

    def send(self, msg):
        self.sent.append(msg)

    def close(self, **kw):
        self.closed.append(kw)


def test_handle_refuses_spawn_at_session_cap(monkeypatch) -> None:
    monkeypatch.setenv("LUCKYD_TERMINAL_MAX_SESSIONS", "2")
    srv = TerminalServer(token="tok")
    srv._clients.update({object(), object()})  # two live sessions already
    spawned: list = []
    monkeypatch.setattr(ts, "_spawn_pty", lambda *a, **k: spawned.append(1))
    ws = _FakeWS()
    srv._handle(ws)
    assert spawned == []  # the cap bites BEFORE any process is created
    assert any("terminal busy" in str(m) for m in ws.sent)
    assert ws.closed != []


def test_handle_under_cap_spawns_normally(monkeypatch) -> None:
    monkeypatch.setenv("LUCKYD_TERMINAL_MAX_SESSIONS", "2")
    srv = TerminalServer(token="tok")
    srv._clients.add(object())  # one live session — room for one more

    class _PTY:
        def isalive(self):
            return False

        def read(self, blocking=False):
            return ""

        def write(self, text):
            pass

        def set_size(self, cols, rows):
            pass

        def cancel_io(self):
            pass

    monkeypatch.setattr(ts, "_spawn_pty", lambda *a, **k: _PTY())
    ws = _FakeWS()
    srv._handle(ws)
    time.sleep(0.1)
    assert not any("terminal busy" in str(m) for m in ws.sent)
    assert os.environ["LUCKYD_TERMINAL_MAX_SESSIONS"] == "2"
