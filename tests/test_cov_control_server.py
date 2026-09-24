"""Coverage push for browser_core/control_server.py — helpers, GuiInvoker, QtBrowserBackend.

Earlier suites covered the HTTP auth model, host/origin defenses, the main
route table, the /hq gateway, /status enrichment, research-swarm routes and
the BrowserControlServer lifecycle (test_night1_control.py,
test_night4_control_routes.py). This file covers the rest:

- handler helpers at unit level: _cookie_token / _host_ok / _origin_ok edge
  cases, the OSError-swallow paths in _send/_send_html/_redirect/
  _send_download, _body's size caps and malformed-body paths, _fail's repr
  fallback, log_message, _status with a raising harness/ai_info;
- the remaining GET routes (/, /mesh, /terminal?shell=, /static/terminal/
  success + mime mapping, /network, /research, /research/status|runs|run
  against the real swarm manager), the do_GET 500 path, POST origin
  rejection, and the /hq error-splash path;
- GuiInvoker as a REAL QObject subclass (PySide6.QtCore ships a real stub
  QObject and a working Signal factory in this file, so the classes under
  test are real, not mocks): _execute success/error, run()
  success/timeout/error propagation;
- QtBrowserBackend driven against a hand-written fake Qt app/window/tabs/
  page: _window/_view errors, run_js value + timeout paths, _wait_settled
  change/timeout paths, _normalize_url, navigate/new_tab/activate_tab/
  close_tab, snapshot ok/empty/parse-error, act/_perform_act for every
  action kind, recording behavior, replay_workflow (healed/failed/exception
  steps, early break, _replaying reset), _wait_ready, extract's snapshot
  failure, set_theme's missing browser_ui.theme, screenshot's unreachable
  CDP, netmon_* lazy + live paths, schedule_list/schedule_set.

No network beyond loopback, no sleeps over a second.
"""

from __future__ import annotations

import http.client
import io
import json
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# ── Qt stubs: real QObject base + working Signal ─────────────────────
# PySide6 is not installed. control_server subclasses QObject (GuiInvoker),
# so the stub must be a REAL class — subclassing a MagicMock would silently
# degrade GuiInvoker into a mock and every test below would pass vacuously.


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
    # Installed unconditionally: this file needs the real QObject/Signal
    # regardless of which test module ran first in the session.
    sys.modules[_mod] = MagicMock()
sys.modules["PySide6.QtCore"].QObject = _QtObjectStub


def _signal_factory(*types):
    return _FakeSignal(*types)


sys.modules["PySide6.QtCore"].Signal = _signal_factory

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

import browser_core.control_server as cs
from browser_core.control_server import (
    _CLICK_JS,
    _HIGHLIGHT_JS,
    _MAX_ELS,
    _MAX_TEXT,
    _SELECT_JS,
    _SETTLE_JS,
    _SNAPSHOT_JS,
    _TYPE_JS,
    API_NAME,
    API_VERSION,
    CTL_COOKIE,
    BrowserControlServer,
    QtBrowserBackend,
    _export_env_to_os,
    _QuietHTTPServer,
    make_handler,
)
from browser_core.scheduler import INTERVALS, ScheduleStore
from browser_core.workflows import WorkflowStore, elements_js, fingerprint_js

TOKEN = "cov-control-token"

_SNAPSHOT_FULL = _SNAPSHOT_JS.replace("__MAXELS__", str(_MAX_ELS)).replace(
    "__MAXTEXT__", str(_MAX_TEXT)
)


# ── hand-written fake Qt app ─────────────────────────────────────────


class _FakeQUrl:
    def __init__(self, spec):
        self.spec = spec


class _ScriptedPage:
    """Stand-in for QWebEnginePage: runJavaScript dispatches scripted answers."""

    def __init__(self):
        self.calls = []
        self._settle_n = 0
        self._settle_fixed = None
        self._raise_on = ()
        self._never_cb = False
        self._ready_state = "complete"
        self._click_results = {}
        self._snapshot_raw = None  # when set, snapshot JS answers with this verbatim
        self._snapshot_doc = {
            "url": "https://example.com/",
            "title": "Example",
            "elements": [],
            "text": "hello",
        }
        self._candidates = []

    def runJavaScript(self, js, cb):  # noqa: N802
        self.calls.append(js)
        if self._never_cb:
            return
        if any(sub in js for sub in self._raise_on):
            raise TimeoutError("page JavaScript timed out")
        if js == "document.readyState":
            cb(self._ready_state)
        elif js == _SNAPSHOT_FULL:
            cb(
                self._snapshot_raw
                if self._snapshot_raw is not None
                else json.dumps(self._snapshot_doc)
            )
        elif js == elements_js():
            cb(json.dumps(self._candidates))
        elif js == _SETTLE_JS:
            if self._settle_fixed is not None:
                cb(self._settle_fixed)
            else:
                self._settle_n += 1
                cb(f"state-{self._settle_n}")
        elif js == fingerprint_js(0):
            cb(json.dumps({"tag": "button", "text": "Go"}))
        else:
            for i in range(64):
                if js == _CLICK_JS.format(i=i):
                    cb(self._click_results.get(i, "clicked"))
                    return
            cb("ok")


class _FakeView:
    def __init__(self, url="https://example.com/", page=None):
        self._url = SimpleNamespace(toString=lambda: url)
        self._page = page or _ScriptedPage()

    def url(self):
        return self._url

    def page(self):
        return self._page


class _FakeTabs:
    def __init__(self, views):
        self._views = list(views)
        self._current = 0
        self.closed = []

    def count(self):
        return len(self._views)

    def current_view(self):
        return self._views[self._current] if self._views else None

    def currentIndex(self):  # noqa: N802
        return self._current

    def setCurrentIndex(self, i):  # noqa: N802
        self._current = i

    def widget(self, i):
        return self._views[i]

    def tabText(self, i):  # noqa: N802
        return f"Tab {i}"

    def close_tab(self, i):
        self.closed.append(i)
        del self._views[i]
        self._current = min(self._current, len(self._views) - 1)


class _FakeWindow:
    def __init__(self, views):
        self.tabs = _FakeTabs(views)
        self.loaded = []
        self.new_tabs = []
        self.themed = 0

    def load_in_current_tab(self, qurl):
        self.loaded.append(qurl)

    def open_in_new_tab(self, qurl):
        self.new_tabs.append(qurl)
        view = _FakeView()
        self.tabs._views.append(view)
        return view

    def new_tab(self, qurl):
        self.new_tabs.append(qurl)

    def _apply_theme(self):
        self.themed += 1


