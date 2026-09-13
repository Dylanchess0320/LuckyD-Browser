"""Night-4 browser-core audit: terminal_server.py round 2.

Night-1 covered: _shell_command, _mesh_shell_command, mesh_shells_available,
SHELLS registry consistency, _client_options parsing/clamps, _client_token,
and the fail-closed auth check. This file covers _expand, the _handle
dispatch (unauthorized rejection, spawn failure, message routing incl. the
resize control frame), and start()/stop() without binding a real socket.
"""

from __future__ import annotations

import sys
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
from browser_core.terminal_server import (
    TERM_COOKIE,
    TerminalServer,
    _client_options,
    _client_token,
    _expand,
)

TOKEN = "night4-term-secret"


def _fake_ws(path="", cookie=""):
    """Mutable fake websocket (SimpleNamespace can't hold bound methods cleanly)."""

    class _WS:
        def __init__(self):
            self.request = SimpleNamespace(
                path=path,
                headers={"Cookie": f"{TERM_COOKIE}={cookie}"} if cookie else {},
            )
            self.sent = []
            self.closed = []

        def send(self, msg):
            self.sent.append(msg)

        def close(self, **kw):
            self.closed.append(kw)

    return _WS()


# ── _expand ──────────────────────────────────────────────────────────


def test_expand_home(monkeypatch) -> None:
    # POSIX expanduser reads $HOME; Windows reads %USERPROFILE% instead.
    monkeypatch.setenv("HOME", "/fake/home")
    monkeypatch.setenv("USERPROFILE", "/fake/home")
    assert _expand("~/docs") == Path("/fake/home/docs")


def test_expand_env_var(monkeypatch) -> None:
    monkeypatch.setenv("NIGHT4_TEST_DIR", "/tmp/n4")
    assert _expand("$NIGHT4_TEST_DIR/x") == Path("/tmp/n4/x")


def test_expand_plain() -> None:
    assert _expand("/abs/path") == Path("/abs/path")


# ── _client_token / _client_options leftovers ─────────────────────────


def test_client_token_missing_request_is_empty() -> None:
    assert _client_token(object()) == ""


def test_client_token_wrong_cookie_name_is_empty() -> None:
    ws = SimpleNamespace(request=SimpleNamespace(path="", headers={"Cookie": "other=1"}))
    assert _client_token(ws) == ""


def test_client_token_strips_quotes() -> None:
    ws = SimpleNamespace(
        request=SimpleNamespace(path="", headers={"Cookie": f'{TERM_COOKIE}="abc"'})
    )
    assert _client_token(ws) == "abc"


def test_client_options_no_request_defaults() -> None:
    assert _client_options(object()) == (120, 30, "agent")


# ── _authorized ──────────────────────────────────────────────────────


def test_authorized_empty_token_fail_closed() -> None:
    srv = TerminalServer(token="")
    ws = _fake_ws(cookie=TOKEN)
    assert srv._authorized(ws) is False


def test_authorized_wrong_token() -> None:
    srv = TerminalServer(token=TOKEN)
    assert srv._authorized(_fake_ws(cookie="wrong")) is False


def test_authorized_correct_token() -> None:
    srv = TerminalServer(token=TOKEN)
    assert srv._authorized(_fake_ws(cookie=TOKEN)) is True


def test_authorized_no_cookie() -> None:
    srv = TerminalServer(token=TOKEN)
    assert srv._authorized(_fake_ws()) is False


# ── _handle: unauthorized ────────────────────────────────────────────


def test_handle_unauthorized_closes_1008(monkeypatch) -> None:
    srv = TerminalServer(token=TOKEN)
    ws = _fake_ws(cookie="wrong")
    spawned = []
    monkeypatch.setattr(ts, "_spawn_pty", lambda *a, **k: spawned.append(1))
    srv._handle(ws)
    assert spawned == []  # no PTY ever created
    assert ws.closed == [{"code": 1008, "reason": "terminal authentication required"}]
    assert ws.sent == []


# ── _handle: spawn failure ───────────────────────────────────────────


def test_handle_spawn_failure_notifies_client(monkeypatch) -> None:
    srv = TerminalServer(token=TOKEN)
    ws = _fake_ws(cookie=TOKEN)

    def _boom(*a, **k):
        raise RuntimeError("no pywinpty")

    monkeypatch.setattr(ts, "_spawn_pty", _boom)
    srv._handle(ws)
    assert any("terminal failed to start" in m for m in ws.sent)
    assert ws.closed != []  # connection closed, no exception leaked
    assert not any("pywinpty" in m for m in ws.sent)  # internals not leaked


