"""Swarm regression tests — browser/browser_core bug fixes (overnight hardening).

Bug 1 — element index 0 silently dropped (the ``int(x or -1)`` falsy bug).
  The snapshot JS tags elements starting at 0, but three spots treated 0 as
  "no index given":
    * workflows.step_record: clicks/types on the first element of a page
      were never recorded into a workflow (step silently skipped).
    * control_server.QtBrowserBackend.act: the recording guard ``index >= 0``
      failed, so index-0 actions were never fingerprinted/recorded.
    * control_server.QtBrowserBackend._perform_act: index 0 became -1, so
      POST /act {"action": "click", "index": 0} answered
      "click needs an element index" — the first element on every page was
      undrivable via the Control API.

Bug 2 — workflow fingerprints never captured (broken str.format template).
  _FINGERPRINT_JS is formatted with .format(i=...) but its literal JS braces
  were never doubled, so fingerprint_js() always raised ValueError. The
  call site in QtBrowserBackend.act() suppresses exceptions, so every
  recorded workflow silently lost its element targets and self-healing
  replay degraded to the raw recorded index every time.

Bug 3 — NetMonitor restart resurrects a stale capture thread.
  NetMonitor.start() calls stop() (bounded 2s join), clears the stop event,
  and spawns a fresh thread. A previous _run still blocked in its 3s CDP
  connect could outlive the join, wake up after the event was cleared, and
  run a second capture session forever — duplicate websocket, clobbered
  target/error. A generation counter now lets stale runs recognize they were
  superseded and exit.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# browser_core modules that need Qt import headless: stub PySide6 first.
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

from browser_core.workflows import WorkflowRecorder, action_index, step_record

# ── Bug 1: action_index ───────────────────────────────────────────────


def test_action_index_zero_is_valid():
    assert action_index({"index": 0}) == 0


def test_action_index_missing_or_blank_is_minus_one():
    assert action_index({}) == -1
    assert action_index({"index": None}) == -1
    assert action_index({"index": ""}) == -1


def test_action_index_other_values():
    assert action_index({"index": 5}) == 5
    assert action_index({"index": "3"}) == 3
    assert action_index({"index": "0"}) == 0
    assert action_index({"index": -2}) == -2


# ── Bug 1: workflows.step_record ─────────────────────────────────────


def test_step_record_keeps_index_zero():
    step = step_record({"action": "click", "index": 0}, {"tag": "button", "text": "Go"})
    assert step is not None
    assert step["action"] == "click"
    assert step["index"] == 0
    assert step["target"]["tag"] == "button"


def test_step_record_type_index_zero():
    step = step_record({"action": "type", "index": 0, "text": "hello"})
    assert step is not None
    assert step["index"] == 0
    assert step["text"] == "hello"


def test_step_record_skips_blank_index():
    assert step_record({"action": "click"}, {"tag": "button"}) is None
    assert step_record({"action": "click", "index": None}, {"tag": "button"}) is None
    assert step_record({"action": "click", "index": ""}, {"tag": "button"}) is None


def test_step_record_skips_negative_index():
    assert step_record({"action": "click", "index": -1}, {"tag": "button"}) is None


# ── Bug 1: control_server act() / _perform_act() ─────────────────────


def _backend():
    """QtBrowserBackend without Qt: __new__ + the attributes act() touches."""
    from browser_core.control_server import QtBrowserBackend

    backend = QtBrowserBackend.__new__(QtBrowserBackend)
    backend._recorder = WorkflowRecorder()
    backend._replaying = False
    return backend


def test_act_records_index_zero():
    backend = _backend()
    backend._recorder.start("t")
    backend.run_js = lambda js, timeout=10.0: '{"tag": "button", "text": "Go"}'
    backend._perform_act = lambda action: "clicked <button>"
    backend.act({"action": "click", "index": 0})
    name, steps = backend._recorder.stop()
    assert name == "t"
    assert len(steps) == 1, f"index-0 click was not recorded: {steps}"
    assert steps[0]["index"] == 0
    assert steps[0]["target"]["tag"] == "button"


def test_perform_act_click_index_zero():
    backend = _backend()
    backend.run_js = lambda js, timeout=10.0: (
        "clicked <button>" if "data-ld-agent" in js else "http://x/|complete"
    )
    backend._wait_settled = lambda *args, **kwargs: None
    result = backend._perform_act({"action": "click", "index": 0})
    assert "element index" not in result, f"index 0 rejected: {result}"
    assert "clicked" in result


def test_perform_act_type_index_zero():
    backend = _backend()
    backend.run_js = lambda js, timeout=10.0: (
        "typed 5 chars" if "data-ld-agent" in js else "http://x/|complete"
    )
    backend._wait_settled = lambda *args, **kwargs: None
    result = backend._perform_act({"action": "type", "index": 0, "text": "hello"})
    assert "element index" not in result, f"index 0 rejected: {result}"
    assert "typed" in result


def test_perform_act_still_rejects_missing_index():
    backend = _backend()
    backend.run_js = lambda js, timeout=10.0: "x"
    backend._wait_settled = lambda *args, **kwargs: None
    assert "element index" in backend._perform_act({"action": "click"})
    assert "element index" in backend._perform_act({"action": "click", "index": -1})


# ── Bug 2: fingerprint_js str.format ─────────────────────────────────


def test_fingerprint_js_formats_without_raising():
    from browser_core.workflows import fingerprint_js

    js = fingerprint_js(3)
    assert '[data-ld-agent="3"]' in js
    assert "{i}" not in js  # no unformatted placeholder left behind


def test_fingerprint_js_is_valid_javascript(tmp_path):
    """The formatted payload must actually parse as JS (node --check)."""
    import shutil
    import subprocess

    from browser_core.workflows import fingerprint_js

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    path = tmp_path / "fp.js"
    path.write_text(fingerprint_js(7), encoding="utf-8")
    proc = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr


# ── Bug 3: NetMonitor stale thread on rapid restart ──────────────────


def test_netmon_restart_abandons_stale_thread(monkeypatch):
    """A _run stuck in its CDP connect when start() is called again must exit
    without connecting once the new generation is live (no duplicate
    capture session)."""
    from browser_core import netmon

    entered = threading.Event()
    release = threading.Event()
    connect_calls: list[str] = []

    class _Resp:
        def json(self):
            return [
                {
                    "type": "page",
                    "url": "http://example/",
                    "webSocketDebuggerUrl": "ws://127.0.0.1:9222/x",
                }
            ]

    def fake_get(*args, **kwargs):
        entered.set()
        assert release.wait(10), "test hung waiting for release"
        return _Resp()

    class _FakeWS:
        def send(self, data):
            pass

        def recv(self, timeout=None):
            raise TimeoutError  # idle capture loop

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_connect(url, open_timeout=None):
        connect_calls.append(url)
        return _FakeWS()

    monkeypatch.setattr(netmon.httpx, "get", fake_get)
    monkeypatch.setattr("websockets.sync.client.connect", fake_connect)

    mon = netmon.NetMonitor()
    try:
        mon.start("a")
        assert entered.wait(5), "first capture thread never reached CDP"
        stale = mon._thread
        entered.clear()
        mon.start("b")  # stop()'s join times out; stale thread still in fake_get
        release.set()  # both threads wake; the stale one must stay dead
        deadline = time.monotonic() + 5
        while stale.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not stale.is_alive(), "stale capture thread resurrected after restart"
        time.sleep(0.3)  # let the fresh thread settle into its capture loop
        assert len(connect_calls) == 1, f"stale thread connected: {connect_calls}"
        assert mon.target == "http://example/"
        assert mon.running
    finally:
        release.set()
        mon.stop()
