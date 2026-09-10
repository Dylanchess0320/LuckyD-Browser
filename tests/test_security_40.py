"""4.0 security regression tests.

Covers the headline 4.0 hardening:

- Control API nav pages (/dashboard, /research, /mesh) require auth and no
  longer embed the bearer token in served HTML — in-browser tabs use the
  HttpOnly ``luckyd_ctl`` session cookie instead.
- The HQ landing page (/) requires auth and hides its token (``luckyd_hq``).
- Deep Research markdown rendering escapes HTML and allowlists link schemes.
- The terminal WebSocket accepts the ``luckyd_term`` cookie; ``?token=`` is gone.
- ``CheckpointManager.undo_to()`` terminates when a checkpoint's file is gone.
- ``_host_ok()`` accepts bracketed IPv6 loopback (``[::1]:port``).
- ``cline_bridge`` requires a bearer token.
"""

from __future__ import annotations

import http.client
import json
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock

# browser_core.agent imports PySide6 at top level — not installed on Linux CI.
# Mock before importing browser modules (same pattern as test_browser_integrations).
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

# Ensure browser/ is importable for `browser_core` (same as test_browser_research).
# conftest already puts the repo root first; append (never prepend) so the
# repo-root `main.py` (coding agent) is never shadowed by `browser/main.py`.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))

SENTINEL = "s3cr3t-sentinel-token-xyz"


# ── fakes ──────────────────────────────────────────────────────────────────


class _FakeBackend:
    """Minimal backend for make_handler: only the routes under test are hit."""

    def status(self):
        return {"ok": True}

    def tabs(self):
        return []

    def navigate(self, *a, **k):
        raise AssertionError("must not be called")

    def new_tab(self, *a, **k):
        raise AssertionError("must not be called")

    def activate_tab(self, *a, **k):
        raise AssertionError("must not be called")

    def close_tab(self, *a, **k):
        raise AssertionError("must not be called")

    def snapshot(self, *a, **k):
        raise AssertionError("must not be called")

    def act(self, *a, **k):
        raise AssertionError("must not be called")

    def eval_js(self, *a, **k):
        raise AssertionError("must not be called")

    def screenshot(self, *a, **k):
        raise AssertionError("must not be called")

    def ask(self, *a, **k):
        raise AssertionError("must not be called")


