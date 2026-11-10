"""Slice 5 — growth watch into a proactive Hub card.

Covers the CardStore (add/dismiss/cap/corrupt-file tolerance), the
watch-change → card mapping (real check.py change shapes), the
control-server surface (cards on /chat/state, GET/POST /chat/cards,
POST /chat/cards/dismiss), the Chat page markers, and the adapter
(no-op on empty changes, one POST per real change, exit codes).
Mirrors the Qt stubs + live-server harness from test_hub_memory.py
(PySide6 is not installed here).
"""

from __future__ import annotations

import http.client
import importlib.util
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

from browser_core import hub_cards as hc


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


def _serve_ctl(settings, monkeypatch, cards_path):
    """Live control server (loopback) with stubbed Hub/Claw upstreams."""
    import browser_core.control_server as cs
    import browser_core.hub_cards as hub_cards

    monkeypatch.setattr(hub_cards, "CARDS_PATH", Path(cards_path))
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


def test_add_current_and_count(tmp_path) -> None:
    store = hc.CardStore(tmp_path / "c.json")
    assert store.current() == []
    assert store.count() == 0
    out = store.add("github_stars", "⭐ New GitHub star — 4 → 5", "New stargazer: ada")
    assert out["state"] == "ok"
    card = out["card"]
    assert card["kind"] == "github_stars"
    assert card["dismissed"] is False
    assert isinstance(card["created_at"], float)
    assert card["id"]
    store.add("discord_members", "👥 Discord members — 12 → 13")
    assert store.count() == 2
    assert [c["kind"] for c in store.current()] == ["github_stars", "discord_members"]
    # A fresh store on the same file sees the same cards (survives restart).
    assert hc.CardStore(tmp_path / "c.json").count() == 2


def test_add_rejects_unknown_kind_and_empty_title(tmp_path) -> None:
    store = hc.CardStore(tmp_path / "c.json")
    assert store.add("youtube_subs", "subs up")["state"] == "error"
    assert store.add("github_stars", "   ")["state"] == "error"
    assert store.count() == 0


def test_dismiss_hides_card_and_unknown_id_errors(tmp_path) -> None:
    store = hc.CardStore(tmp_path / "c.json")
    card = store.add("github_stars", "star")["card"]
    assert store.dismiss("")["state"] == "error"
    assert store.dismiss("nope")["state"] == "error"
    assert store.dismiss(card["id"])["state"] == "ok"
    assert store.current() == []
    assert store.count() == 0
    # Dismissing again is still ok, and the card stays gone.
    assert store.dismiss(card["id"])["state"] == "ok"
    assert store.count() == 0
    # The dismissed card is still in the log (audit), just not current.
    assert len(store.list()) == 1
    assert store.list()[0]["dismissed"] is True


def test_store_caps_cards_and_tolerates_corrupt_file(tmp_path) -> None:
    path = tmp_path / "c.json"
    store = hc.CardStore(path)
    for i in range(25):
        assert store.add("github_stars", f"star {i}")["state"] == "ok"
    assert len(store.list()) == hc.MAX_CARDS
    assert store.list(1)[0]["title"] == "star 24"
    path.write_text("{not json", encoding="utf-8")
    assert hc.CardStore(path).current() == []
    assert hc.CardStore(path).count() == 0


def test_cards_state_never_raises(tmp_path) -> None:
    store = hc.CardStore(tmp_path / "c.json")
    store.add("discord_members", "members up")
    state = hc.cards_state(store)
    assert state["count"] == 1
    assert state["cards"][0]["kind"] == "discord_members"


# ── watch-change mapping ───────────────────────────────────────────────
# Fixtures use the REAL check.py change shapes (verified 2026-11-10:
# {"type": "github_stars", "from": 4, "to": 5, "new_stargazers": [...]}
# and {"type": "discord_members", "from": 12, "to": 13}).


def test_card_from_github_stars_change() -> None:
    built = hc.card_from_watch_change(
        {"type": "github_stars", "from": 4, "to": 5, "new_stargazers": ["ada"]}
    )
    assert built["state"] == "ok"
    assert built["kind"] == "github_stars"
    assert "4 → 5" in built["title"]
    assert "ada" in built["detail"]
    multi = hc.card_from_watch_change(
        {"type": "github_stars", "from": 4, "to": 7, "new_stargazers": ["a", "b", "c"]}
    )
    assert "stars" in multi["title"]  # plural for a multi-star jump
    assert "a, b, c" in multi["detail"]


def test_card_from_discord_members_change() -> None:
    built = hc.card_from_watch_change({"type": "discord_members", "from": 12, "to": 13})
    assert built["state"] == "ok"
    assert built["kind"] == "discord_members"
    assert "12 → 13" in built["title"]
    assert "+1" in built["detail"]