def test_handle_spawn_failure_close_error_suppressed(monkeypatch) -> None:
    srv = TerminalServer(token=TOKEN)
    ws = _fake_ws(cookie=TOKEN)
    monkeypatch.setattr(ts, "_spawn_pty", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    ws.close = lambda **kw: (_ for _ in ()).throw(RuntimeError("gone"))
    srv._handle(ws)  # must not raise


# ── _handle: message routing ─────────────────────────────────────────


class _FakePTY:
    def __init__(self):
        self.written = []
        self.sizes = []
        self.alive = True

    def isalive(self):
        return self.alive

    def read(self, blocking=False):
        self.alive = False  # die after the first pump iteration
        return ""

    def write(self, text):
        self.written.append(text)

    def set_size(self, cols, rows):
        self.sizes.append((cols, rows))

    def cancel_io(self):
        pass


def _handle_with_messages(monkeypatch, messages, path="/?cols=80&rows=24&shell=cmd"):
    srv = TerminalServer(token=TOKEN)
    ws = _fake_ws(path=path, cookie=TOKEN)

    class _IterWS(type(ws)):
        def __iter__(self):
            return iter(messages)

    itws = _IterWS()
    itws.request = ws.request
    pty = _FakePTY()
    monkeypatch.setattr(ts, "_spawn_pty", lambda *a, **k: pty)
    srv._handle(itws)
    return srv, itws, pty


def test_handle_resize_frame_clamped(monkeypatch) -> None:

    _srv, _ws, pty = _handle_with_messages(
        monkeypatch, ['{"type": "resize", "cols": 5, "rows": 9999}']
    )
    assert pty.sizes == [(20, 200)]  # clamped to the 20..500 / 5..200 box
    assert pty.written == []  # control frame is not written to the shell


def test_handle_json_non_resize_is_passthrough(monkeypatch) -> None:
    _srv, _ws, pty = _handle_with_messages(monkeypatch, ['{"cmd": "ls"}'])
    assert pty.written == ['{"cmd": "ls"}']


def test_handle_plain_keystrokes_written(monkeypatch) -> None:
    _srv, _ws, pty = _handle_with_messages(monkeypatch, ["ls -la\r", b"bytes\r"])
    assert pty.written == ["ls -la\r", "bytes\r"]


def test_handle_pty_write_error_breaks_loop(monkeypatch) -> None:
    import time

    srv2 = TerminalServer(token=TOKEN)
    ws2 = _fake_ws(cookie=TOKEN)

    class _IterWS(type(ws2)):
        def __iter__(self):
            return iter(["boom"])

    itws2 = _IterWS()
    itws2.request = ws2.request
    pty2 = _FakePTY()
    pty2.write = lambda text: (_ for _ in ()).throw(OSError("dead"))
    monkeypatch.setattr(ts, "_spawn_pty", lambda *a, **k: pty2)
    srv2._handle(itws2)  # write raises → loop breaks, client discarded, no raise
    time.sleep(0.1)
    assert itws2 not in srv2._clients


def test_handle_tracks_and_releases_client(monkeypatch) -> None:
    import time

    srv = TerminalServer(token=TOKEN)
    ws = _fake_ws(cookie=TOKEN)

    class _IterWS(type(ws)):
        def __iter__(self):
            return iter([])

    itws = _IterWS()
    itws.request = ws.request
    monkeypatch.setattr(ts, "_spawn_pty", lambda *a, **k: _FakePTY())
    srv._handle(itws)
    time.sleep(0.1)  # let the pump_out thread finish and discard
    assert itws not in srv._clients


# ── start / stop ─────────────────────────────────────────────────────


def test_start_without_websockets_returns_false(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "websockets.sync.server", None)
    srv = TerminalServer(port=0)
    assert srv.start() is False
    assert srv.running is False


def test_start_bind_failure_returns_false(monkeypatch) -> None:
    class _Serve:
        def __init__(self, *a, **k):
            raise OSError("port in use")

    monkeypatch.setitem(sys.modules, "websockets.sync.server", SimpleNamespace(serve=_Serve))
    srv = TerminalServer(port=0)
    assert srv.start() is False


def test_stop_without_server_is_safe() -> None:
    srv = TerminalServer(port=0)
    srv.stop()  # must not raise
    assert srv.running is False


def test_stop_closes_tracked_clients() -> None:
    srv = TerminalServer(port=0)
    ws = _fake_ws(cookie=TOKEN)
    srv._clients.add(ws)
    srv.stop()
    assert ws.closed != []
    assert srv._clients == set()