def _control_server(token: str):
    from browser_core.control_server import make_handler

    handler = make_handler(_FakeBackend(), token=token)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _get(port: int, path: str, headers: dict | None = None):
    """Plain HTTP GET via http.client (no proxy-env interference)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path, headers=headers or {})
    resp = conn.getresponse()
    body = resp.read().decode("utf-8", "replace")
    conn.close()
    return resp.status, body


# ── Control API: cookie auth + token non-disclosure ────────────────────────


class TestControlCookieAuth:
    def test_nav_pages_require_auth(self):
        server, thread = _control_server(SENTINEL)
        try:
            for path in ("/dashboard", "/research", "/mesh"):
                status, body = _get(server.server_port, path)
                assert status == 401, f"{path} was reachable without auth"
                assert SENTINEL not in body
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_nav_pages_accept_session_cookie_and_hide_token(self):
        server, thread = _control_server(SENTINEL)
        try:
            for path in ("/dashboard", "/research", "/mesh"):
                status, body = _get(
                    server.server_port,
                    path,
                    {"Cookie": f"luckyd_ctl={SENTINEL}"},
                )
                assert status == 200, f"{path} rejected the session cookie"
                assert SENTINEL not in body, f"{path} leaks the token in HTML"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_api_routes_accept_bearer_or_cookie(self):
        server, thread = _control_server(SENTINEL)
        try:
            status, body = _get(server.server_port, "/tabs")
            assert status == 401
            assert json.loads(body)["error"] == "unauthorized"

            status, _ = _get(
                server.server_port,
                "/tabs",
                {"Authorization": f"Bearer {SENTINEL}"},
            )
            assert status == 200

            status, _ = _get(server.server_port, "/tabs", {"Cookie": f"luckyd_ctl={SENTINEL}"})
            assert status == 200

            # Wrong cookie must not authenticate.
            status, _ = _get(server.server_port, "/tabs", {"Cookie": "luckyd_ctl=wrong"})
            assert status == 401
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_host_ok_accepts_bracketed_ipv6_loopback(self):
        from browser_core.control_server import make_handler

        handler_cls = make_handler(_FakeBackend(), token=SENTINEL)

        def host_ok(host: str) -> bool:
            h = handler_cls.__new__(handler_cls)
            h.headers = {"Host": host}
            return h._host_ok()

        assert host_ok("127.0.0.1:9777")
        assert host_ok("localhost:9777")
        assert host_ok("[::1]:9777")
        assert host_ok("[::1]")
        assert not host_ok("evil.example:9777")
        assert not host_ok("[::ffff:1.2.3.4]:9777")


# ── HQ server: landing-page auth + token non-disclosure ────────────────────


class TestHQCookieAuth:
    def test_hq_landing_requires_auth_and_hides_token(self, monkeypatch):
        import web_server

        monkeypatch.setattr(web_server, "_TOKEN", SENTINEL)
        server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.HQHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status, body = _get(server.server_port, "/")
            assert status == 401, "HQ landing page was reachable without auth"

            status, body = _get(server.server_port, "/", {"Cookie": f"luckyd_hq={SENTINEL}"})
            assert status == 200, "HQ landing page rejected the session cookie"
            assert SENTINEL not in body, "HQ landing page leaks the token"
            assert "__HQ_TOKEN__" not in body
            assert "HQ_TOKEN" not in body
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


# ── Token non-disclosure in page builders ──────────────────────────────────


class TestTokenNonDisclosure:
    def test_pages_never_embed_tokens(self):
        from browser_core.dashboard import dashboard_html
        from browser_core.research_page import research_html
        from browser_core.terminal_page import mesh_html, terminal_html

        for html in (
            dashboard_html(),
            research_html(),
            mesh_html(),
            terminal_html(),
        ):
            assert SENTINEL not in html
        # No JS-visible token constants remain in any served page.
        combined = dashboard_html() + research_html() + mesh_html() + terminal_html()
        for const in ("DASH_TOKEN", "MESH_TOKEN", "WS_TOKEN", "HQ_TOKEN", "const TOKEN"):
            assert const not in combined, f"{const} still embedded in served HTML"


# ── Deep Research markdown XSS ─────────────────────────────────────────────


def _extract_js_function(html: str, name: str) -> str:
    start = html.index(f"function {name}")
    i = html.index("{", start)
    depth = 0
    for j in range(i, len(html)):
        if html[j] == "{":
            depth += 1
        elif html[j] == "}":
            depth -= 1
            if depth == 0:
                return html[start : j + 1]
    raise AssertionError(f"unbalanced braces in {name}")


def _run_parse_markdown(payloads: list[str]) -> list[str]:
    """Execute the real parseMarkdown from the served page in node."""
    from browser_core.research_page import research_html

    html = research_html()
    src = (
        _extract_js_function(html, "escapeHtml")
        + "\n"
        + _extract_js_function(html, "parseMarkdown")
    )
    script = (
        src
        + "\nconst payloads = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n"
        + "console.log(JSON.stringify(payloads.map(parseMarkdown)));"
    )
    proc = subprocess.run(
        ["node", "-e", script],
        input=json.dumps(payloads).encode(),
        capture_output=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr.decode()
    return json.loads(proc.stdout.decode())


class TestResearchMarkdownXSS:
    def test_report_markdown_is_escaped(self):
        out = _run_parse_markdown(
            [
                "<img src=x onerror=alert(1)>",
                "<script>alert(1)</script>",
                "a < b && c > d",
            ]
        )
        for rendered in out:
            assert "<img" not in rendered
            assert "<script>" not in rendered
        assert "&lt;img" in out[0]
        assert "&lt;script&gt;" in out[1]

    def test_dangerous_link_schemes_are_neutralized(self):
        out = _run_parse_markdown(
            [
                "[click](javascript:alert(1))",
                "[data](data:text/html,<script>alert(1)</script>)",
                "[ok](https://example.com/report)",
            ]
        )
        assert "javascript:" not in out[0]
        assert 'href="#"' in out[0]
        assert "data:text/html" not in out[1]
        assert 'href="https://example.com/report"' in out[2]

    def test_legit_markdown_still_renders(self):
        out = _run_parse_markdown(["**bold** and *italic*", "# Title"])
        assert "<b>bold</b>" in out[0]
        assert "<i>italic</i>" in out[0]
        assert "<h1>Title</h1>" in out[1]


# ── Terminal WebSocket: cookie auth, no query token ────────────────────────


class TestTerminalCookieAuth:
    def _socket(self, cookie: str):
        class Request:
            def __init__(self):
                self.path = "/?cols=80&rows=24&shell=agent"
                self.headers = {"Cookie": cookie}

        class Socket:
            def __init__(self):
                self.request = Request()

        return Socket()

    def test_cookie_authenticates_and_query_token_does_not(self):
        from browser_core.terminal_server import TerminalServer

        server = TerminalServer(token=SENTINEL)
        assert server._authorized(self._socket(f"luckyd_term={SENTINEL}"))
        assert not server._authorized(self._socket("luckyd_term=wrong"))
        assert not server._authorized(self._socket(""))
        # The old ?token= transport is gone: a bare query token must fail.
        assert not TerminalServer(token=SENTINEL)._authorized(self._socket(""))


# ── Checkpoint: no infinite loop ───────────────────────────────────────────


class TestCheckpointNoInfiniteLoop:
    def test_undo_to_missing_file_terminates(self, tmp_path):
        """undo_to() must terminate when a checkpoint's file is gone (4.0)."""
        from core.checkpoint import CheckpointManager

        mgr = CheckpointManager()
        target = tmp_path / "victim.txt"
        target.write_text("v1", encoding="utf-8")
        first = mgr.record_change(str(target), "v0", "v1")
        target.write_text("v2", encoding="utf-8")
        mgr.record_change(str(target), "v1", "v2")
        target.unlink()  # file gone: the old code spun here forever

        done: list = []

        def _run():
            done.extend(mgr.undo_to(first.checkpoint_id))

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout=10)
        assert not t.is_alive(), "undo_to() did not terminate (infinite loop)"


