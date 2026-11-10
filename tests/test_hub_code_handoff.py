"""Slice 9 — LuckyD Code wired to the shared Hub handoff note.

Covers the Code-side client (``core/hub_handoff.py``: read/write, env
override, fail-quiet on a down Hub, session-end receipt text), the
``/handoff`` REPL command + session-end receipt in ``main.py``, the
terminal bridge handing the Hub URL to agent CLIs
(``TerminalServer.hub_url`` → ``LUCKYD_HUB_URL``), and the round trip
that proves the definition of done: a note written by Code shows up on
``GET /chat/state`` (Browser Chat) and a note written by Chat is
readable by the Code client — against a live control server.
"""

from __future__ import annotations

import asyncio
import http.client
import io
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
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

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

from core import hub_handoff as hh


class _FakeResp:
    def __init__(self, obj):
        self._body = json.dumps(obj).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _opener(body=None, exc=None):
    """Fake urlopen: records requests, returns `body` or raises `exc`."""
    calls: list = []

    def open_fn(req, timeout=None):
        calls.append(req)
        if exc is not None:
            raise exc
        return _FakeResp(body if body is not None else {})

    open_fn.calls = calls
    return open_fn


def _agent(turns=0, model="", project=""):
    info = SimpleNamespace(name=project, is_empty=lambda: not project)
    return SimpleNamespace(turn_count=turns, model=model, _project_info=info)


# ── client: URL + auth ─────────────────────────────────────────────────


def test_hub_url_default_and_env_override(monkeypatch) -> None:
    monkeypatch.delenv("LUCKYD_HUB_URL", raising=False)
    assert hh.hub_url() == "http://127.0.0.1:9777"
    monkeypatch.setenv("LUCKYD_HUB_URL", "http://127.0.0.1:9999/")
    assert hh.hub_url() == "http://127.0.0.1:9999/"
    monkeypatch.setenv("LUCKYD_HUB_URL", "   ")
    assert hh.hub_url() == "http://127.0.0.1:9777"


def test_read_latest_returns_entry_and_hits_handoff_endpoint(monkeypatch) -> None:
    monkeypatch.delenv("LUCKYD_CTL_TOKEN", raising=False)
    opener = _opener({"latest": {"ts": 1.0, "source": "chat", "text": "hi"}, "count": 1})
    latest = hh.read_latest(opener=opener)
    assert latest == {"ts": 1.0, "source": "chat", "text": "hi"}
    req = opener.calls[0]
    assert req.full_url == "http://127.0.0.1:9777/chat/handoff"
    assert req.get_method() == "GET"
    assert req.get_header("Authorization") is None


def test_read_latest_sends_bearer_token_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("LUCKYD_CTL_TOKEN", "sekrit")
    opener = _opener({"latest": None, "count": 0})
    assert hh.read_latest(opener=opener) is None
    assert opener.calls[0].get_header("Authorization") == "Bearer sekrit"


def test_read_latest_none_when_empty_or_down() -> None:
    assert hh.read_latest(opener=_opener({"latest": None, "count": 0})) is None
    # A down Hub never raises — the REPL stays silent instead of crashing.
    assert hh.read_latest(opener=_opener(exc=urllib.error.URLError("conn refused"))) is None
    assert hh.read_latest(opener=_opener(body={"not": "a handoff"})) is None


def test_write_note_posts_json_to_handoff_endpoint(monkeypatch) -> None:
    monkeypatch.delenv("LUCKYD_CTL_TOKEN", raising=False)
    opener = _opener({"state": "ok", "entry": {"source": "code", "text": "done"}})
    ok, reason = hh.write_note("done", opener=opener)
    assert (ok, reason) == (True, "")
    req = opener.calls[0]
    assert req.full_url == "http://127.0.0.1:9777/chat/handoff"
    assert req.get_method() == "POST"
    assert json.loads(req.data.decode()) == {"source": "code", "text": "done"}


def test_write_note_rejects_empty_without_touching_the_hub() -> None:
    opener = _opener({"state": "ok"})
    ok, reason = hh.write_note("   ", opener=opener)
    assert ok is False
    assert "text required" in reason
    assert opener.calls == []