class _FakeSettings:
    def __init__(self):
        self.set_calls = []

    def set(self, key, value):
        self.set_calls.append((key, value))


class _FakeApp:
    def __init__(self, windows):
        self.windows = list(windows)
        self._active = self.windows[0] if self.windows else None
        self.qapp = SimpleNamespace(activeWindow=lambda: self._active)
        self.settings = _FakeSettings()
        self.adblock = SimpleNamespace(blocked_count=7)


def _qt_backend(url="https://example.com/", monkeypatch=None):
    """Real QtBrowserBackend driving the fake Qt app; returns (be, app, win, view, page)."""
    if monkeypatch is not None:
        monkeypatch.setattr(sys.modules["PySide6.QtCore"], "QUrl", _FakeQUrl)
    page = _ScriptedPage()
    view = _FakeView(url, page)
    win = _FakeWindow([view])
    app = _FakeApp([win])
    be = QtBrowserBackend(app)
    # Fresh signal per backend: the class-level one accumulates slots across
    # instances, which would run each payload fn multiple times.
    be._invoker.invoke = _FakeSignal()
    be._invoker.invoke.connect(be._invoker._execute)
    return be, app, win, view, page


# ── hand-written fake backend for the HTTP routing layer ─────────────


class _HttpBackend:
    """Full make_handler protocol — records calls, scripted answers."""

    def __init__(self):
        self.calls = []
        self.ai_info_error = False

    def _rec(self, name, *args):
        self.calls.append((name, *args))

    def status(self):
        return {"ok": True}

    def ai_info(self):
        if self.ai_info_error:
            raise RuntimeError("ai exploded")
        return {"provider": "kimi"}

    def tabs(self):
        return [{"index": 0, "url": "https://a.com", "title": "A", "active": True}]

    def navigate(self, url, new_tab):
        self._rec("navigate", url, new_tab)
        return url

    def new_tab(self, url):
        return 3

    def activate_tab(self, index):
        return index

    def close_tab(self, index):
        return True

    def snapshot(self):
        return {"url": "https://a.com"}

    def act(self, body):
        return "acted"

    def eval_js(self, js):
        return {"echo": js}

    def screenshot(self, url):
        return "aGVsbG8="

    def ask(self, question, provider):
        return "answer!"

    def list_workflows(self):
        return {"workflows": ["w1"]}

    def start_recording(self, name):
        return name

    def stop_recording(self):
        return {"name": "w1", "steps": 2}

    def replay_workflow(self, name):
        return {"replayed": name}

    def delete_workflow(self, name):
        return True

    def schedule_list(self):
        return {"schedules": [], "intervals": {"0": "Off"}}

    def schedule_set(self, name, every_min):
        return {"name": name, "every_min": every_min}

    def extract(self, instruction, schema, provider):
        return {"field": "value"}

    def set_theme(self, name):
        return name

    def netmon_start(self, url):
        return {"started": True, "target": url}

    def netmon_stop(self):
        return {"stopped": True}

    def netmon_clear(self):
        return {"cleared": True}

    def netmon_events(self, since):
        return {"rows": [], "seq": since}

    def netmon_har(self):
        return {"log": {"entries": []}}


class _Harness:
    def __init__(self, up=False, starting=False, error=None, autostart=True):
        self._up = up
        self._starting = starting
        self._error = error
        self._autostart = autostart
        self.ensure_calls = []
        self.url = "http://127.0.0.1:8000"

    def probe(self):
        return {"up": self._up}

    def status(self):
        return {
            "up": self._up,
            "starting": self._starting,
            "error": self._error,
            "url": self.url,
        }

    def ensure_started(self, force=False):
        self.ensure_calls.append(force)
        if self._autostart:
            self._starting = True
            self._error = None


@pytest.fixture()
def server():
    handler = make_handler(_HttpBackend(), token=TOKEN)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield port
    srv.shutdown()
    t.join(timeout=5)


def _serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, t, srv.server_address[1]


