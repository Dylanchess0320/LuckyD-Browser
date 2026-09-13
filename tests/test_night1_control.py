"""Night-1 browser-core audit: control_server.py — auth model, host/origin
defenses, body-size cap, route dispatch, traversal guard.

make_handler() is Qt-free; the module-level `from browser_core.agent import`
needs PySide6 mocked (agent imports Qt at top level).
"""

from __future__ import annotations

import http.client
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock

import pytest

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

from browser_core.control_server import CTL_COOKIE, make_handler

TOKEN = "night1-sentinel-token"


class _Backend:
    """Minimal backend implementing the make_handler protocol."""

    def status(self):
        return {"ok": True}

    def tabs(self):
        return [{"index": 0, "url": "https://a.com", "title": "A", "active": True}]

    def navigate(self, url, new_tab):
        return url

    def new_tab(self, url):
        return 1

    def activate_tab(self, index):
        return index

    def close_tab(self, index):
        if index == 99:
            raise RuntimeError("no such tab")
        return True

    def snapshot(self):
        return {}

    def act(self, body):
        return {}

    def eval_js(self, js):
        return None

    def screenshot(self, url):
        return ""

    def ask(self, question, provider):
        return ""


@pytest.fixture()
def server():
    handler = make_handler(_Backend(), token=TOKEN)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield port
    srv.shutdown()
    t.join(timeout=5)


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
    conn.close()
    return resp.status, resp.getheader("Content-Type", ""), data


def _authz(extra=None):
    h = {"Authorization": f"Bearer {TOKEN}"}
    if extra:
        h.update(extra)
    return h


# ── auth model ───────────────────────────────────────────────────────


def test_unauthorized_api_gets_json(server) -> None:
    status, ctype, data = _req(server, "GET", "/tabs")
    assert status == 401
    assert "application/json" in ctype
    assert json.loads(data)["error"] == "unauthorized"


def test_unauthorized_nav_help_gets_friendly_html(server) -> None:
    """/help is a nav path (in _NAV_PATHS) → browser tabs get the HTML 401."""
    status, ctype, data = _req(server, "GET", "/help")
    assert status == 401
    assert "text/html" in ctype
    assert b"Unauthorized" in data


def test_unauthorized_nav_gets_friendly_html(server) -> None:
    status, ctype, data = _req(server, "GET", "/dashboard")
    assert status == 401
    assert "text/html" in ctype
    assert b"Unauthorized" in data
    assert TOKEN.encode() not in data  # token never reflected


def test_bearer_token_grants_access(server) -> None:
    status, _, data = _req(server, "GET", "/tabs", headers=_authz())
    assert status == 200
    assert json.loads(data)["tabs"][0]["url"] == "https://a.com"


def test_session_cookie_grants_access(server) -> None:
    status, _, data = _req(server, "GET", "/tabs", headers={"Cookie": f"{CTL_COOKIE}={TOKEN}"})
    assert status == 200
    assert json.loads(data)["ok"] is True


def test_wrong_cookie_and_token_denied(server) -> None:
    s1, _, _ = _req(server, "GET", "/tabs", headers={"Cookie": f"{CTL_COOKIE}=wrong"})
    s2, _, _ = _req(server, "GET", "/tabs", headers={"Authorization": "Bearer wrong"})
    assert s1 == s2 == 401


def test_empty_token_disables_auth() -> None:
    handler = make_handler(_Backend(), token="")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        status, _, _ = _req(srv.server_address[1], "GET", "/tabs")
        assert status == 200
    finally:
        srv.shutdown()
        t.join(timeout=5)


# ── host & origin defenses ───────────────────────────────────────────


def test_forbidden_host_rejected(server) -> None:
    status, _, data = _req(server, "GET", "/tabs", headers={**_authz(), "Host": "evil.example.com"})
    assert status == 403
    assert json.loads(data)["error"] == "forbidden host"


def test_bracketed_ipv6_loopback_allowed(server) -> None:
    status, _, _ = _req(server, "GET", "/tabs", headers={**_authz(), "Host": "[::1]:9777"})
    assert status == 200


def test_cross_origin_fetch_rejected(server) -> None:
    status, _, data = _req(
        server,
        "GET",
        "/tabs",
        headers={**_authz(), "Origin": "http://evil.example.com"},
    )
    assert status == 403
    assert json.loads(data)["error"] == "forbidden origin"


def test_same_origin_fetch_allowed(server) -> None:
    status, _, _ = _req(
        server,
        "GET",
        "/tabs",
        headers={**_authz(), "Origin": f"http://127.0.0.1:{server}"},
    )
    assert status == 200


# ── route dispatch & input handling ──────────────────────────────────


def test_unknown_route_404(server) -> None:
    status, _, data = _req(server, "GET", "/nope", headers=_authz())
    assert status == 404
    assert json.loads(data)["ok"] is False


def test_navigate_requires_url(server) -> None:
    status, _ctype, _data = _req(server, "POST", "/navigate", body={}, headers=_authz())
    assert status == 400


def test_navigate_roundtrip(server) -> None:
    status, _, data = _req(
        server, "POST", "/navigate", body={"url": "https://b.com"}, headers=_authz()
    )
    assert status == 200
    assert json.loads(data)["url"] == "https://b.com"


def test_backend_error_becomes_500(server) -> None:
    status, _, data = _req(server, "POST", "/tab/close", body={"index": 99}, headers=_authz())
    assert status == 500
    assert json.loads(data)["error"] == "no such tab"


def test_oversized_body_is_ignored(server) -> None:
    """Content-Length > 1 MB must not be read into memory (OOM guard)."""
    conn = http.client.HTTPConnection("127.0.0.1", server, timeout=10)
    big = b"x" * (2 << 20)
    conn.request(
        "POST",
        "/navigate",
        body=big,
        headers={"Content-Type": "application/json", **_authz()},
    )
    resp = conn.getresponse()
    assert resp.status == 400  # body dropped → url required
    conn.close()


def test_malformed_json_body_is_ignored(server) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", server, timeout=10)
    conn.request(
        "POST", "/navigate", body=b"{oops", headers={"Content-Type": "application/json", **_authz()}
    )
    assert conn.getresponse().status == 400
    conn.close()


def test_terminal_asset_traversal_blocked(server) -> None:
    for probe in (
        "/static/terminal/../../etc/passwd",
        "/static/terminal/..%2f..%2fsecret",
        "/static/terminal/.",
    ):
        status, _ctype, data = _req(server, "GET", probe, headers=_authz())
        assert status in (403, 404), probe
        assert b"root:" not in data  # never serve /etc/passwd


def test_help_lists_routes(server) -> None:
    _status, _, data = _req(server, "GET", "/help", headers=_authz())
    routes = [r["route"] for r in json.loads(data)["routes"]]
    assert "GET  /tabs" in routes and "POST /navigate" in routes


def test_cookie_name_constant() -> None:
    assert CTL_COOKIE == "luckyd_ctl"
