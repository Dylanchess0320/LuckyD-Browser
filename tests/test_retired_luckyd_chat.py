"""LuckyD Chat retirement (2026-10-22, Dylan: "retire luckyd chat app in
luckyd browser for now"): Feature 13's surfaces are hidden behind the
same ``show_retired_tools`` switch that retired HQ / Workflows / Deep
Researcher / Deck Studio in pass 1 — code, routes and stored tokens
all stay in place, and flipping the switch brings everything back.

Pass-1 parity is the spec: tiles and menu entries are filtered, the
Settings → Connections tab only joins the dialog while the switch is
on, and the control-server routes themselves are deliberately NOT
gated (exactly like /hq, /workflows and /research, whose routes still
serve while retired — see test_retired_tools.py).

Qt-free on purpose: the suite runs against mocked/absent Qt. The real
widget-level proof (Tools menu + Settings tabs, both switch states)
lives in test_retired_luckyd_chat_qt.py, which needs real PySide6.
"""

from __future__ import annotations

import http.client
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from browser_core.dashboard import dashboard_html
from browser_core.tile_registry import CONFIG_PATH


def _fake_settings(**overrides):
    vals = {"theme": "neon", **overrides}
    return SimpleNamespace(get=lambda k, d=None: vals.get(k, d))


def _src(rel: str) -> str:
    return (_BROWSER_DIR / rel).read_text(encoding="utf-8")


# ── homepage tile ─────────────────────────────────────────────────────


def test_dashboard_hides_chat_tile_by_default() -> None:
    html = dashboard_html()
    assert '"LuckyD Chat"' in html  # named in the retired-apps filter…
    assert '["Agent HQ", "Deep Research", "Workflows", "LuckyD Chat"]' in html
    # …and the built-in app entry itself is still in the page source
    # (hidden client-side by the filter, not deleted).
    assert "'LuckyD Chat', '/chat'" in html


def test_dashboard_restores_chat_tile_when_switch_on() -> None:
    html = dashboard_html(_fake_settings(show_retired_tools=True))
    assert "const RETIRED_APPS = [];" in html
    assert "'LuckyD Chat', '/chat'" in html


def test_chat_tile_ids_in_sync_between_dashboard_and_registry() -> None:
    from browser_core import dashboard, tile_registry

    assert dashboard.RETIRED_TILE_IDS == tile_registry.RETIRED_TILE_IDS
    assert "luckyd-chat" in dashboard.RETIRED_TILE_IDS
    assert "LuckyD Chat" in dashboard.RETIRED_APP_NAMES


def test_shipped_chat_tile_config_untouched() -> None:
    """The registry entry stays configured exactly as shipped — the
    switch hides it at the consumers, never by editing registry state."""
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    entry = next(t for t in raw["tiles"] if t["id"] == "luckyd-chat")
    assert entry["name"] == "LuckyD Chat"
    assert entry["url"] == "http://127.0.0.1:8771/"


def test_ensure_autostart_skips_chat_tile_unless_switch_on(monkeypatch) -> None:
    from browser_core import tile_registry

    # url is a non-HTTP placeholder (like the deck-studio test): the
    # shipped http://127.0.0.1:8771/ may be served in some environments,
    # and ensure_autostart rightly refuses to double-launch a live tile.
    tile = tile_registry.Tile(
        id="luckyd-chat",
        name="LuckyD Chat",
        icon="",
        url="lucky://down",
        autostart=True,
        command=(sys.executable, "-c", "pass"),
    )
    monkeypatch.setattr(tile_registry, "load_tiles", lambda *a, **k: [tile])
    tile_registry._last_attempt.pop("luckyd-chat", None)
    tile_registry._launched.pop("luckyd-chat", None)

    monkeypatch.setattr(tile_registry, "_show_retired_tools", lambda: False)
    tile_registry.ensure_autostart()
    assert "luckyd-chat" not in tile_registry._launched

    monkeypatch.setattr(tile_registry, "_show_retired_tools", lambda: True)
    tile_registry.ensure_autostart()
    try:
        assert "luckyd-chat" in tile_registry._launched
    finally:
        tile_registry.shutdown_autostart()
        tile_registry._launched.pop("luckyd-chat", None)
        tile_registry._last_attempt.pop("luckyd-chat", None)


# ── menu + settings surfaces (source-level, the pass-1 test style) ────


def test_tools_menu_entry_gated_but_handler_kept() -> None:
    mw = _src("browser_ui/main_window.py")
    gated = (
        "if self._show_retired_tools():\n"
        '            self._add(tools_menu, "LuckyD Chat", self.open_luckyd_chat)'
    )
    assert gated in mw
    # Hiding is UI-only: the handler itself is NOT removed.
    assert "def open_luckyd_chat" in mw


def test_connections_tab_gated_but_fields_and_saves_kept() -> None:
    dialogs = _src("browser_ui/dialogs.py")
    gated = (
        'if bool(settings.get("show_retired_tools", False)):\n'
        '            tabs.addTab(_scrollable(connections), "Connections")'
    )
    assert gated in dialogs
    # The fields are still built and still saved on accept(), so stored
    # tokens/addresses survive with the tab hidden (never deleted).
    for field in ("hub_url", "hub_token", "claw_url", "claw_token"):
        assert f"self.{field}" in dialogs
        assert f'set("{field}", self.{field}.text().strip())' in dialogs


# ── routes: pass-1 parity (deliberately not gated) ────────────────────


def _serve(settings):
    import browser_core.control_server as cs

    handler = cs.make_handler(SimpleNamespace(), token="", settings=settings)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, f"127.0.0.1:{httpd.server_address[1]}"


def _get(base, path):
    conn = http.client.HTTPConnection(base, timeout=10)
    conn.request("GET", path)
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8", "replace")
    conn.close()
    return resp.status, raw


def test_chat_modules_still_present_and_importable() -> None:
    from browser_core import chat_page, luckyd_services  # noqa: F401

    assert (_BROWSER_DIR / "browser_core" / "chat_page.py").is_file()
    assert (_BROWSER_DIR / "browser_core" / "luckyd_services.py").is_file()


def test_chat_route_serves_in_both_switch_states() -> None:
    """Pass-1 parity: retired tools' routes (/hq, /workflows, /research)
    are deliberately untouched, so /chat keeps serving too — retirement
    removes every UI path to it, not the route. With the switch on it
    must behave exactly as before (Feature 13's own end-to-end suite in
    test_pass3_fixes.py covers the full route set)."""
    for settings in (_fake_settings(), _fake_settings(show_retired_tools=True)):
        httpd, base = _serve(settings)
        try:
            status, page = _get(base, "/chat")
            assert status == 200 and "LuckyD Chat" in page
        finally:
            httpd.shutdown()
            httpd.server_close()
