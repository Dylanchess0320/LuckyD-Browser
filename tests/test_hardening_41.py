"""4.1 hardening regression tests (audit backlog).

- SSRF: web_tools blocks private/loopback/link-local targets, decimal-IP
  tricks, and non-http schemes; redirects are re-validated; bearer tokens
  never go over plain HTTP.
- Single-source blocklist: bash_tool delegates to sandbox.is_safe;
  ProcessTool refuses blocked background commands.
- bridge/vscode-bridge dedupe; dead web_gui.py archived; orphaned tool
  modules wired into agent.py.
- terminal_server.stop() is lock-safe; control frames need a known type.
- session.save() uses per-process tmp names; settings/permissions save()
  report success instead of swallowing failures.
- tile_registry: locked autostart, zombie reaping, shutdown cleanup,
  HTML-escaped tile anchors.
- cdp_driver: malformed CDP replies are skipped, not crashed on.
"""

from __future__ import annotations

import json
import sys
import threading
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


# ── SSRF ─────────────────────────────────────────────────────────────

from tools.web_tools import (
    HttpTool,
    WebFetchTool,
    _assert_public_url,
    _safe_opener,
    _SSRFRedirectHandler,
)


def test_ssrf_blocks_loopback_and_private():
    for url in (
        "http://127.0.0.1/",
        "http://localhost/",
        "http://10.0.0.5/x",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://169.254.169.254/",  # cloud metadata
        "http://[::1]/",
        "http://0.0.0.0/",
        "http://2130706433/",  # decimal 127.0.0.1
        "http://0x7f.0x0.0x0.0x1/",
    ):
        try:
            _assert_public_url(url)
        except ValueError:
            continue
        raise AssertionError(f"SSRF not blocked: {url}")


def test_ssrf_rejects_non_http_scheme():
    for url in ("ftp://example.com/", "file:///etc/passwd", "gopher://x/"):
        try:
            _assert_public_url(url)
        except ValueError:
            continue
        raise AssertionError(f"scheme not rejected: {url}")


def test_ssrf_redirect_handler_validates():
    handler = _SSRFRedirectHandler()
    try:
        handler.redirect_request(MagicMock(), None, 302, "Found", {}, "http://169.254.169.254/")
    except ValueError:
        return
    raise AssertionError("redirect to metadata IP was not rejected")


def test_safe_opener_disables_proxies(monkeypatch):

    # Even with hostile proxy env vars, the opener must not route through
    # a proxy (which would bypass the SSRF IP checks).
    monkeypatch.setenv("http_proxy", "http://evil-proxy:9999")
    monkeypatch.setenv("https_proxy", "http://evil-proxy:9999")
    opener = _safe_opener()
    for kind, handlers in opener.handle_open.items():
        for h in handlers:
            assert not getattr(h, "proxies", None), f"{h!r} would proxy {kind}"


async def test_bearer_token_never_over_plain_http():
    tool = HttpTool()
    out = await tool.execute(url="http://example.com/", bearer_token="sekret")
    assert out.error
    assert "HTTPS" in out.text


async def test_webfetch_blocks_ssrf_target():
    tool = WebFetchTool()
    out = await tool.execute(url="http://127.0.0.1:9999/")
    assert out.error


# ── single-source blocklist ──────────────────────────────────────────

from sandbox import BLOCKLIST, is_safe
from tools import bash_tool


def test_bash_tool_delegates_to_sandbox():
    assert bash_tool.BLOCKED_PATTERNS is BLOCKLIST
    assert bash_tool._sandbox_is_safe is not None
    # merged coverage from both former lists
    for cmd in ("rm -rf /", "reboot", "del /f /s C:\\Windows\\x", "shutdown /s"):
        safe, _ = is_safe(cmd)
        assert not safe, cmd
        tool_safe, _ = bash_tool.BashTool()._is_safe(cmd)
        assert not tool_safe, cmd
    safe, _ = bash_tool.BashTool()._is_safe("echo hello")
    assert safe


async def test_process_tool_refuses_blocked_command():
    from tools.utility_tools import ProcessTool

    tool = ProcessTool()
    out = await tool.execute(op="start", command="rm -rf /")
    assert out.error
    assert "Refused" in out.text


# ── bridge dedupe / dead code / wiring ───────────────────────────────