def test_card_from_watch_change_rejects_bad_input() -> None:
    assert (
        hc.card_from_watch_change({"type": "youtube_subs", "from": 1, "to": 2})["state"] == "error"
    )
    assert hc.card_from_watch_change({"type": "github_stars", "from": 4})["state"] == "error"
    assert (
        hc.card_from_watch_change({"type": "github_stars", "from": "4", "to": 5})["state"]
        == "error"
    )
    assert (
        hc.card_from_watch_change(
            {"type": "github_stars", "from": 4, "to": 5, "new_stargazers": "ada"}
        )["state"]
        == "error"
    )
    assert hc.card_from_watch_change("not a dict")["state"] == "error"
    assert hc.card_from_watch_change({})["state"] == "error"


# ── control-server surface ─────────────────────────────────────────────


def test_chat_state_includes_cards(monkeypatch, tmp_path) -> None:
    settings = _settings(hub_token="t", claw_token="c")
    httpd, base = _serve_ctl(settings, monkeypatch, tmp_path / "c.json")
    try:
        status, raw = _req(base, "GET", "/chat/state")
        state = json.loads(raw)
        assert status == 200
        assert state["cards"] == {"cards": [], "count": 0}
        # Slice 1-3 shape is unchanged.
        assert state["components"]["hub"]["state"] == "ok"
        assert state["handoff"] == {"latest": None, "count": 0}
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_chat_cards_post_and_read(monkeypatch, tmp_path) -> None:
    settings = _settings(hub_token="t", claw_token="c")
    path = tmp_path / "c.json"
    httpd, base = _serve_ctl(settings, monkeypatch, path)
    try:
        status, raw = _req(
            base,
            "POST",
            "/chat/cards",
            {"type": "github_stars", "from": 4, "to": 5, "new_stargazers": ["ada"]},
        )
        body = json.loads(raw)
        assert status == 200
        assert body["state"] == "ok"
        assert body["card"]["kind"] == "github_stars"
        assert "ada" in body["card"]["detail"]
        status, raw = _req(
            base, "POST", "/chat/cards", {"type": "discord_members", "from": 12, "to": 13}
        )
        assert status == 200
        # Both cards show up on /chat/state and GET /chat/cards.
        _status, raw = _req(base, "GET", "/chat/state")
        cards = json.loads(raw)["cards"]
        assert cards["count"] == 2
        assert [c["kind"] for c in cards["cards"]] == ["github_stars", "discord_members"]
        _status, raw = _req(base, "GET", "/chat/cards")
        listed = json.loads(raw)
        assert listed["count"] == 2
        # Persisted on disk: a fresh store sees them.
        assert hc.CardStore(path).count() == 2
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_chat_cards_rejects_unknown_and_malformed(monkeypatch, tmp_path) -> None:
    settings = _settings(hub_token="t", claw_token="c")
    httpd, base = _serve_ctl(settings, monkeypatch, tmp_path / "c.json")
    try:
        for bad in (
            {"type": "youtube_subs", "from": 10, "to": 11},
            {"type": "github_stars", "from": 4},  # missing "to"
            {"type": "github_stars", "from": "4", "to": 5, "new_stargazers": ["ada"]},
            {"type": "github_stars", "from": 4, "to": 5, "new_stargazers": "ada"},
            {},
            {"type": ""},
        ):
            status, raw = _req(base, "POST", "/chat/cards", bad)
            assert status == 400, bad
            assert json.loads(raw)["state"] == "error"
        # Nothing was stored by any rejected payload.
        _status, raw = _req(base, "GET", "/chat/state")
        assert json.loads(raw)["cards"] == {"cards": [], "count": 0}
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_dismissed_card_does_not_reappear(monkeypatch, tmp_path) -> None:
    settings = _settings(hub_token="t", claw_token="c")
    httpd, base = _serve_ctl(settings, monkeypatch, tmp_path / "c.json")
    try:
        _status, raw = _req(
            base,
            "POST",
            "/chat/cards",
            {"type": "github_stars", "from": 4, "to": 5, "new_stargazers": ["ada"]},
        )
        card_id = json.loads(raw)["card"]["id"]
        # Unknown id is a 400 and changes nothing.
        status, _raw = _req(base, "POST", "/chat/cards/dismiss", {"id": "nope"})
        assert status == 400
        _status, raw = _req(base, "GET", "/chat/state")
        assert json.loads(raw)["cards"]["count"] == 1
        # Dismiss: the card leaves /chat/state and stays gone.
        status, raw = _req(base, "POST", "/chat/cards/dismiss", {"id": card_id})
        assert status == 200
        assert json.loads(raw)["state"] == "ok"
        _status, raw = _req(base, "GET", "/chat/state")
        assert json.loads(raw)["cards"] == {"cards": [], "count": 0}
        _status, raw = _req(base, "GET", "/chat/state")
        assert json.loads(raw)["cards"]["count"] == 0
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_chat_page_has_cards_surface() -> None:
    from browser_core.chat_page import chat_html

    page = chat_html(_settings())
    assert 'id="cards-state"' in page
    assert "renderCards" in page
    assert "/chat/cards" in page
    assert "/chat/cards/dismiss" in page
    assert "Dismiss" in page


