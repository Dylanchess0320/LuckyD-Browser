"""Slice 3 — shared Hub memory / handoff note.

Covers the HandoffStore (append/latest/list/cap/corrupt-file tolerance),
the control-server surface (handoff on /chat/state, GET/POST
/chat/handoff), and the Chat page markers (handoff display, note input,
automatic Claw-receipt posting). Mirrors the Qt stubs + live-server
harness from test_hub_status.py (PySide6 is not installed here).
"""

from __future__ import annotations

import http.client
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.insert(0, str(_BROWSER_DIR))


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
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()
_qtcore = sys.modules["PySide6.QtCore"]
if not isinstance(getattr(_qtcore, "QObject", None), type):
    _qtcore.QObject = _QtObjectStub
if isinstance(getattr(_qtcore, "Signal", None), MagicMock):
    _qtcore.Signal = lambda *types: _FakeSignal(*types)

from browser_core import hub_memory as hm


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


def _serve_ctl(settings, monkeypatch, handoff_path):
    """Live control server (loopback) with stubbed Hub/Claw upstreams."""
    import browser_core.control_server as cs
    import browser_core.hub_memory as hub_mem

    monkeypatch.setattr(hub_mem, "HANDOFF_PATH", Path(handoff_path))
    calls: list = []
    routes = [
        ("18790/healthz", 200, {"ok": True, "ollama": True}),
        ("18791/healthz", 200, {"ok": True, "enabled": True, "projects": ["browser"]}),
    ]
    monkeypatch.setattr(urllib.request, "urlopen", _opener(routes, calls))
    handler = cs.make_handler(SimpleNamespace(), token="", settings=settings)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, f"127.0.0.1:{httpd.server_address[1]}"


# ── store ──────────────────────────────────────────────────────────────


def test_append_latest_and_count(tmp_path) -> None:
    store = hm.HandoffStore(tmp_path / "h.json")
    assert store.latest() is None
    assert store.count() == 0
    out = store.append("chat", "first note")
    assert out["state"] == "ok"
    assert out["entry"]["source"] == "chat"
    assert out["entry"]["text"] == "first note"
    assert isinstance(out["entry"]["ts"], float)
    store.append("claw", "second note")
    assert store.latest()["text"] == "second note"
    assert store.count() == 2
    assert [e["text"] for e in store.list()] == ["first note", "second note"]
    # A fresh store on the same file sees the same log (survives restart).
    assert hm.HandoffStore(tmp_path / "h.json").count() == 2


def test_append_rejects_empty_and_oversize_text(tmp_path) -> None:
    store = hm.HandoffStore(tmp_path / "h.json")
    assert store.append("chat", "   ")["state"] == "error"
    assert store.append("chat", "x" * 2001)["state"] == "error"
    assert store.count() == 0


def test_store_caps_entries_and_tolerates_corrupt_file(tmp_path) -> None:
    path = tmp_path / "h.json"
    store = hm.HandoffStore(path)
    for i in range(55):
        assert store.append("chat", f"note {i}")["state"] == "ok"
    assert store.count() == hm.MAX_ENTRIES
    assert store.list(1)[0]["text"] == "note 54"
    path.write_text("{not json", encoding="utf-8")
    assert hm.HandoffStore(path).latest() is None
    assert hm.HandoffStore(path).count() == 0


def test_handoff_state_never_raises(tmp_path) -> None:
    store = hm.HandoffStore(tmp_path / "h.json")
    store.append("terminal", "from the terminal")
    state = hm.handoff_state(store)
    assert state["count"] == 1
    assert state["latest"]["source"] == "terminal"


# ── control-server surface ─────────────────────────────────────────────


def test_chat_state_includes_handoff(monkeypatch, tmp_path) -> None:
    settings = _settings(hub_token="t", claw_token="c")
    httpd, base = _serve_ctl(settings, monkeypatch, tmp_path / "h.json")
    try:
        status, raw = _req(base, "GET", "/chat/state")
        state = json.loads(raw)
        assert status == 200
        assert state["handoff"] == {"latest": None, "count": 0}
        # Slice 1 + 2 shape is unchanged.
        assert state["components"]["hub"]["state"] == "ok"
        assert state["providers"]["current"] == "auto"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_chat_handoff_post_and_read(monkeypatch, tmp_path) -> None:
    settings = _settings(hub_token="t", claw_token="c")
    path = tmp_path / "h.json"
    httpd, base = _serve_ctl(settings, monkeypatch, path)
    try:
        status, raw = _req(base, "POST", "/chat/handoff", {"source": "chat", "text": "hello hub"})
        body = json.loads(raw)
        assert status == 200
        assert body["state"] == "ok"
        assert body["entry"]["text"] == "hello hub"
        # The note shows up on the next state read, and in the log.
        _status, raw = _req(base, "GET", "/chat/state")
        handoff = json.loads(raw)["handoff"]
        assert handoff["count"] == 1
        assert handoff["latest"]["text"] == "hello hub"
        _status, raw = _req(base, "GET", "/chat/handoff")
        log = json.loads(raw)
        assert log["count"] == 1
        assert log["entries"][0]["source"] == "chat"
        # Persisted on disk: a fresh store sees it.
        assert hm.HandoffStore(path).latest()["text"] == "hello hub"
        # Empty text is a 400 and saves nothing.
        status, raw = _req(base, "POST", "/chat/handoff", {"source": "chat", "text": "  "})
        assert status == 400
        assert json.loads(raw)["state"] == "error"
        _status, raw = _req(base, "GET", "/chat/state")
        assert json.loads(raw)["handoff"]["count"] == 1
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_chat_page_has_handoff_surface() -> None:
    from browser_core.chat_page import chat_html

    page = chat_html(_settings())
    assert 'id="handoff-state"' in page
    assert 'id="handoff-input"' in page
    assert 'id="handoff-save"' in page
    assert "/chat/handoff" in page
    assert "postClawReceipt" in page
    assert "renderHandoff" in page