def _req(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    hdrs = dict(headers or {})
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    conn.request(method, path, body=payload, headers=hdrs)
    resp = conn.getresponse()
    data = resp.read()
    out = (resp.status, resp.getheader("Content-Type", ""), data)
    conn.close()
    return out


def _authz(extra=None):
    h = {"Authorization": f"Bearer {TOKEN}"}
    if extra:
        h.update(extra)
    return h


def _handler(token=TOKEN, backend=None, harness=None, settings=None):
    """Bare handler instance (no socket) for unit-testing helpers."""
    cls = make_handler(backend or _HttpBackend(), token=token, harness=harness, settings=settings)
    h = cls.__new__(cls)
    h.headers = {}
    h.rfile = io.BytesIO()
    h.wfile = io.BytesIO()
    return h


class _Raising:
    def __init__(self, exc):
        self._exc = exc

    def __call__(self, *args, **kwargs):
        raise self._exc


# ── _cookie_token ────────────────────────────────────────────────────


def test_cookie_token_missing_is_empty():
    assert _handler()._cookie_token() == ""


def test_cookie_token_picks_ours_among_many():
    h = _handler()
    h.headers = {"Cookie": "a=1; luckyd_ctl=tok123; b=2"}
    assert h._cookie_token() == "tok123"


def test_cookie_token_strips_quotes():
    h = _handler()
    h.headers = {"Cookie": f'{CTL_COOKIE}="quoted-token"'}
    assert h._cookie_token() == "quoted-token"


def test_cookie_token_exception_yields_empty():
    h = _handler()
    h.headers = SimpleNamespace(get=_Raising(RuntimeError("weird headers")))
    assert h._cookie_token() == ""


# ── _host_ok / _origin_ok ────────────────────────────────────────────


@pytest.mark.parametrize(
    "host",
    [
        "",
        "localhost",
        "LOCALHOST",
        "localhost:9777",
        "127.0.0.1",
        "127.0.0.1:9777",
        "[::1]",
        "[::1]:9777",
    ],
)
def test_host_ok_loopback_variants(host):
    h = _handler()
    h.headers = {"Host": host}
    assert h._host_ok() is True


@pytest.mark.parametrize(
    "host", ["evil.example.com", "127.0.0.2", "[::2]", "localhost.evil.com", "::1"]
)
def test_host_ok_rejects_non_loopback(host):
    # NOTE: bare "::1" is rejected too — "…".split(":")[0] yields "", even
    # though the code comment claims "::1 (bracketed or not)" is allowed.
    h = _handler()
    h.headers = {"Host": host}
    assert h._host_ok() is False


def test_origin_ok_missing_or_blank():
    h = _handler()
    h.headers = {}
    assert h._origin_ok() is True
    h.headers = {"Origin": ""}
    assert h._origin_ok() is True


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_origin_ok_same_origin(scheme):
    h = _handler()
    h.headers = {"Host": "127.0.0.1:9777", "Origin": f"{scheme}://127.0.0.1:9777"}
    assert h._origin_ok() is True


def test_origin_ok_mismatch():
    h = _handler()
    h.headers = {"Host": "127.0.0.1:9777", "Origin": "http://evil.example.com"}
    assert h._origin_ok() is False


# ── _send / _send_html / _redirect / _send_download failure paths ────


def _no_op_handler(h):
    """Stub the stdlib send_* plumbing on a socket-less handler instance."""
    h.send_response = lambda code: None
    h.send_header = lambda k, v: None
    h.end_headers = lambda: None
    return h


def test_send_swallows_broken_pipe():
    h = _handler()
    h.send_response = _Raising(OSError("gone"))
    h._send(200, {"ok": True})  # must not raise


def test_send_swallows_write_error():
    h = _no_op_handler(_handler())
    h.wfile = SimpleNamespace(write=_Raising(ConnectionError("reset")))
    h._send(200, {"ok": True})  # must not raise


def test_send_html_swallows_write_error():
    h = _no_op_handler(_handler())
    h.wfile = SimpleNamespace(write=_Raising(OSError("gone")))
    h._send_html("<html></html>")  # must not raise


def test_redirect_sends_302_with_location():
    h = _handler()
    calls = []
    h.send_response = lambda code: calls.append(("response", code))
    h.send_header = lambda k, v: calls.append(("header", k, v))
    h.end_headers = lambda: calls.append(("end",))
    h._redirect("http://127.0.0.1:8000/ui")
    assert ("response", 302) in calls
    assert ("header", "Location", "http://127.0.0.1:8000/ui") in calls
    assert ("end",) in calls


def test_redirect_swallows_broken_pipe():
    h = _handler()
    h.send_response = _Raising(ConnectionError("reset"))
    h._redirect("http://x/")  # must not raise


def test_send_download_swallows_write_error():
    h = _no_op_handler(_handler())
    h.wfile = SimpleNamespace(write=_Raising(OSError("gone")))
    h._send_download("data", "f.har", "application/json")  # must not raise


def test_send_file_missing_asset_is_404(server):
    status, _, data = _req(server, "GET", "/static/terminal/does-not-exist.js", headers=_authz())
    assert status == 404
    assert json.loads(data)["error"] == "asset not found"


# ── _body ────────────────────────────────────────────────────────────


def test_body_bad_content_length_is_empty():
    h = _handler()
    h.headers = {"Content-Length": "not-a-number"}
    assert h._body() == {}


def test_body_zero_length_is_empty():
    h = _handler()
    h.headers = {"Content-Length": "0"}
    assert h._body() == {}


def test_body_over_cap_is_not_read():
    h = _handler()
    h.headers = {"Content-Length": str(2**20 + 1)}
    h.rfile = SimpleNamespace(read=_Raising(AssertionError("must not read")))
    assert h._body() == {}


def test_body_short_read_over_cap_is_dropped():
    """Content-Length lies small but the stream yields >1MB → dropped."""

    class _Liar:
        def read(self, n):
            return b"x" * (2**20 + 1)

    h = _handler()
    h.headers = {"Content-Length": "10"}
    h.rfile = _Liar()
    assert h._body() == {}


def test_body_non_dict_json_is_empty():
    h = _handler()
    h.headers = {"Content-Length": "5"}
    h.rfile = io.BytesIO(b"[1,2]")
    assert h._body() == {}


def test_body_malformed_json_is_empty():
    h = _handler()
    h.headers = {"Content-Length": "5"}
    h.rfile = io.BytesIO(b"{oops")
    assert h._body() == {}


def test_body_valid_dict_parsed():
    h = _handler()
    h.headers = {"Content-Length": "8"}
    h.rfile = io.BytesIO(b'{"a": 1}')
    assert h._body() == {"a": 1}


# ── _fail / log_message / _status ────────────────────────────────────


def test_fail_uses_repr_when_str_is_empty():
    h = _no_op_handler(_handler())
    h._fail(500, Exception())
    body = json.loads(h.wfile.getvalue().decode())
    assert body == {"ok": False, "error": "Exception()"}


def test_log_message_is_quiet():
    assert _handler().log_message('"%s" %s %s') is None


def test_status_merges_harness_and_ai():
    be = _HttpBackend()
    h = _handler(backend=be, harness=_Harness(up=True))
    info = h._status()
    assert info["harness"] is True
    assert info["provider"] == "kimi"


def test_status_suppresses_harness_and_ai_errors():
    be = _HttpBackend()
    be.ai_info_error = True

    class _BadHarness:
        def status(self):
            raise RuntimeError("down")

    h = _handler(backend=be, harness=_BadHarness())
    info = h._status()
    assert info["ok"] is True
    assert "harness" not in info
    assert "provider" not in info


# ── remaining GET routes ─────────────────────────────────────────────


def test_get_root_lists_routes(server):
    status, ctype, data = _req(server, "GET", "/", headers=_authz())
    assert status == 200
    assert "application/json" in ctype
    body = json.loads(data)
    assert body["ok"] is True
    assert body["name"] == API_NAME
    assert body["version"] == API_VERSION
    assert any(r["route"] == "GET  /tabs" for r in body["routes"])


def test_get_mesh_is_html(server):
    status, ctype, data = _req(server, "GET", "/mesh", headers=_authz())
    assert status == 200
    assert "text/html" in ctype
    assert b"<html" in data.lower()


def test_get_terminal_falls_back_for_unknown_shell(server):
    status, ctype, data = _req(server, "GET", "/terminal?shell=fish", headers=_authz())
    assert status == 200
    assert "text/html" in ctype
    assert b"agent" in data  # unknown profile → default agent shell


def test_get_terminal_honors_known_shell(server):
    status, _, data = _req(server, "GET", "/terminal?shell=powershell", headers=_authz())
    assert status == 200
    assert b"powershell" in data


def test_get_terminal_defaults_shell(server):
    status, _, _ = _req(server, "GET", "/terminal", headers=_authz())
    assert status == 200


def test_terminal_asset_js_mime(server):
    status, ctype, data = _req(server, "GET", "/static/terminal/xterm.js", headers=_authz())
    assert status == 200
    assert "application/javascript" in ctype
    assert len(data) > 1000


def test_terminal_asset_css_mime(server):
    status, ctype, _ = _req(server, "GET", "/static/terminal/xterm.css", headers=_authz())
    assert status == 200
    assert "text/css" in ctype


def test_get_network_page_is_html(server):
    status, ctype, data = _req(server, "GET", "/network", headers=_authz())
    assert status == 200
    assert "text/html" in ctype
    assert b"<html" in data.lower()


def test_get_workflows_page_is_html(server):
    status, ctype, _ = _req(server, "GET", "/workflows", headers=_authz())
    assert status == 200
    assert "text/html" in ctype


def test_get_research_page_is_html(server):
    status, ctype, _ = _req(server, "GET", "/research", headers=_authz())
    assert status == 200
    assert "text/html" in ctype


def test_research_status_real_manager_idle(server):
    status, _, data = _req(server, "GET", "/research/status", headers=_authz())
    assert status == 200
    body = json.loads(data)
    assert body["ok"] is True
    assert body["status"] == "idle"
    assert body["active"] is False


def test_research_runs_real_manager(server):
    status, _, data = _req(server, "GET", "/research/runs", headers=_authz())
    assert status == 200
    assert isinstance(json.loads(data)["runs"], list)


def test_research_run_real_manager_not_found(server):
    status, _, data = _req(server, "GET", "/research/run?id=does-not-exist", headers=_authz())
    assert status == 200
    body = json.loads(data)
    assert body["ok"] is False
    assert "not found" in body["error"]


def test_research_run_rejects_traversal(server):
    status, _, data = _req(server, "GET", "/research/run?id=../../secret", headers=_authz())
    assert status == 200
    assert json.loads(data)["ok"] is False


def test_get_backend_error_is_500():
    class _Bad:
        def tabs(self):
            raise RuntimeError("tabs exploded")

    handler = make_handler(_Bad(), token=TOKEN)
    srv, t, port = _serve(handler)
    try:
        status, _, data = _req(port, "GET", "/tabs", headers=_authz())
        assert status == 500
        body = json.loads(data)
        assert body["ok"] is False
        assert body["error"] == "tabs exploded"
    finally:
        srv.shutdown()
        t.join(timeout=5)


def test_post_forbidden_origin_403(server):
    status, _, data = _req(
        server,
        "POST",
        "/snapshot",
        body={},
        headers={**_authz(), "Origin": "http://evil.example.com"},
    )
    assert status == 403
    assert json.loads(data)["error"] == "forbidden origin"


def test_hq_error_splash_when_harness_broken():
    h = _Harness(up=False, error="port busy", autostart=False)
    handler = make_handler(_HttpBackend(), token=TOKEN, harness=h)
    srv, t, port = _serve(handler)
    try:
        status, ctype, data = _req(port, "GET", "/hq", headers=_authz())
        assert status == 200
        assert "text/html" in ctype
        assert b"port busy" in data
        assert h.ensure_calls == []  # error state → straight to the error page
    finally:
        srv.shutdown()
        t.join(timeout=5)


# ── _export_env_to_os ────────────────────────────────────────────────


def test_export_env_to_os_pushes_matching_suffixes(monkeypatch):
    import browser_core.ai_bridge as ai_bridge

    monkeypatch.setattr(
        ai_bridge,
        "_load_env",
        lambda: {
            "COV_CTL_TEST_KEY": "v",
            "COV_CTL_PLAIN": "nope",
            "COV_CTL_MODEL": "m",
            "COV_CTL_HOST": "h",
            "COV_CTL_PROVIDER": "p",
            "COV_CTL_BASE_URL": "u",
        },
    )
    for k in (
        "COV_CTL_TEST_KEY",
        "COV_CTL_PLAIN",
        "COV_CTL_MODEL",
        "COV_CTL_HOST",
        "COV_CTL_PROVIDER",
        "COV_CTL_BASE_URL",
    ):
        monkeypatch.delenv(k, raising=False)
    _export_env_to_os()
    import os

    for k in (
        "COV_CTL_TEST_KEY",
        "COV_CTL_MODEL",
        "COV_CTL_HOST",
        "COV_CTL_PROVIDER",
        "COV_CTL_BASE_URL",
    ):
        assert os.environ[k] != ""
    assert "COV_CTL_PLAIN" not in os.environ
    for k in (
        "COV_CTL_TEST_KEY",
        "COV_CTL_MODEL",
        "COV_CTL_HOST",
        "COV_CTL_PROVIDER",
        "COV_CTL_BASE_URL",
    ):
        monkeypatch.delenv(k, raising=False)


# ── GuiInvoker (real QObject subclass) ─────────────────────────────────


def test_gui_invoker_is_a_real_class():
    assert isinstance(cs.GuiInvoker, type)
    assert issubclass(cs.GuiInvoker, _QtObjectStub)


def test_gui_invoker_runs_inline_on_gui_thread(monkeypatch):
    # Regression: run() from the GUI thread used to block on its own
    # signal (a ~20s UI freeze per call). GUI-thread callers now run
    # inline — nested QEventLoops inside the closures still pump.
    monkeypatch.setattr(cs, "_on_gui_thread", lambda: True)
    assert cs.GuiInvoker.run(object(), lambda: "inline-ok") == "inline-ok"


def _fresh_invoker():
    inv = cs.GuiInvoker()
    inv.invoke = _FakeSignal()  # fresh signal: no cross-test slot buildup
    inv.invoke.connect(inv._execute)
    return inv


def test_invoker_execute_runs_fn_and_sets_done():
    inv = _fresh_invoker()
    box, done = {}, threading.Event()
    inv._execute((lambda: 42, box, done))
    assert box == {"result": 42}
    assert done.is_set()


def test_invoker_execute_captures_fn_error():
    inv = _fresh_invoker()
    box, done = {}, threading.Event()
    boom = ValueError("nope")
    inv._execute((_Raising(boom), box, done))
    assert box["error"] is boom
    assert done.is_set()  # waiters are always released


def test_invoker_run_returns_result():
    assert _fresh_invoker().run(lambda: "hi") == "hi"


def test_invoker_run_reraises_fn_error():
    with pytest.raises(ZeroDivisionError):
        _fresh_invoker().run(lambda: 1 / 0)


def test_invoker_run_times_out_when_gui_is_dead():
    inv = cs.GuiInvoker.__new__(cs.GuiInvoker)
    inv.invoke = _FakeSignal()  # nothing connected → emit never answers
    with pytest.raises(TimeoutError, match="GUI thread did not respond"):
        inv.run(lambda: 1, timeout=0.05)


# ── _QuietHTTPServer.handle_error ────────────────────────────────────


def test_quiet_server_swallows_dead_client_errors():
    handler = make_handler(_HttpBackend(), token=TOKEN)
    srv = _QuietHTTPServer(("127.0.0.1", 0), handler)
    try:
        try:
            raise ConnectionError("tab went away")
        except ConnectionError:
            srv.handle_error(None, ("127.0.0.1", 1234))  # must not raise
        try:
            raise OSError("reset by peer")
        except OSError:
            srv.handle_error(None, ("127.0.0.1", 1234))  # must not raise
    finally:
        srv.server_close()


def test_quiet_server_delegates_unexpected_errors(capsys):
    handler = make_handler(_HttpBackend(), token=TOKEN)
    srv = _QuietHTTPServer(("127.0.0.1", 0), handler)
    try:
        srv.handle_error(None, ("127.0.0.1", 1))  # no active exception → super()
    finally:
        srv.server_close()
    assert "Exception occurred during processing" in capsys.readouterr().err


# ── BrowserControlServer extras ──────────────────────────────────────


def test_control_server_exposes_token_and_address():
    srv = BrowserControlServer(_HttpBackend(), port=0, token=TOKEN)
    try:
        assert srv.token == TOKEN
        assert srv.host == "127.0.0.1"
        assert srv.port > 0
        assert srv.base_url == f"http://127.0.0.1:{srv.port}"
        assert not srv.running
    finally:
        srv.stop()


# ── QtBrowserBackend: GUI-thread helpers ─────────────────────────────


def test_backend_requires_qt(monkeypatch):
    monkeypatch.setattr(cs, "_HAVE_QT", False)
    with pytest.raises(RuntimeError, match="PySide6 is required"):
        QtBrowserBackend(_FakeApp([]))


def test_window_no_windows_raises():
    be, app, *_ = _qt_backend()
    app.windows = []
    app._active = None
    with pytest.raises(RuntimeError, match="no browser window open"):
        be._window()


def test_window_prefers_active_window():
    be, _app, win, *_ = _qt_backend()
    assert be._window() is win


def test_window_falls_back_to_last_window():
    be, app, win, *_ = _qt_backend()
    other = _FakeWindow([_FakeView()])
    app.windows = [other]
    app._active = win  # stale reference
    assert be._window() is other


def test_view_no_active_tab_raises():
    be, _app, win, *_ = _qt_backend()
    win.tabs._views = []
    with pytest.raises(RuntimeError, match="no active tab"):
        be._view()


def test_view_returns_current_view():
    be, _app, _win, view, _ = _qt_backend()
    assert be._view() is view


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("example.com", "https://example.com"),
        ("http://x.com/", "http://x.com/"),
        ("https://x.com/", "https://x.com/"),
        ("file:///tmp/a.html", "file:///tmp/a.html"),
        ("about:blank", "about:blank"),
    ],
)
def test_normalize_url(raw, expected):
    assert QtBrowserBackend._normalize_url(raw) == expected


