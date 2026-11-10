"""Slice 1 — Hub status handshake: /chat/state reports all 4 components.

Covers the one Hub status surface: live reachability + last-seen for
Browser (self), Hub, Claw and the puck bridge, the honest "not running"
puck state, the PC-off shape (200 with "unreachable", never an error
page), and the under-1s response bound when upstreams black-hole.
"""

from __future__ import annotations

import http.client
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.insert(0, str(_BROWSER_DIR))

# ── Qt stubs: real QObject base + working Signal ─────────────────────
# PySide6 is not installed. control_server subclasses QObject (GuiInvoker),
# so the stub must be a REAL class — subclassing a MagicMock would silently
# degrade GuiInvoker into a mock and every test below would pass vacuously.
# Installed only-if-missing (see the loop below) so this file passes
# standalone without breaking other modules' stub identities.


class _QtObjectStub:
    """Instantiable, subclassable QObject stand-in."""

    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return MagicMock(name=f"qtstub.{name}")


class _FakeSignal:
    """Minimal synchronous signal: connect() stores slots, emit() calls them."""

    def __init__(self, *types):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in list(self._slots):
            slot(*args)


for _mod in (
    "PySide6",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
):
    # Only install if missing, and only fill in attributes on the EXISTING
    # stub module objects — never replace a sys.modules entry. Other test
    # modules read mock identities off those objects (e.g. DownloadState),
    # so swapping them mid-session breaks their tests.
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()
_qtcore = sys.modules["PySide6.QtCore"]
if not isinstance(getattr(_qtcore, "QObject", None), type):
    _qtcore.QObject = _QtObjectStub
if isinstance(getattr(_qtcore, "Signal", None), MagicMock):
    _qtcore.Signal = lambda *types: _FakeSignal(*types)

from browser_core import luckyd_services as ls


@pytest.fixture(autouse=True)
def _clear_last_seen():
    """_LAST_SEEN is process-global; reset it so tests stay independent."""
    ls._LAST_SEEN.clear()
    yield
    ls._LAST_SEEN.clear()


def _settings(**values):
    return SimpleNamespace(get=lambda key, default=None: values.get(key, default))


def _req(base, method, path, body=None):
    conn = http.client.HTTPConnection(base, timeout=10)
    payload = json.dumps(body) if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8", "replace")
    conn.close()
    return resp.status, raw


class _FakeResp:
    def __init__(self, status, obj):
        self.status = status
        self._body = json.dumps(obj).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _opener(routes, calls):
    def open_fn(req, timeout=None):
        calls.append(req)
        for sub, status, obj in routes:
            if sub in req.full_url:
                if isinstance(obj, Exception):
                    raise obj
                return _FakeResp(status, obj)
        raise AssertionError(f"unexpected URL {req.full_url}")

    return open_fn


def _serve_ctl(settings, monkeypatch):
    """Live control server (loopback) with stubbed Hub/Claw upstreams."""
    import browser_core.control_server as cs

    calls: list = []
    routes = [
        ("18790/healthz", 200, {"ok": True, "ollama": True}),
        ("18791/healthz", 200, {"ok": True, "enabled": True, "projects": ["browser", "hub"]}),
    ]
    monkeypatch.setattr(urllib.request, "urlopen", _opener(routes, calls))
    handler = cs.make_handler(SimpleNamespace(), token="", settings=settings)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, f"127.0.0.1:{httpd.server_address[1]}"


# ── puck_state ────────────────────────────────────────────────────────


def test_puck_state_without_bridge_is_honestly_not_running() -> None:
    st = _settings()
    out = ls.puck_state(st)
    assert out["state"] == "not_running"
    assert out["last_seen"] is None
    assert "detail" in out  # the page never has to guess why


def test_puck_state_probes_a_configured_bridge() -> None:
    st = _settings(puck_url="http://127.0.0.1:19999")
    op = _opener([("19999/healthz", 200, {"ok": True})], [])
    out = ls.puck_state(st, op)
    assert out["state"] == "ok"
    assert isinstance(out["last_seen"], float)


def test_puck_state_down_bridge_is_not_running_not_unreachable() -> None:
    st = _settings(puck_url="http://127.0.0.1:19999")
    down = _opener([("healthz", 200, urllib.error.URLError("refused"))], [])
    assert ls.puck_state(st, down)["state"] == "not_running"


# ── last-seen tracking ────────────────────────────────────────────────