def test_write_note_reports_http_rejection() -> None:
    err = urllib.error.HTTPError(
        "http://127.0.0.1:9777/chat/handoff",
        400,
        "Bad Request",
        {},
        io.BytesIO(b'{"state": "error", "error": "text too long (max 2000 chars)"}'),
    )
    ok, reason = hh.write_note("x" * 2001, opener=_opener(exc=err))
    assert ok is False
    assert "400" in reason and "too long" in reason


def test_write_note_reports_unreachable_hub() -> None:
    ok, reason = hh.write_note("hi", opener=_opener(exc=urllib.error.URLError("down")))
    assert ok is False
    assert "could not reach the Hub" in reason


# ── session-end receipt text ───────────────────────────────────────────


def test_session_end_note_none_when_session_did_nothing() -> None:
    assert hh.session_end_note(_agent(turns=0)) is None
    assert hh.session_end_note(SimpleNamespace()) is None


def test_session_end_note_formats_turns_model_project() -> None:
    note = hh.session_end_note(_agent(turns=3, model="kimi-k3", project="LuckyD"))
    assert note == "Code session ended - LuckyD - 3 turns, kimi-k3"
    assert hh.session_end_note(_agent(turns=1)) == "Code session ended - no project - 1 turn"


# ── terminal bridge: Hub URL into agent CLIs ───────────────────────────


def test_terminal_server_stores_hub_url() -> None:
    from browser_core.terminal_server import DEFAULT_HUB_URL, TerminalServer

    assert TerminalServer().hub_url == DEFAULT_HUB_URL
    assert TerminalServer(hub_url="http://127.0.0.1:9999").hub_url == "http://127.0.0.1:9999"
    assert TerminalServer(hub_url="  ").hub_url == DEFAULT_HUB_URL


# ── /handoff REPL command + session-end receipt ────────────────────────


class _FakeUI:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def markdown(self, text):
        self.calls.append(("markdown", text))

    def info(self, text):
        self.calls.append(("info", text))

    def success(self, text):
        self.calls.append(("success", text))

    def warn(self, text):
        self.calls.append(("warn", text))


def _run_handle_command(monkeypatch, cmd, agent=None):
    import main

    monkeypatch.setattr(main, "_ensure_all_heavy", lambda: None)
    fake_ui = _FakeUI()
    monkeypatch.setattr(main, "ui", fake_ui)
    result = asyncio.run(main.handle_command(agent or SimpleNamespace(), cmd))
    return result, fake_ui


def test_handoff_command_read_shows_latest(monkeypatch) -> None:
    monkeypatch.setattr(hh, "read_latest", lambda *a, **k: {"source": "chat", "text": "from chat"})
    exited, ui = _run_handle_command(monkeypatch, "/handoff")
    assert exited is False
    assert ui.calls == [("markdown", "**Handoff** (chat): from chat")]


def test_handoff_command_read_when_no_note(monkeypatch) -> None:
    monkeypatch.setattr(hh, "read_latest", lambda *a, **k: None)
    _, ui = _run_handle_command(monkeypatch, "/handoff")
    assert ui.calls[0][0] == "info"
    assert "No handoff note yet" in ui.calls[0][1]


def test_handoff_command_write_keeps_original_casing(monkeypatch) -> None:
    seen: dict = {}

    def _write(text, **kwargs):
        seen["text"] = text
        return True, ""

    monkeypatch.setattr(hh, "write_note", _write)
    _, ui = _run_handle_command(monkeypatch, "/handoff Hello World From Code")
    # handle_command lowercases the command — the note text must not be.
    assert seen["text"] == "Hello World From Code"
    assert ui.calls == [("success", "Handoff note saved — it shows in Browser Chat.")]


def test_handoff_command_write_failure_warns(monkeypatch) -> None:
    monkeypatch.setattr(hh, "write_note", lambda *a, **k: (False, "could not reach the Hub"))
    _, ui = _run_handle_command(monkeypatch, "/handoff hello")
    assert ui.calls[0][0] == "warn"
    assert "could not reach the Hub" in ui.calls[0][1]