def test_run_js_returns_page_value():
    be, *_ = _qt_backend()
    assert be.run_js("1+1") == "ok"


def test_run_js_timeout_when_page_never_answers():
    be, *_, page = _qt_backend()
    page._never_cb = True
    with pytest.raises(TimeoutError, match="page JavaScript timed out"):
        be.run_js("1+1")


def test_wait_settled_returns_on_change():
    be, *_ = _qt_backend()
    assert be._wait_settled("state-0", timeout=4.0) is None


def test_wait_settled_waits_out_static_page():
    be, *_, page = _qt_backend()
    page._settle_fixed = "same"
    t0 = time.monotonic()
    assert be._wait_settled("same", timeout=0.3) is None
    assert time.monotonic() - t0 >= 0.3


def test_wait_ready_returns_when_complete():
    be, *_ = _qt_backend()
    assert be._wait_ready(timeout=2.0) is None


def test_wait_ready_times_out_silently():
    be, *_, page = _qt_backend()
    page._raise_on = ("document.readyState",)
    assert be._wait_ready(timeout=0.4) is None


# ── QtBrowserBackend: browser state / tabs ───────────────────────────


def test_status_reports_browser_state(monkeypatch):
    # /status reports the EFFECTIVE CDP endpoint (or "disabled"), not a
    # constant — main.py may leave the debug port off per user setting.
    monkeypatch.setenv("QTWEBENGINE_REMOTE_DEBUGGING", "127.0.0.1:9222")
    be, _app, win, _view, _page = _qt_backend()
    win.tabs._views.append(_FakeView("https://b.com/"))
    info = be.status()
    assert info["tabs"] == 2
    assert info["current_url"] == "https://example.com/"
    assert info["name"] == API_NAME
    assert info["version"] == API_VERSION
    assert info["cdp"] == "127.0.0.1:9222"
    assert info["ads_blocked"] == 7
    assert info["harness"] is False  # nothing listens on :8000


