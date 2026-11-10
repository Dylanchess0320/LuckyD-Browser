"""Hub slice 8 — one-tap provider switching inside the terminal.

Covers the terminal half of the shared ``ai_provider`` pick (slice 2
pinned it from the Chat page): the provider the terminal spawns an
Agent PTY with, the picker on the terminal page, and the switch
round-trip the picker performs (POST /chat/provider, < 5s, persisted
so it survives restart). The POST/GET persistence itself is covered
by test_hub_providers.py; these tests cover what slice 8 adds.
"""

from __future__ import annotations

import http.client
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
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
# Append (never insert at 0): browser/main.py must not shadow the repo-root
# main.py (the CLI) that test_browser_integrations imports.
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))

import browser_core.terminal_server as ts
from browser_core.terminal_page import terminal_html
from browser_core.terminal_server import (
    TERMINAL_PROVIDER_ENV,
    TerminalServer,
    _spawn_pty,
    terminal_provider,
)

TOKEN = "slice8-test-token"


class _Store:
    """Settings stand-in with get/set/save, recording persistence."""

    def __init__(self, **values):
        self._data = dict(values)
        self.saved = 0

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value

    def save(self):
        self.saved += 1
        return True


class _BrokenSettings:
    def get(self, key, default=None):
        raise RuntimeError("settings unreadable")


# ── terminal_provider: the shared pick, read at spawn time ───────────


def test_terminal_provider_returns_pin() -> None:
    assert terminal_provider(_Store(ai_provider="ollama")) == "ollama"
    assert terminal_provider(_Store(ai_provider="google")) == "google"


def test_terminal_provider_auto_is_empty() -> None:
    assert terminal_provider(_Store()) == ""
    assert terminal_provider(_Store(ai_provider="")) == ""
    assert terminal_provider(_Store(ai_provider="auto")) == ""
    assert terminal_provider(None) == ""
    assert terminal_provider(_BrokenSettings()) == ""


# ── _spawn_pty: the pin rides into the agent env ─────────────────────


class _FakePTYFactory:
    """Stand-in for winpty.PTY — records constructor dims and spawn() kwargs."""

    instances: list = []

    def __init__(self, cols, rows):
        self.cols = cols
        self.rows = rows
        self.spawn_kwargs = None
        _FakePTYFactory.instances.append(self)

    def spawn(self, appname, cmdline=None, cwd=None, env=None):
        self.spawn_kwargs = {
            "appname": appname,
            "cmdline": cmdline,
            "cwd": cwd,
            "env": env,
        }


def _install_fake_winpty(monkeypatch) -> None:
    _FakePTYFactory.instances.clear()
    monkeypatch.setitem(sys.modules, "winpty", SimpleNamespace(PTY=_FakePTYFactory))


def _env_parts(env_block: str) -> list[str]:
    assert env_block.endswith("\0")
    return env_block.split("\0")[:-1]


def _env_value(parts: list[str], key: str):
    for part in parts:
        if part.startswith(key + "="):
            return part[len(key) + 1 :]
    return None


def test_spawn_pty_agent_pins_provider_in_env(monkeypatch, tmp_path) -> None:
    _install_fake_winpty(monkeypatch)
    exe = tmp_path / "cli.exe"
    exe.touch()
    _spawn_pty(shell="agent", cli_path=str(exe), provider="ollama")
    parts = _env_parts(_FakePTYFactory.instances[-1].spawn_kwargs["env"])
    assert _env_value(parts, TERMINAL_PROVIDER_ENV) == "ollama"


def test_spawn_pty_agent2_pins_provider_in_env(monkeypatch, tmp_path) -> None:
    _install_fake_winpty(monkeypatch)
    script = tmp_path / "main.py"
    script.write_text("# agent2")
    _spawn_pty(shell="agent2", cli2_path=str(script), provider="google")
    parts = _env_parts(_FakePTYFactory.instances[-1].spawn_kwargs["env"])
    assert _env_value(parts, TERMINAL_PROVIDER_ENV) == "google"


def test_spawn_pty_auto_clears_inherited_override(monkeypatch, tmp_path) -> None:
    # A stale CODING_AGENT_PROVIDER in the browser process env must not
    # leak into an Auto agent session — Auto means mirror/auto logic.
    _install_fake_winpty(monkeypatch)
    monkeypatch.setenv(TERMINAL_PROVIDER_ENV, "stale-provider")
    exe = tmp_path / "cli.exe"
    exe.touch()
    _spawn_pty(shell="agent", cli_path=str(exe), provider="")
    parts = _env_parts(_FakePTYFactory.instances[-1].spawn_kwargs["env"])
    assert _env_value(parts, TERMINAL_PROVIDER_ENV) is None
    _spawn_pty(shell="agent", cli_path=str(exe), provider="auto")
    parts = _env_parts(_FakePTYFactory.instances[-1].spawn_kwargs["env"])
    assert _env_value(parts, TERMINAL_PROVIDER_ENV) is None


def test_spawn_pty_system_shell_never_gets_provider(monkeypatch) -> None:
    _install_fake_winpty(monkeypatch)
    _spawn_pty(shell="powershell", provider="ollama")
    parts = _env_parts(_FakePTYFactory.instances[-1].spawn_kwargs["env"])
    assert _env_value(parts, TERMINAL_PROVIDER_ENV) is None
    _spawn_pty(shell="cmd", provider="ollama")
    parts = _env_parts(_FakePTYFactory.instances[-1].spawn_kwargs["env"])
    assert _env_value(parts, TERMINAL_PROVIDER_ENV) is None