def test_vscode_bridge_is_shim():
    src = (_REPO_ROOT / "vscode-bridge.py").read_text(encoding="utf-8")
    assert "bridge.READY_MESSAGE" in src
    assert "handle_request" not in src  # no duplicated protocol code
    assert (_REPO_ROOT / "archive" / "web_gui.py").exists()  # dead GUI archived


def test_orphaned_tools_wired():
    src = (_REPO_ROOT / "agent.py").read_text(encoding="utf-8")
    for mod in ("tools.browser_use_tool", "tools.desktop_tools", "tools.mcp_tools"):
        assert f"import {mod}" in src, mod


# ── terminal server ──────────────────────────────────────────────────

from browser_core.terminal_server import TerminalServer


def test_terminal_stop_is_lock_safe_under_concurrency():
    server = TerminalServer(token="tok")
    ws = MagicMock()
    errors = []

    def adder():
        try:
            for _ in range(200):
                with server._lock:
                    server._clients.add(ws)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=adder) for _ in range(4)]
    for t in threads:
        t.start()
    for _ in range(50):
        server.stop()
    for t in threads:
        t.join()
    # Deterministic finish: no adder can run past this point, so one final
    # stop() must leave the client set empty. (The interleaved stops above
    # are what exercise lock safety; the bare assertion was racy.)
    server.stop()
    assert not errors
    assert server._clients == set()


# ── session / settings / permissions ─────────────────────────────────

from browser_core.session import SessionStore


def test_session_save_leaves_no_tmp_and_roundtrips(tmp_path):
    store = SessionStore(tmp_path / "session.json")
    assert store.save([{"tabs": []}]) is True
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == [], leftovers
    # second save from "another process" also leaves no staging files
    assert store.save([{"tabs": []}]) is True
    assert list(tmp_path.glob("*.tmp")) == []


def test_settings_and_permissions_save_report_success(tmp_path):
    from browser_core.permissions import PermissionStore
    from browser_core.settings import SettingsStore

    s = SettingsStore(path=tmp_path / "settings.json")
    assert s.save() is True
    p = PermissionStore(path=tmp_path / "permissions.json")
    assert p.save() is True


# ── tile registry ────────────────────────────────────────────────────

from browser_core.tile_registry import (
    Tile,
    _launched,
    _registry_lock,
    ensure_autostart,
    shutdown_autostart,
    tile_anchor,
)


def test_tile_anchor_escapes_config_html():
    evil = Tile(id="x", name="<script>alert(1)</script>", icon="🔗", url='https://"onmouseover="x')
    out = tile_anchor(evil, {"up": True})
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_autostart_reaps_dead_children():
    import subprocess
    import sys

    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    with _registry_lock:
        _launched["__test_zombie__"] = proc
    ensure_autostart([])  # reaps without launching anything
    with _registry_lock:
        assert "__test_zombie__" not in _launched


def test_shutdown_autostart_never_raises():
    shutdown_autostart()  # no children launched — must be a safe no-op


# ── CDP driver ───────────────────────────────────────────────────────

from browser_core.cdp_driver import CdpDriver, CdpPage


class _FakeWS:
    def __init__(self, messages):
        self._messages = list(messages)
        self.sent = []

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        return self._messages.pop(0)


async def test_cdp_cmd_skips_malformed_frames():
    ws = _FakeWS(
        [
            "not json at all",
            "[1, 2, 3]",  # valid JSON, not a CDP message
            json.dumps({"id": 999, "method": "Event", "params": {}}),  # wrong id
            json.dumps({"id": 1, "result": {"ok": True}}),
        ]
    )
    page = CdpPage(ws)
    result = await page.cmd("Page.enable")
    assert result == {"ok": True}


async def test_cdp_cmd_tolerates_missing_result():
    ws = _FakeWS([json.dumps({"id": 1})])
    page = CdpPage(ws)
    assert await page.cmd("Page.enable") == {}


async def test_cdp_evaluate_and_screenshot_guard_shapes():
    ws = _FakeWS(
        [
            json.dumps({"id": 1, "result": {"result": None}}),  # result.result is null
            json.dumps({"id": 2, "result": {}}),  # no data key
        ]
    )
    page = CdpPage(ws)
    assert await page.evaluate("1+1") is None
    assert await page.screenshot_b64() == ""


async def test_cdp_coords_rejects_malformed():
    ws = _FakeWS([json.dumps({"id": 1, "result": {"result": {"value": '{"x": 1}'}}})])
    driver = CdpDriver(CdpPage(ws))
    assert await driver._coords(3) is None