def test_status_reports_cdp_disabled(monkeypatch):
    monkeypatch.delenv("QTWEBENGINE_REMOTE_DEBUGGING", raising=False)
    be, _app, _win, _view, _page = _qt_backend()
    assert be.status()["cdp"] == "disabled"


def test_tabs_lists_views():
    be, _app, win, _view, _page = _qt_backend()
    win.tabs._views.append(_FakeView("https://b.com/"))
    assert be.tabs() == [
        {"index": 0, "url": "https://example.com/", "title": "Tab 0", "active": True},
        {"index": 1, "url": "https://b.com/", "title": "Tab 1", "active": False},
    ]


def test_navigate_normalizes_and_loads(monkeypatch):
    be, _app, win, _view, _page = _qt_backend(monkeypatch=monkeypatch)
    assert be.navigate("example.com") == "https://example.com/"
    assert win.loaded[0].spec == "https://example.com"


def test_navigate_new_tab(monkeypatch):
    be, _app, win, _view, _page = _qt_backend(monkeypatch=monkeypatch)
    be.navigate("https://x.com/", new_tab=True)
    assert win.new_tabs[0].spec == "https://x.com/"


def test_new_tab_with_url(monkeypatch):
    be, _app, win, _view, _page = _qt_backend(monkeypatch=monkeypatch)
    be.new_tab("example.com")
    assert win.new_tabs[0].spec == "https://example.com"