# ── adapter ────────────────────────────────────────────────────────────


def _load_adapter():
    spec = importlib.util.spec_from_file_location(
        "hub_growth_watch", _REPO_ROOT / "scripts" / "hub_growth_watch.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# The REAL check.py output shape, copied verbatim from a live run
# (2026-11-10: no change that day, discord token absent → null).
_REAL_EMPTY_REPORT = {
    "changes": [],
    "current": {
        "github_stars": 4,
        "github_stargazers": ["shuen2023", "sir1usm1ke", "vejeta", "Dylanchess0320"],
        "discord_members": None,
    },
}
_REAL_CHANGE_REPORT = {
    "changes": [
        {"type": "github_stars", "from": 4, "to": 5, "new_stargazers": ["ada"]},
        {"type": "discord_members", "from": 12, "to": 13},
    ],
    "current": {
        "github_stars": 5,
        "github_stargazers": ["shuen2023", "sir1usm1ke", "vejeta", "Dylanchess0320", "ada"],
        "discord_members": 13,
    },
}


def _record_post(monkeypatch):
    """Fake urlopen that records POSTs and answers like the Hub."""
    posted: list = []

    def fake_urlopen(req, timeout=None):
        posted.append(req)
        return _FakeResp(200, {"state": "ok", "card": {"id": "x", "kind": "github_stars"}})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return posted


def test_adapter_noop_on_empty_changes(monkeypatch, tmp_path, capsys) -> None:
    mod = _load_adapter()
    report = tmp_path / "report.json"
    report.write_text(json.dumps(_REAL_EMPTY_REPORT), encoding="utf-8")
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no POST expected")),
    )
    assert mod.main(["--input", str(report), "--hub", "http://127.0.0.1:9777"]) == 0
    assert capsys.readouterr().out == ""  # silence on a no-change day


def test_adapter_noop_on_empty_changes_via_stdin(monkeypatch, capsys) -> None:
    mod = _load_adapter()
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no POST expected")),
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_REAL_EMPTY_REPORT)))
    assert mod.main(["--hub", "http://127.0.0.1:9777"]) == 0
    assert capsys.readouterr().out == ""


def test_adapter_posts_each_change(monkeypatch, tmp_path, capsys) -> None:
    mod = _load_adapter()
    report = tmp_path / "report.json"
    report.write_text(json.dumps(_REAL_CHANGE_REPORT), encoding="utf-8")
    posted = _record_post(monkeypatch)
    assert mod.main(["--input", str(report), "--hub", "http://127.0.0.1:9777"]) == 0
    assert len(posted) == 2
    assert posted[0].full_url == "http://127.0.0.1:9777/chat/cards"
    assert json.loads(posted[0].data.decode()) == _REAL_CHANGE_REPORT["changes"][0]
    assert json.loads(posted[1].data.decode()) == _REAL_CHANGE_REPORT["changes"][1]
    assert "posted 2 card(s)" in capsys.readouterr().out


def test_adapter_runs_check_script(monkeypatch, tmp_path) -> None:
    mod = _load_adapter()
    fake_check = tmp_path / "check.py"
    fake_check.write_text(
        "import json; print(json.dumps(" + json.dumps(_REAL_CHANGE_REPORT) + "))",
        encoding="utf-8",
    )
    posted = _record_post(monkeypatch)
    assert mod.main(["--check-script", str(fake_check), "--hub", "http://127.0.0.1:9777"]) == 0
    assert len(posted) == 2


def test_adapter_malformed_report_and_unreachable_hub(monkeypatch, tmp_path) -> None:
    mod = _load_adapter()
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    assert mod.main(["--input", str(bad)]) == 2
    good = tmp_path / "good.json"
    good.write_text(json.dumps(_REAL_CHANGE_REPORT), encoding="utf-8")
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("connection refused")),
    )
    assert mod.main(["--input", str(good), "--hub", "http://127.0.0.1:9777"]) == 1