def test_post_handoff_receipt_posts_only_after_work(monkeypatch) -> None:
    import main

    posted: list = []
    monkeypatch.setattr(hh, "write_note", lambda text, **k: posted.append(text) or (True, ""))
    main._post_handoff_receipt(_agent(turns=0))
    assert posted == []
    main._post_handoff_receipt(_agent(turns=2, model="kimi-k3", project="LuckyD"))
    assert posted == ["Code session ended - LuckyD - 2 turns, kimi-k3"]


# ── round trip against a live control server ───────────────────────────


class _QtObjectStub:
    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return MagicMock(name=f"qtstub.{name}")


class _FakeSignal:
    def __init__(self, *types):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in list(self._slots):
            slot(*args)


_qtcore = sys.modules["PySide6.QtCore"]
if not isinstance(getattr(_qtcore, "QObject", None), type):
    _qtcore.QObject = _QtObjectStub
if isinstance(getattr(_qtcore, "Signal", None), MagicMock):
    _qtcore.Signal = lambda *types: _FakeSignal(*types)


def _settings(**values):
    return SimpleNamespace(get=lambda key, default=None: values.get(key, default))


def _http(base, method, path, body=None):
    conn = http.client.HTTPConnection(base, timeout=10)
    payload = json.dumps(body) if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8", "replace")
    conn.close()
    return resp.status, raw


def _serve_ctl(monkeypatch, handoff_path):
    """Live control server (loopback) with stubbed Hub/Claw upstreams."""
    import browser_core.control_server as cs
    import browser_core.hub_memory as hub_mem

    monkeypatch.setattr(hub_mem, "HANDOFF_PATH", Path(handoff_path))

    class _FakeResp2:
        def __init__(self, status, obj):
            self.status = status
            self._body = json.dumps(obj).encode()

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    routes = [
        ("18790/healthz", 200, {"ok": True, "ollama": True}),
        ("18791/healthz", 200, {"ok": True, "enabled": True, "projects": ["browser"]}),
    ]
    real_urlopen = urllib.request.urlopen

    def open_fn(req, timeout=None):
        for sub, status, obj in routes:
            if sub in req.full_url:
                return _FakeResp2(status, obj)
        # Anything else (e.g. the Code client's own calls to this server)
        # goes to the real urlopen — the stub only covers the health checks.
        return real_urlopen(req, timeout=timeout)

    monkeypatch.setattr(urllib.request, "urlopen", open_fn)
    handler = cs.make_handler(SimpleNamespace(), token="", settings=_settings())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, f"127.0.0.1:{httpd.server_address[1]}"


def test_round_trip_code_write_shows_in_chat_state(monkeypatch, tmp_path) -> None:
    """Note written by Code shows in Browser Chat's state surface."""
    httpd, base = _serve_ctl(monkeypatch, tmp_path / "h.json")
    try:
        ok, reason = hh.write_note("fixed the bug, tests green", hub=f"http://{base}")
        assert (ok, reason) == (True, "")
        status, raw = _http(base, "GET", "/chat/state")
        assert status == 200
        handoff = json.loads(raw)["handoff"]
        assert handoff["count"] == 1
        assert handoff["latest"]["source"] == "code"
        assert handoff["latest"]["text"] == "fixed the bug, tests green"
        status, raw = _http(base, "GET", "/chat/handoff")
        assert json.loads(raw)["entries"][0]["source"] == "code"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_round_trip_chat_write_read_by_code(monkeypatch, tmp_path) -> None:
    """Note written by Chat is readable by the Code client."""
    httpd, base = _serve_ctl(monkeypatch, tmp_path / "h.json")
    try:
        status, _raw = _http(
            base, "POST", "/chat/handoff", {"source": "chat", "text": "work on the Hub next"}
        )
        assert status == 200
        latest = hh.read_latest(hub=f"http://{base}")
        assert latest is not None
        assert latest["source"] == "chat"
        assert latest["text"] == "work on the Hub next"
    finally:
        httpd.shutdown()
        httpd.server_close()