def test_new_tab_without_url_passes_none(monkeypatch):
    be, _app, win, _view, _page = _qt_backend(monkeypatch=monkeypatch)
    be.new_tab()
    assert win.new_tabs == [None]


def test_activate_tab():
    be, _app, win, _view, _page = _qt_backend()
    win.tabs._views.append(_FakeView())
    assert be.activate_tab(1) == 1
    assert win.tabs.currentIndex() == 1


def test_activate_tab_out_of_range():
    be, *_ = _qt_backend()
    with pytest.raises(IndexError, match=r"tab index 5 out of range"):
        be.activate_tab(5)


def test_close_tab_refuses_last_tab():
    be, *_ = _qt_backend()
    with pytest.raises(RuntimeError, match="refusing to close the last tab"):
        be.close_tab(0)


def test_close_tab_negative_closes_current():
    be, _app, win, _view, _page = _qt_backend()
    win.tabs._views.append(_FakeView())
    assert be.close_tab(-1) == 0
    assert win.tabs.closed == [0]


def test_close_tab_out_of_range():
    be, *_ = _qt_backend()
    with pytest.raises(IndexError, match=r"tab index 9 out of range"):
        be.close_tab(9)


# ── QtBrowserBackend: snapshot ───────────────────────────────────────


def test_snapshot_parses_page_json():
    be, *_ = _qt_backend()
    snap = be.snapshot()
    assert snap["title"] == "Example"
    assert snap["text"] == "hello"


def test_snapshot_empty_result_raises():
    be, *_, page = _qt_backend()
    page._snapshot_raw = ""
    with pytest.raises(RuntimeError, match="snapshot failed"):
        be.snapshot()


def test_snapshot_bad_json_raises():
    be, *_, page = _qt_backend()
    page._snapshot_raw = "{not json"
    with pytest.raises(RuntimeError, match="snapshot parse failed"):
        be.snapshot()


# ── QtBrowserBackend: act / _perform_act ─────────────────────────────


def test_act_wait():
    be, *_ = _qt_backend()
    assert be.act({"action": "wait", "text": "0.2"}) == "waited 0.2s"


def test_act_wait_bad_text_defaults_to_one_second():
    be, *_ = _qt_backend()
    assert be.act({"action": "wait", "text": "nonsense"}) == "waited 1.0s"


def test_act_wait_clamps_to_minimum():
    be, *_ = _qt_backend()
    assert be.act({"action": "wait", "text": "0"}) == "waited 0.2s"


def test_perform_act_indexed_kinds_need_index():
    be, *_ = _qt_backend()
    assert be._perform_act({"action": "click"}) == "click needs an element index"
    assert be._perform_act({"action": "type", "text": "hi"}) == "type needs an element index"
    assert be._perform_act({"action": "select"}) == "select needs an element index"


def test_perform_act_click():
    be, *_, page = _qt_backend()
    assert be._perform_act({"action": "click", "index": 2}) == "clicked"
    assert page.calls[1] == _HIGHLIGHT_JS.format(i=2)
    assert page.calls[2] == _CLICK_JS.format(i=2)


def test_perform_act_type_and_select():
    be, *_, page = _qt_backend()
    assert be._perform_act({"action": "type", "index": 1, "text": "hi"}) == "ok"
    assert _TYPE_JS.format(i=1, text=json.dumps("hi")) in page.calls
    assert be._perform_act({"action": "select", "index": 1, "text": "b"}) == "ok"
    assert _SELECT_JS.format(i=1, text=json.dumps("b")) in page.calls


def test_perform_act_press_raises_valueerror():
    """REAL BUG (not fixed — production code untouched): _PRESS_JS uses a
    ``__KEY__`` placeholder, but _perform_act formats it with
    ``.format(key=...)``. The template's literal JS braces
    (``{key: key, ...}``) make str.format raise ``ValueError: unexpected '{'
    in field name``, so EVERY press action fails before any JS runs."""
    be, *_ = _qt_backend()
    with pytest.raises(ValueError, match="unexpected"):
        be._perform_act({"action": "press", "text": "Tab"})


def test_perform_act_scroll_direction():
    be, *_, page = _qt_backend()
    assert be._perform_act({"action": "scroll", "text": "up"}) == "scrolled"
    assert "window.scrollBy(0, -700)" in page.calls
    assert be._perform_act({"action": "scroll", "text": ""}) == "scrolled"
    assert "window.scrollBy(0, 700)" in page.calls


def test_perform_act_navigate_rejects_bad_url():
    be, *_ = _qt_backend()
    assert (
        be._perform_act({"action": "navigate", "url": "javascript:alert(1)"})
        == "refused: invalid url"
    )


def test_perform_act_navigate():
    be, *_ = _qt_backend()
    assert (
        be._perform_act({"action": "navigate", "url": "https://x.com/"})
        == "navigated to https://x.com/"
    )


def test_perform_act_back():
    be, *_ = _qt_backend()
    assert be._perform_act({"action": "back"}) == "went back"


def test_perform_act_unknown_action():
    be, *_ = _qt_backend()
    assert be._perform_act({"action": "dance"}) == "unknown action: 'dance'"


def test_act_records_click_with_fingerprint():
    be, *_ = _qt_backend()
    be.start_recording("demo")
    assert be.act({"action": "click", "index": 0}) == "clicked"
    _, steps = be._recorder.stop()
    assert steps == [{"action": "click", "index": 0, "target": {"tag": "button", "text": "Go"}}]


def test_act_skips_recording_failed_result():
    be, *_, page = _qt_backend()
    page._click_results = {0: "refused: stale element"}
    be.start_recording("demo")
    assert be.act({"action": "click", "index": 0}) == "refused: stale element"
    _, steps = be._recorder.stop()
    assert steps == []


def test_navigate_records_when_recording(monkeypatch):
    be, _app, _win, _view, _page = _qt_backend(monkeypatch=monkeypatch)
    assert be.start_recording("demo") == "demo"
    be.navigate("example.com")
    name, steps = be._recorder.stop()
    assert name == "demo"
    assert steps == [{"action": "navigate", "url": "https://example.com"}]


