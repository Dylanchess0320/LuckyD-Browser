"""Terminal + LuckyD Code speed/stability (post-10.7 follow-on).

* ``mesh_shells_available`` is TTL-cached: rendering the terminal/mesh
  page used to re-probe ~24 agent CLIs on PATH every time.
* The PTY→WebSocket pump coalesces small writes into single frames and
  backs off its poll rate when idle instead of waking 100x/second.
* ``core.free_rotation`` probes must fail soft (return False) when the
  optional ``httpx`` dependency is missing — never crash the caller.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

import browser_core.terminal_server as ts
from browser_core.terminal_server import (
    TERM_COOKIE,
    TerminalServer,
    poll_delay_seconds,
)

# ── poll backoff policy ──────────────────────────────────────────────


def test_poll_delay_backs_off_and_caps() -> None:
    assert poll_delay_seconds(0) == pytest.approx(0.01)
    assert poll_delay_seconds(1) > poll_delay_seconds(0)
    assert poll_delay_seconds(2) > poll_delay_seconds(1)
    assert poll_delay_seconds(3) == poll_delay_seconds(1000) == pytest.approx(0.05)
    assert poll_delay_seconds(-5) == pytest.approx(0.01)


# ── mesh availability cache ──────────────────────────────────────────


def test_mesh_availability_is_cached_within_ttl(monkeypatch) -> None:
    probes = {"n": 0}

    def _probe(exe):
        probes["n"] += 1
        return f"/fake/{exe}"

    monkeypatch.setattr(ts, "_find_mesh_exe", _probe)
    monkeypatch.setattr(ts, "_find_wsl_exe", lambda: "/fake/wsl.exe")
    ts._reset_mesh_availability_cache()
    try:
        first = ts.mesh_shells_available()
        after_first = probes["n"]
        assert after_first == len(ts.MESH_SHELLS)
        second = ts.mesh_shells_available()
        assert probes["n"] == after_first  # no re-probe within the TTL
        assert second == first
        # Callers get a copy — mutating it must not poison the cache.
        second["mesh-cline"] = "tampered"
        assert ts.mesh_shells_available()["mesh-cline"] is True
    finally:
        ts._reset_mesh_availability_cache()


# ── pump coalescing ──────────────────────────────────────────────────


class _FakeWS:
    def __init__(self, messages=()):
        self.request = SimpleNamespace(
            path="/?cols=80&rows=24&shell=cmd",
            headers={"Cookie": f"{TERM_COOKIE}=tok"},
        )
        self._messages = messages
        self.sent = []
        self.closed = []

    def __iter__(self):
        return iter(self._messages)

    def send(self, msg):
        self.sent.append(msg)

    def close(self, **kw):
        self.closed.append(kw)


class _PumpPTY:
    def __init__(self, reads=()):
        self._reads = list(reads)
        self.written = []
        self.cancelled = False

    def isalive(self):
        return True

    def read(self, blocking=False):
        return self._reads.pop(0) if self._reads else ""

    def write(self, text):
        self.written.append(text)

    def set_size(self, cols, rows):
        pass

    def cancel_io(self):
        self.cancelled = True


def test_pump_coalesces_small_writes_into_one_frame(monkeypatch) -> None:
    pty = _PumpPTY(reads=["hello ", "world", "!"])
    ws = _FakeWS(messages=[])
    srv = TerminalServer(token="tok")
    monkeypatch.setattr(ts, "_spawn_pty", lambda *a, **k: pty)
    srv._handle(ws)
    time.sleep(0.3)  # let the daemon pump thread flush and finish
    assert "".join(ws.sent) == "hello world!"
    assert len(ws.sent) == 1  # three PTY reads, one WebSocket frame
    assert ws.closed != []


# ── free_rotation fails soft without httpx ───────────────────────────


def test_ollama_probe_returns_false_without_httpx(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "httpx", None)
    from core.free_rotation import _ollama_reachable

    assert _ollama_reachable() is False


def test_rotate_check_returns_false_without_httpx(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "httpx", None)
    from core.free_rotation import should_rotate_free_model

    assert should_rotate_free_model(RuntimeError("boom")) is False
