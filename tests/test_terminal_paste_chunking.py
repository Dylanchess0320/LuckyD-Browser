"""Regression test for the antigravity "too much info corrupts" bug.

Pasting large text into the agent terminal used to send it as ONE WebSocket
message → ONE pty.write() call. Large single writes garble/truncate in
ConPTY/pywinpty, and pastes over 1MB kill the WS connection (max_size).

The fix chunks writes at 8KB (backend) and pastes at 16KB (frontend).
This test verifies a 200KB paste arrives at the PTY intact.
"""

import threading

import pytest

from browser.browser_core import terminal_server
from browser.browser_core.terminal_server import TerminalServer


class FakePty:
    """Captures written data; pretends to be dead so pump_out exits."""

    def __init__(self):
        self.written: list[str] = []
        self.write_calls = 0

    def write(self, text: str) -> None:
        self.write_calls += 1
        self.written.append(text)

    def isalive(self) -> bool:
        return False

    def read(self, blocking: bool = False) -> str:
        return ""

    def set_size(self, cols: int, rows: int) -> None:
        pass

    def cancel_io(self) -> None:
        pass


class FakeWs:
    """Yields canned messages, then stops iteration."""

    def __init__(self, messages):
        self._messages = list(messages)
        self.sent: list = []
        self.closed = False

    def __iter__(self):
        return iter(self._messages)

    def send(self, data):
        self.sent.append(data)

    def close(self, *a, **k):
        self.closed = True


@pytest.fixture
def server(monkeypatch):
    srv = TerminalServer(token="test-token")
    # Bypass auth, options parsing, and real PTY spawn.
    monkeypatch.setattr(srv, "_authorized", lambda ws: True)
    monkeypatch.setattr(terminal_server, "_client_options", lambda ws: (80, 24, "cmd"))
    fake_pty = FakePty()
    monkeypatch.setattr(terminal_server, "_spawn_pty", lambda *a, **k: fake_pty)
    return srv, fake_pty


def test_large_paste_arrives_intact(server):
    """A 200KB paste must reach the PTY complete and in order."""
    srv, fake_pty = server
    payload = "x" * 200_000  # 200KB — far beyond a safe single write
    ws = FakeWs([payload])
    # _handle blocks on the ws iterator; run it and wait for completion.
    t = threading.Thread(target=srv._handle, args=(ws,), daemon=True)
    t.start()
    t.join(timeout=10)
    assert not t.is_alive(), "_handle did not finish"
    received = "".join(fake_pty.written)
    assert received == payload, f"PTY received {len(received)} of {len(payload)} chars intact"


def test_large_paste_is_chunked(server):
    """The backend must not issue a single giant pty.write()."""
    srv, fake_pty = server
    payload = "y" * 100_000
    ws = FakeWs([payload])
    t = threading.Thread(target=srv._handle, args=(ws,), daemon=True)
    t.start()
    t.join(timeout=10)
    assert not t.is_alive(), "_handle did not finish"
    assert fake_pty.write_calls > 1, "expected chunked pty.write() calls"
    for chunk in fake_pty.written:
        assert len(chunk) <= 8192, f"chunk of {len(chunk)} exceeds 8KB limit"
    assert "".join(fake_pty.written) == payload