def test_eval_js_passthrough():
    be, *_ = _qt_backend()
    assert be.eval_js("document.title") == "ok"


def test_screenshot_raises_when_cdp_unreachable():
    be, *_ = _qt_backend()
    with pytest.raises(RuntimeError, match="CDP endpoint unreachable"):
        be.screenshot("")


def test_extract_snapshot_failure_propagates():
    be, *_, page = _qt_backend()
    page._never_cb = True
    with pytest.raises(TimeoutError):
        be.extract("get the title")


def test_set_theme_unknown_name_raises():
    be, *_ = _qt_backend()
    with pytest.raises(ValueError, match="unknown theme"):
        be.set_theme("nope")


def test_set_theme_applies_to_every_window():
    be, app, win, *_ = _qt_backend()
    assert be.set_theme("neon") == "neon"
    assert app.settings.set_calls == [("theme", "neon")]
    assert win.themed == 1


# ── QtBrowserBackend: workflows / replay ─────────────────────────────


def test_stop_recording_without_start(tmp_path):
    be, *_ = _qt_backend()
    be._wf_store = WorkflowStore(directory=tmp_path / "wf")
    assert be.stop_recording() == {"saved": False, "name": "", "steps": 0}


def test_record_stop_save_list_delete_roundtrip(tmp_path, monkeypatch):
    be, _app, _win, _view, _page = _qt_backend(monkeypatch=monkeypatch)
    be._wf_store = WorkflowStore(directory=tmp_path / "wf")
    assert be.start_recording("Demo Flow") == "Demo-Flow"
    be.navigate("example.com")
    assert be.stop_recording() == {"saved": True, "name": "Demo-Flow", "steps": 1}
    assert (tmp_path / "wf" / "Demo-Flow.json").is_file()
    listed = be.list_workflows()
    assert listed["recording"]["recording"] is False
    assert any(w["name"] == "Demo-Flow" for w in listed["workflows"])
    assert be.delete_workflow("Demo-Flow") is True
    assert not (tmp_path / "wf" / "Demo-Flow.json").is_file()


def test_replay_missing_workflow_raises_keyerror(tmp_path):
    be, *_ = _qt_backend()
    be._wf_store = WorkflowStore(directory=tmp_path / "wf")
    with pytest.raises(KeyError, match="nope"):
        be.replay_workflow("nope")


def test_replay_workflow_heals_breaks_and_reports(tmp_path):
    be, *_, page = _qt_backend()
    be._wf_store = WorkflowStore(directory=tmp_path / "wf")
    page._candidates = [{"index": 5, "el_id": "go", "tag": "button", "text": "Go"}]
    page._click_results = {9: "element not found: gone"}
    be._wf_store.save(
        "flow",
        [
            {"action": "wait", "text": "0.2"},
            {
                "action": "click",
                "index": 3,
                "target": {"el_id": "go", "tag": "button", "text": "Go"},
            },
            {"action": "click", "index": 9},
        ],
    )
    out = be.replay_workflow("flow")
    assert out["workflow"] == "flow"
    assert out["total"] == 3
    assert out["succeeded"] == 2
    assert [r["ok"] for r in out["results"]] == [True, True, False]
    assert out["results"][1]["healed"] is True
    assert out["results"][2]["healed"] is False
    assert "element not found" in out["results"][2]["detail"]
    assert be._replaying is False  # reset even after the early break


def test_replay_act_exception_is_recorded_not_raised(tmp_path):
    be, *_, page = _qt_backend()
    be._wf_store = WorkflowStore(directory=tmp_path / "wf")
    page._raise_on = ("history.back",)
    be._wf_store.save("flow", [{"action": "back"}])
    out = be.replay_workflow("flow")
    assert out["total"] == 1
    assert out["succeeded"] == 0
    assert out["results"][0]["ok"] is False
    assert "timed out" in out["results"][0]["detail"]
    assert be._replaying is False


# ── QtBrowserBackend: netmon / schedules ─────────────────────────────


def test_netmon_lazy_paths():
    be, *_ = _qt_backend()
    assert be.netmon_events() == {"running": False, "target": "", "error": "", "seq": 0, "rows": []}
    assert be.netmon_stop() == {"stopped": True}
    assert be.netmon_clear() == {"cleared": True}
    assert "log" in be.netmon_har()


def test_netmon_start_stop_roundtrip():
    be, *_ = _qt_backend()
    out = be.netmon_start("")
    assert out == {"started": True, "target": "https://example.com/"}
    assert be.netmon_events()["rows"] == []
    assert be.netmon_stop() == {"stopped": True}
    assert be.netmon_clear() == {"cleared": True}
    assert be.netmon_har()["log"]["entries"] == []


def test_schedule_list_and_set(tmp_path):
    be, *_ = _qt_backend()
    be._schedules = ScheduleStore(path=tmp_path / "sched.json")
    out = be.schedule_list()
    assert out["schedules"] == []
    assert out["intervals"] == INTERVALS
    entry = be.schedule_set("morning", 60)
    assert entry == {"name": "morning", "every_min": 60, "label": "Hourly"}
    assert be.schedule_list()["schedules"][0]["name"] == "morning"
    assert be.schedule_set("morning", 0)["label"] == "Off"
    assert be.schedule_list()["schedules"] == []


# ── gap-fillers for the remaining uncovered backend/handler branches ──────


def test_send_file_swallows_write_error():
    h = _no_op_handler(_handler())
    h.wfile = SimpleNamespace(write=_Raising(OSError("gone")))
    asset = (cs.STATIC_DIR / "xterm.js").resolve()
    assert asset.exists()
    h._send_file(asset, "application/javascript; charset=utf-8")  # must not raise


def test_wait_settled_tolerates_js_errors():
    be, *_, page = _qt_backend()
    page._never_cb = True
    t0 = time.monotonic()
    # Each JS attempt raises (swallowed); the loop still waits out the deadline.
    assert be._wait_settled("x", timeout=0.3) is None
    assert time.monotonic() - t0 >= 0.3


def test_wait_ready_waits_out_incomplete_state():
    be, *_, page = _qt_backend()
    page._ready_state = "loading"
    t0 = time.monotonic()
    assert be._wait_ready(timeout=0.4) is None
    assert time.monotonic() - t0 >= 0.4