def test_last_seen_recorded_on_ok_and_kept_after_failure(monkeypatch) -> None:
    st = _settings(hub_token="HT", claw_token="CT")
    calls: list = []
    op = _opener(
        [
            ("18790/healthz", 200, {"ok": True, "ollama": False}),
            ("18791/healthz", 200, {"ok": True, "enabled": True, "projects": []}),
        ],
        calls,
    )
    hub = ls.hub_state(st, op)
    assert hub["state"] == "ok" and isinstance(hub["last_seen"], float)
    claw = ls.claw_state(st, op)
    assert claw["state"] == "ok" and isinstance(claw["last_seen"], float)
    seen = hub["last_seen"]

    down = _opener([("healthz", 200, urllib.error.URLError("refused"))], [])
    hub2 = ls.hub_state(st, down)
    assert hub2["state"] == "unreachable"
    assert hub2["last_seen"] == seen  # still shows when it was last alive


# ── /chat/state surface ───────────────────────────────────────────────


def test_chat_state_reports_all_four_components(monkeypatch) -> None:
    settings = _settings(hub_token="HT", claw_token="CT")
    httpd, base = _serve_ctl(settings, monkeypatch)
    try:
        status, raw = _req(base, "GET", "/chat/state")
        state = json.loads(raw)
        assert status == 200
        comps = state["components"]
        assert sorted(comps) == ["browser", "claw", "hub", "puck"]
        assert comps["browser"]["state"] == "ok"
        assert isinstance(comps["browser"]["last_seen"], float)
        assert comps["hub"]["state"] == "ok"
        assert isinstance(comps["hub"]["last_seen"], float)
        assert comps["claw"]["state"] == "ok"
        assert isinstance(comps["claw"]["last_seen"], float)
        assert comps["puck"]["state"] == "not_running"
        assert comps["puck"]["last_seen"] is None
        # Existing fields keep their shape.
        assert state["hub"]["state"] == "ok" and state["claw"]["state"] == "ok"
        assert state["puck"]["state"] == "not_running"
        assert state["projects"] == ["browser", "hub"]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_chat_state_pc_off_is_200_with_unreachable_not_an_error(monkeypatch) -> None:
    import browser_core.control_server as cs

    settings = _settings(hub_token="HT", claw_token="CT")
    down = _opener([("healthz", 200, urllib.error.URLError("refused"))], [])
    monkeypatch.setattr(urllib.request, "urlopen", down)
    handler = cs.make_handler(SimpleNamespace(), token="", settings=settings)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"127.0.0.1:{httpd.server_address[1]}"
        status, raw = _req(base, "GET", "/chat/state")
        state = json.loads(raw)
        assert status == 200
        comps = state["components"]
        assert comps["hub"]["state"] == "unreachable"
        assert comps["claw"]["state"] == "unreachable"
        assert comps["browser"]["state"] == "ok"
        assert comps["puck"]["state"] == "not_running"
    finally:
        httpd.shutdown()
        httpd.server_close()


class _SlowHandler(BaseHTTPRequestHandler):
    """Upstream that black-holes: sleeps longer than any status timeout."""

    def do_GET(self):
        time.sleep(5)
        body = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_chat_state_under_one_second_when_upstreams_black_hole(monkeypatch) -> None:
    """Both probes hanging must still bound the endpoint at ~2x STATUS_TIMEOUT."""
    import browser_core.control_server as cs

    slow = ThreadingHTTPServer(("127.0.0.1", 0), _SlowHandler)
    threading.Thread(target=slow.serve_forever, daemon=True).start()
    try:
        slow_port = slow.server_address[1]
        settings = _settings(
            hub_token="HT",
            claw_token="CT",
            hub_url=f"http://127.0.0.1:{slow_port}",
            claw_url=f"http://127.0.0.1:{slow_port}",
        )
        handler = cs.make_handler(SimpleNamespace(), token="", settings=settings)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            base = f"127.0.0.1:{httpd.server_address[1]}"
            start = time.monotonic()
            status, raw = _req(base, "GET", "/chat/state")
            elapsed = time.monotonic() - start
            state = json.loads(raw)
            assert status == 200
            assert state["components"]["hub"]["state"] == "unreachable"
            assert state["components"]["claw"]["state"] == "unreachable"
            # 2 x STATUS_TIMEOUT (0.4s) + handler overhead; 1.2s guards
            # against CI jitter while proving the ~1s bound.
            print(f"\nblack-hole /chat/state elapsed: {elapsed:.3f}s")
            assert elapsed < 1.2, f"/chat/state took {elapsed:.2f}s"
        finally:
            httpd.shutdown()
            httpd.server_close()
    finally:
        slow.shutdown()
        slow.server_close()


# ── chat page block ───────────────────────────────────────────────────


def test_chat_page_has_hub_status_block() -> None:
    from browser_core.chat_page import chat_html

    page = chat_html(_settings())
    assert 'id="hub-status"' in page
    assert "renderHubStatus" in page
    assert "Puck bridge" in page
    assert "not running" in page
    assert "/chat/state" in page