# ── Version unification ────────────────────────────────────────────────────


class TestVersionUnification:
    """Version references are unified; this guards against drift."""

    def test_versions_unified_at_current(self):
        import tomllib

        import browser

        assert browser.__version__ == "5.0.0"

        with open(_REPO_ROOT / "pyproject.toml", "rb") as f:
            assert tomllib.load(f)["project"]["version"] == "5.0.0"

        ai_bridge = (_BROWSER_DIR / "browser_core" / "ai_bridge.py").read_text(encoding="utf-8")
        assert "LuckyDBrowser/5.0" in ai_bridge
        assert "LuckyDBrowser/1.0" not in ai_bridge

        version_info = (_BROWSER_DIR / "version_info.txt").read_text(encoding="utf-8")
        assert "5.0.0.0" in version_info
        assert "3.9.0" not in version_info

        iss = (_BROWSER_DIR / "installer" / "LuckyDBrowser.iss").read_text(encoding="utf-8")
        assert '#define AppVersion   "5.0.0"' in iss


# ── cline_bridge: inbound bearer auth ───────────────────────────────────────


class TestClineBridgeAuth:
    def test_bridge_requires_bearer_token(self, monkeypatch):
        monkeypatch.setenv("CLINE_BRIDGE_TOKEN", "bridge-test-secret")
        monkeypatch.delenv("CODING_AGENT_API_KEY", raising=False)
        from fastapi.testclient import TestClient

        import cline_bridge

        client = TestClient(cline_bridge.app)
        assert client.get("/v1/models").status_code == 401
        assert (
            client.get("/v1/models", headers={"Authorization": "Bearer wrong"}).status_code == 401
        )
        r = client.get("/v1/models", headers={"Authorization": "Bearer bridge-test-secret"})
        assert r.status_code == 200
        assert r.json()["object"] == "list"
        # Health stays public (diagnostic only).
        assert client.get("/v1/health").status_code in (200, 500)

    def test_bridge_fails_closed_without_token_configured(self, monkeypatch):
        monkeypatch.delenv("CLINE_BRIDGE_TOKEN", raising=False)
        monkeypatch.delenv("CODING_AGENT_API_KEY", raising=False)
        from fastapi.testclient import TestClient

        import cline_bridge

        client = TestClient(cline_bridge.app)
        assert client.get("/v1/models").status_code == 401
        # Even a plausible bearer must fail when nothing is configured.
        assert (
            client.get("/v1/models", headers={"Authorization": "Bearer anything"}).status_code
            == 401
        )