def test_replay_workflow_copies_url_hint(tmp_path, monkeypatch):
    be, *_ = _qt_backend(monkeypatch=monkeypatch)
    be._wf_store = WorkflowStore(directory=tmp_path / "wf")
    be._wf_store.save("demo", [{"action": "wait", "text": "0.01", "url": "https://x.com/"}])
    out = be.replay_workflow("demo")
    assert out["total"] == 1
    assert out["succeeded"] == 1
    assert out["results"][0]["ok"] is True


def test_netmon_start_with_explicit_filter_and_restart():
    be, *_ = _qt_backend()
    first = be.netmon_start("https://x.com")
    assert first == {"started": True, "target": "https://x.com"}
    second = be.netmon_start("https://y.com")  # restarts the existing monitor
    assert second == {"started": True, "target": "https://y.com"}
    assert be.netmon_stop() == {"stopped": True}


def test_schedule_store_created_once(tmp_path):
    be, *_ = _qt_backend()
    be.schedule_list()
    store = be._schedules
    assert store is not None
    be.schedule_list()  # store already present → no second ScheduleStore()
    assert be._schedules is store
    be._schedules = ScheduleStore(path=tmp_path / "sched.json")
    store = be._schedules
    be.schedule_set("nightly", 1440)
    assert be._schedules is store


def test_control_server_start_is_idempotent():
    be = _HttpBackend()
    srv = BrowserControlServer(be, "127.0.0.1", 0, TOKEN)
    try:
        srv.start()
        assert srv.running
        first_thread = srv._thread
        srv.start()  # already running → no-op
        assert srv._thread is first_thread
        assert srv.base_url.startswith("http://127.0.0.1:")
    finally:
        srv.stop()
        srv.stop()  # second stop → no-op
    assert not srv.running


# ── AI-backed routes with a hand-written fake provider ───────────────────


class _FakeAI:
    """Stand-in for AIBridge: scripted replies, optional provider failure."""

    def __init__(self, reply="the answer", fail=False):
        self.reply = reply
        self.fail = fail
        self.seen = []

    def providers(self):
        if self.fail:
            raise RuntimeError("no providers configured")
        return ["fake"]

    def default_provider(self):
        if self.fail:
            raise RuntimeError("no providers configured")
        return "fake"

    def local_status(self):
        if self.fail:
            raise RuntimeError("no providers configured")
        return {"fake": "ok"}

    async def chat(self, messages, provider=None):
        self.seen.append((messages, provider))
        return self.reply, "fake"


def test_ai_info_reports_providers():
    be, *_ = _qt_backend()
    be._ai = _FakeAI()
    assert be.ai_info() == {
        "ai_providers": ["fake"],
        "ai_default": "fake",
        "ai_local_status": {"fake": "ok"},
    }


def test_ai_info_falls_back_when_provider_layer_breaks():
    be, *_ = _qt_backend()
    be._ai = _FakeAI(fail=True)
    assert be.ai_info() == {"ai_providers": [], "ai_default": None, "ai_local_status": {}}


def test_ask_includes_page_snapshot():
    be, *_ = _qt_backend()
    ai = _FakeAI(reply="the answer")
    be._ai = ai
    assert be.ask("What is this page?") == "the answer"
    messages, provider = ai.seen[0]
    assert provider is None
    assert any("Example" in m.get("content", "") for m in messages)


def test_ask_without_snapshot_context():
    be, *_ = _qt_backend()
    be._ai = _FakeAI(reply="hi")
    be.snapshot = _Raising(RuntimeError("no page"))  # page context is best-effort
    assert be.ask("hi") == "hi"


def test_extract_parses_model_json():
    be, *_ = _qt_backend()
    be._ai = _FakeAI(reply='{"title": "Example"}')
    assert be.extract("get the title") == {"title": "Example"}


def test_extract_rejects_unparseable_model_output():
    be, *_ = _qt_backend()
    be._ai = _FakeAI(reply="definitely not json {{{")
    with pytest.raises(RuntimeError, match="did not return parseable JSON"):
        be.extract("get the title")


def test_schedule_set_creates_store_lazily(tmp_path, monkeypatch):
    be, *_ = _qt_backend()
    assert be._schedules is None
    monkeypatch.setattr(cs, "ScheduleStore", lambda: ScheduleStore(path=tmp_path / "sched.json"))
    entry = be.schedule_set("nightly", 1440)
    assert entry == {"name": "nightly", "every_min": 1440, "label": "Daily"}
    assert be._schedules is not None


def test_status_harness_probe_success(monkeypatch):
    be, *_ = _qt_backend()

    class _Resp:
        status_code = 200

    monkeypatch.setattr("httpx.get", lambda *a, **k: _Resp())
    assert be.status()["harness"] is True


def test_ai_info_builds_bridge_lazily():
    be, *_ = _qt_backend()
    assert be._ai is None
    info = be.ai_info()
    assert be._ai is not None  # real AIBridge constructed and cached
    assert set(info) == {"ai_providers", "ai_default", "ai_local_status"}


def test_ask_builds_bridge_lazily(monkeypatch):
    be, *_ = _qt_backend()
    assert be._ai is None
    fake = _FakeAI(reply="lazy answer")
    monkeypatch.setattr("browser_core.ai_bridge.AIBridge", lambda: fake)
    assert be.ask("q?") == "lazy answer"
    assert be._ai is fake


def test_extract_builds_bridge_lazily(monkeypatch):
    be, *_ = _qt_backend()
    assert be._ai is None
    fake = _FakeAI(reply='{"a": 1}')
    monkeypatch.setattr("browser_core.ai_bridge.AIBridge", lambda: fake)
    assert be.extract("get a") == {"a": 1}
    assert be._ai is fake


def test_status_without_harness_or_ai_info():
    class _BareBackend:
        def status(self):
            return {"ok": True}

    handler = make_handler(_BareBackend(), token=TOKEN)  # harness=None
    srv, t, port = _serve(handler)
    try:
        status, _, data = _req(port, "GET", "/status", headers=_authz())
        assert status == 200
        assert json.loads(data)["ok"] is True
    finally:
        srv.shutdown()
        t.join(timeout=5)


def test_get_dashboard_renders(server):
    status, ctype, data = _req(server, "GET", "/dashboard", headers=_authz())
    assert status == 200
    assert "text/html" in ctype
    assert b"LuckyD" in data


def test_workflow_delete_requires_name(server):
    status, _, data = _req(server, "POST", "/workflow/delete", {}, headers=_authz())
    assert status == 400
    assert json.loads(data)["error"] == "name required"
