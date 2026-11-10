"""Hub slice 4: the retired tools are REMOVED, not hidden.

HQ, Workflows, Deep Researcher and Deck Studio were hidden from every UI
surface behind the ``show_retired_tools`` setting (2026-10-22) but stayed
fully built in. Slice 4 removes them entirely: no code, routes, tiles or
settings references remain. These tests pin the removal — a regression here
means a retired surface came back.

Deliberately KEPT (not retired tools — do not "fix" by deleting them):
- LuckyD Chat (/chat) — now the live Hub chat (Hub slices 2-3).
- features/deep_research + tools/deep_research_tool — the LuckyD agent's
  ``deepresearch`` tool; only the browser page (/research) was retired.
- harness_bridge / the coding-agent backend — the AI sidebar's harness mode
  uses it; only the /hq gateway view was retired.
"""

from __future__ import annotations

import http.client
import importlib.util
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
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

from browser_core.dashboard import dashboard_html
from browser_core.settings import DEFAULTS


def test_show_retired_tools_setting_is_gone() -> None:
    assert "show_retired_tools" not in DEFAULTS
    for rel in (
        "browser_core/settings.py",
        "browser_core/dashboard.py",
        "browser_core/tile_registry.py",
        "browser_core/control_server.py",
        "browser_ui/dialogs.py",
        "browser_ui/main_window.py",
        "browser_ui/palette.py",
    ):
        src = (_BROWSER_DIR / rel).read_text(encoding="utf-8")
        assert "show_retired_tools" not in src, rel
        assert "RETIRED_TILE" not in src, rel
        assert "RETIRED_APP" not in src, rel


def test_retired_modules_and_payload_are_gone() -> None:
    for mod in (
        "browser_core.workflows",
        "browser_core.research_page",
        "browser_core.scheduler",
    ):
        assert importlib.util.find_spec(mod) is None, mod
    assert not (_BROWSER_DIR / "studio").exists(), "Deck Studio payload still present"


def test_dashboard_has_no_retired_surfaces() -> None:
    html = dashboard_html()
    for gone in (
        "Agent HQ",
        "Deep Research",
        "Workflows",
        "Deck Studio",
        "RETIRED_APPS",
        "/hq",
        "/research",
        "/workflows",
    ):
        assert gone not in html, gone
    # The live Hub chat tile stays.
    assert "LuckyD Chat" in html and "/chat" in html


def test_retired_routes_are_404_and_chat_stays_live() -> None:
    from browser_core.control_server import make_handler

    class _Backend:
        def status(self):
            return {"ok": True}

    handler = make_handler(_Backend(), token="")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:

        def _req(method, path, body=None):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            payload = json.dumps(body or {}) if body is not None else None
            headers = {"Content-Type": "application/json"} if payload else {}
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            conn.close()
            return resp.status, data

        for path in (
            "/hq",
            "/workflows",
            "/workflows/list",
            "/schedules",
            "/research",
            "/research/status",
            "/research/runs",
            "/research/run",
        ):
            status, _ = _req("GET", path)
            assert status == 404, f"GET {path} came back: {status}"
        for path in (
            "/workflow/record",
            "/workflow/stop",
            "/workflow/replay",
            "/workflow/delete",
            "/workflow/generate",
            "/schedule",
            "/research/start",
            "/research/cancel",
        ):
            status, _ = _req("POST", path, {"name": "x", "query": "x"})
            assert status == 404, f"POST {path} came back: {status}"
        status, _ = _req("GET", "/chat")
        assert status == 200, f"/chat (live Hub chat) came back: {status}"
        status, _ = _req("GET", "/dashboard")
        assert status == 200
    finally:
        srv.shutdown()
        t.join(timeout=5)


def test_retired_open_handlers_are_gone_from_main_window() -> None:
    src = (_BROWSER_DIR / "browser_ui" / "main_window.py").read_text(encoding="utf-8")
    for gone in ("def open_hq", "def open_workflows", "def open_deep_research"):
        assert gone not in src, gone
    assert "def open_luckyd_chat" in src  # Hub chat stays


def test_kept_surfaces_still_present() -> None:
    # The agent's deepresearch tool and its engine were never retired.
    assert importlib.util.find_spec("features.deep_research") is not None
    assert importlib.util.find_spec("tools.deep_research_tool") is not None
    # The harness backend (sidebar harness mode, /status) was never retired.
    assert importlib.util.find_spec("browser_core.harness_bridge") is not None
    # The live Hub chat page.
    assert importlib.util.find_spec("browser_core.chat_page") is not None
    from browser_core import tile_registry

    assert not hasattr(tile_registry, "RETIRED_TILE_IDS")
    assert not hasattr(tile_registry, "_show_retired_tools")