def test_spawn_pty_no_provider_arg_keeps_old_behavior(monkeypatch, tmp_path) -> None:
    # TerminalServer without settings (and every pre-slice-8 caller)
    # spawns exactly as before: no override is injected.
    _install_fake_winpty(monkeypatch)
    monkeypatch.delenv(TERMINAL_PROVIDER_ENV, raising=False)
    exe = tmp_path / "cli.exe"
    exe.touch()
    _spawn_pty(shell="agent", cli_path=str(exe))
    parts = _env_parts(_FakePTYFactory.instances[-1].spawn_kwargs["env"])
    assert _env_value(parts, TERMINAL_PROVIDER_ENV) is None


# ── TerminalServer: settings-derived provider reaches the spawn ─────


class _FakePTY:
    def __init__(self):
        self.written = []
        self.sizes = []

    def isalive(self):
        return False

    def read(self, blocking=False):
        return ""

    def write(self, text):
        self.written.append(text)

    def set_size(self, cols, rows):
        self.sizes.append((cols, rows))

    def cancel_io(self):
        pass


def _fake_ws(path="", cookie=""):
    class _WS:
        def __init__(self):
            self.request = SimpleNamespace(
                path=path,
                headers={"Cookie": f"{ts.TERM_COOKIE}={cookie}"} if cookie else {},
            )
            self.sent = []
            self.closed = []

        def send(self, msg):
            self.sent.append(msg)

        def close(self, **kw):
            self.closed.append(kw)

    return _WS()


def test_handle_spawns_agent_with_shared_pick(monkeypatch) -> None:
    settings = _Store(ai_provider="ollama")
    srv = TerminalServer(token=TOKEN, settings=settings)
    ws = _fake_ws(path="/?cols=80&rows=24&shell=agent", cookie=TOKEN)

    class _IterWS(type(ws)):
        def __iter__(self):
            return iter([])

    itws = _IterWS()
    itws.request = ws.request
    seen: dict = {}

    def _capture(*args, **kwargs):
        seen.update(kwargs)
        return _FakePTY()

    monkeypatch.setattr(ts, "_spawn_pty", _capture)
    srv._handle(itws)
    assert seen.get("provider") == "ollama"
    assert seen.get("shell") == "agent"


def test_handle_without_settings_spawns_unpinned(monkeypatch) -> None:
    srv = TerminalServer(token=TOKEN)
    ws = _fake_ws(path="/?cols=80&rows=24&shell=agent", cookie=TOKEN)

    class _IterWS(type(ws)):
        def __iter__(self):
            return iter([])

    itws = _IterWS()
    itws.request = ws.request
    seen: dict = {}

    def _capture(*args, **kwargs):
        seen.update(kwargs)
        return _FakePTY()

    monkeypatch.setattr(ts, "_spawn_pty", _capture)
    srv._handle(itws)
    assert seen.get("provider") == ""


# ── terminal page: the picker ─────────────────────────────────────────


def test_terminal_html_has_provider_picker() -> None:
    page = terminal_html()
    assert 'id="provider-select"' in page
    assert "/chat/state" in page  # picker lists what state reports
    assert "/chat/provider" in page  # one change pins the shared pick
    assert "switched to" in page
    assert "reconnect();" in page  # agent session respawns on the new pick
    # Non-agent shells keep their session; the pick applies next time.
    assert "applies to new Agent sessions" in page


# ── live control server: the picker's switch round-trip ──────────────


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


def _fake_backend():
    return SimpleNamespace(
        ai_info=lambda: {
            "ai_providers": ["ollama", "google"],
            "ai_default": "ollama",
            "ai_local_status": {"ollama": "ok"},
        }
    )


def _serve_ctl(settings, monkeypatch):
    import browser_core.control_server as cs

    calls: list = []
    routes = [
        ("18790/healthz", 200, {"ok": True, "ollama": True}),
        ("18791/healthz", 200, {"ok": True, "enabled": True, "projects": ["browser"]}),
    ]
    monkeypatch.setattr(urllib.request, "urlopen", _opener(routes, calls))
    handler = cs.make_handler(_fake_backend(), token="", settings=settings)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, f"127.0.0.1:{httpd.server_address[1]}"


def test_terminal_switch_round_trip_under_five_seconds(monkeypatch) -> None:
    """The exact round-trip the terminal picker performs: POST the pin,
    re-read state, and confirm the pin persisted (survives restart) —
    all inside the 5-second switch budget."""
    settings = _Store(hub_token="<redacted>", claw_token="<redacted>")
    httpd, base = _serve_ctl(settings, monkeypatch)
    try:
        started = time.monotonic()
        status, raw = _req(base, "POST", "/chat/provider", {"provider": "google"})
        assert status == 200
        assert json.loads(raw)["providers"]["current"] == "google"
        status, raw = _req(base, "GET", "/chat/state")
        assert status == 200
        assert json.loads(raw)["providers"]["current"] == "google"
        assert settings.get("ai_provider") == "google"
        assert settings.saved == 1
        assert time.monotonic() - started < 5.0
    finally:
        httpd.shutdown()
        httpd.server_close()
