"""Slice 2 — one-tap provider switching on the Hub Chat page.

Covers the pure provider logic (current / pin / validate / persist),
the provider passthrough on /chat/send, and the control-server surface:
``providers`` on /chat/state, POST /chat/provider, and the picker on
the Chat page. Mirrors the Qt stubs + live-server harness from
test_hub_status.py (PySide6 is not installed here).
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

from browser_core import hub_providers as hp
from browser_core import luckyd_services as ls


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


def _fake_backend():
    return SimpleNamespace(
        ai_info=lambda: {
            "ai_providers": ["ollama", "google"],
            "ai_default": "ollama",
            "ai_local_status": {"ollama": "ok"},
        }
    )


def _serve_ctl(settings, monkeypatch, backend=None):
    """Live control server (loopback) with stubbed Hub/Claw upstreams."""
    import browser_core.control_server as cs

    calls: list = []
    routes = [
        ("18790/healthz", 200, {"ok": True, "ollama": True}),
        ("18791/healthz", 200, {"ok": True, "enabled": True, "projects": ["browser"]}),
        ("18790/chat", 200, {"ok": True, "text": "hi", "via": "luckyd", "model": "m"}),
    ]
    monkeypatch.setattr(urllib.request, "urlopen", _opener(routes, calls))
    handler = cs.make_handler(
        backend if backend is not None else _fake_backend(), token="", settings=settings
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, f"127.0.0.1:{httpd.server_address[1]}", calls


# ── pure provider logic ────────────────────────────────────────────────


def test_current_provider_defaults_to_auto() -> None:
    assert hp.current_provider(_settings()) == "auto"
    assert hp.current_provider(_settings(ai_provider="")) == "auto"
    assert hp.current_provider(_settings(ai_provider="google")) == "google"


def test_pin_provider_persists_and_auto_clears() -> None:
    store = _Store()
    assert hp.pin_provider(store, "google", ["ollama", "google"]) == {
        "state": "ok",
        "current": "google",
    }
    assert store.get("ai_provider") == "google"
    assert store.saved == 1
    assert hp.pin_provider(store, "auto", ["ollama", "google"]) == {
        "state": "ok",
        "current": "auto",
    }
    assert store.get("ai_provider") == ""
    assert store.saved == 2


def test_pin_provider_rejects_unknown_and_saves_nothing() -> None:
    store = _Store(ai_provider="ollama")
    out = hp.pin_provider(store, "nope", ["ollama", "google"])
    assert out["state"] == "error"
    assert "nope" in out["error"]
    assert store.get("ai_provider") == "ollama"
    assert store.saved == 0


def test_pin_provider_without_allowlist_accepts_any_name() -> None:
    store = _Store()
    assert hp.pin_provider(store, "groq")["current"] == "groq"
    assert store.get("ai_provider") == "groq"


def test_provider_state_from_backend_ai_info() -> None:
    state = hp.provider_state(_settings(ai_provider="google"), _fake_backend())
    assert state["current"] == "google"
    assert state["available"] == ["ollama", "google"]
    assert state["default"] == "ollama"
    assert state["local_status"] == {"ollama": "ok"}


def test_provider_state_stale_pin_falls_back_to_auto() -> None:
    """A pin for a provider the backend no longer offers (dead Cline
    dropped from the usable list) is reported as Auto — the pin itself
    is only cleared by picking Auto or another provider."""
    state = hp.provider_state(_settings(ai_provider="cline-usage"), _fake_backend())
    assert state["current"] == "auto"
    assert state["available"] == ["ollama", "google"]
    assert state["default"] == "ollama"


def test_pin_provider_rejects_dead_cline() -> None:
    """Pinning cline-usage against a usable list that no longer contains
    it is rejected and saves nothing — the switcher can never make a
    dead Cline current."""
    store = _Store(ai_provider="ollama")
    out = hp.pin_provider(store, "cline-usage", ["ollama", "google"])
    assert out["state"] == "error"
    assert "cline-usage" in out["error"]
    assert store.get("ai_provider") == "ollama"
    assert store.saved == 0


def test_provider_state_without_ai_info_never_raises() -> None:
    state = hp.provider_state(_settings(), SimpleNamespace())
    assert state == {"current": "auto", "available": [], "default": None, "local_status": {}}


# ── hub_chat provider passthrough ──────────────────────────────────────


def test_hub_chat_passes_pinned_provider_and_omits_auto() -> None:
    st = _settings(hub_token="t")
    seen: list = []

    def op(req, timeout=None):
        seen.append(json.loads(req.data.decode()))
        return _FakeResp(200, {"ok": True, "text": "hi", "via": "luckyd", "model": "m"})

    assert (
        ls.hub_chat(st, [{"role": "user", "content": "hi"}], op, provider="google")["state"] == "ok"
    )
    assert seen[-1]["provider"] == "google"
    assert (
        ls.hub_chat(st, [{"role": "user", "content": "hi"}], op, provider="auto")["state"] == "ok"
    )
    assert "provider" not in seen[-1]
    assert ls.hub_chat(st, [{"role": "user", "content": "hi"}], op)["state"] == "ok"
    assert "provider" not in seen[-1]


# ── control-server surface ─────────────────────────────────────────────


def test_chat_state_includes_providers(monkeypatch) -> None:
    settings = _Store(hub_token="t", claw_token="c")
    httpd, base, _calls = _serve_ctl(settings, monkeypatch)
    try:
        status, raw = _req(base, "GET", "/chat/state")
        state = json.loads(raw)
        assert status == 200
        assert state["providers"]["current"] == "auto"
        assert state["providers"]["available"] == ["ollama", "google"]
        assert state["providers"]["default"] == "ollama"
        # Slice 1 shape is unchanged.
        assert state["components"]["hub"]["state"] == "ok"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_chat_provider_pins_and_persists(monkeypatch) -> None:
    settings = _Store(hub_token="t", claw_token="c")
    httpd, base, _calls = _serve_ctl(settings, monkeypatch)
    try:
        status, raw = _req(base, "POST", "/chat/provider", {"provider": "google"})
        body = json.loads(raw)
        assert status == 200
        assert body["state"] == "ok"
        assert body["providers"]["current"] == "google"
        assert settings.get("ai_provider") == "google"
        assert settings.saved == 1
        # The pin shows up on the next state read (survives the round trip).
        _status, raw = _req(base, "GET", "/chat/state")
        assert json.loads(raw)["providers"]["current"] == "google"
        # Auto clears it again.
        status, raw = _req(base, "POST", "/chat/provider", {"provider": "auto"})
        assert status == 200
        assert json.loads(raw)["providers"]["current"] == "auto"
        assert settings.get("ai_provider") == ""
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_chat_provider_rejects_unknown(monkeypatch) -> None:
    settings = _Store(hub_token="t", claw_token="c")
    httpd, base, _calls = _serve_ctl(settings, monkeypatch)
    try:
        status, raw = _req(base, "POST", "/chat/provider", {"provider": "nope"})
        body = json.loads(raw)
        assert status == 400
        assert body["state"] == "error"
        assert settings.get("ai_provider") is None
        assert settings.saved == 0
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_chat_send_uses_pinned_provider(monkeypatch) -> None:
    settings = _Store(hub_token="t", claw_token="c", ai_provider="google")
    httpd, base, calls = _serve_ctl(settings, monkeypatch)
    try:
        status, raw = _req(
            base, "POST", "/chat/send", {"messages": [{"role": "user", "content": "hi"}]}
        )
        assert status == 200
        assert json.loads(raw)["state"] == "ok"
        chat_calls = [c for c in calls if "18790/chat" in c.full_url]
        assert len(chat_calls) == 1
        assert json.loads(chat_calls[0].data.decode())["provider"] == "google"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_chat_page_has_provider_picker() -> None:
    from browser_core.chat_page import chat_html

    page = chat_html(_settings())
    assert 'id="provider-select"' in page
    assert "/chat/provider" in page
    assert "renderProviders" in page


def test_chat_provider_rejects_dead_cline(monkeypatch) -> None:
    """End to end: the Hub offers only [ollama, google], so pinning a
    dead cline-usage through the live switcher endpoint is a 400 and
    nothing is persisted."""
    settings = _Store(hub_token="x", claw_token="y")
    httpd, base, _calls = _serve_ctl(settings, monkeypatch)
    try:
        status, raw = _req(base, "POST", "/chat/provider", {"provider": "cline-usage"})
        body = json.loads(raw)
        assert status == 400
        assert body["state"] == "error"
        assert "cline-usage" in body["error"]
        assert settings.get("ai_provider") is None
        assert settings.saved == 0
    finally:
        httpd.shutdown()
        httpd.server_close()
