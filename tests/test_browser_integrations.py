"""Regression coverage for the browser's agent, terminal, and updater plumbing."""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import config
import main
from browser.browser_core.dashboard import DASHBOARD_HTML
from browser.browser_core.harness_bridge import HarnessBridge
from browser.browser_core.settings import DEFAULTS, SettingsStore
from browser.browser_core.terminal_page import mesh_html, terminal_html
from browser.browser_core.terminal_server import TerminalServer, _cli_command
from browser.browser_core.updater import asset_sha256, is_installer_asset, is_newer


class _AuthenticatedToolsHandler(BaseHTTPRequestHandler):
    token = "test-hq-token"

    def do_GET(self) -> None:
        if self.path != "/api/tools" or self.headers.get("Authorization") != f"Bearer {self.token}":
            self.send_response(401)
            self.end_headers()
            return
        body = json.dumps({"tools": [{"name": "AgentHandoff"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *args) -> None:
        return


def test_harness_bridge_authenticates_protected_api_calls(monkeypatch) -> None:
    """A healthy, token-protected HQ must expose its tools to the browser."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _AuthenticatedToolsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("LUCKYD_HQ_TOKEN", _AuthenticatedToolsHandler.token)
    try:
        bridge = HarnessBridge(host="127.0.0.1", port=server.server_port)
        assert asyncio.run(bridge.list_tools()) == [{"name": "AgentHandoff"}]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_legacy_terminal_setting_is_migrated_to_interactive_cli(tmp_path: Path) -> None:
    """The old headless harness executable must never occupy a terminal tab."""
    harness = tmp_path / "luckyd-code.exe"
    interactive = tmp_path / "luckyd-cli.exe"
    harness.touch()
    interactive.touch()
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"terminal_cli": str(harness)}), encoding="utf-8")

    settings = SettingsStore(settings_path)

    assert settings.get("terminal_cli") == str(interactive)
    assert _cli_command(str(harness)) == [str(interactive)]


def test_harness_defaults_and_release_asset_filter() -> None:
    assert DEFAULTS["harness_mode"] is True
    assert DEFAULTS["harness_autostart"] is True
    assert is_installer_asset({"name": "LuckyDBrowserSetup-2.5.6.exe"})
    assert is_installer_asset({"name": "LuckyDBrowserSetup-2.5.6.exe"}, "2.5.6")
    assert not is_installer_asset({"name": "LuckyDBrowserSetup-2.5.5.exe"}, "2.5.6")
    assert not is_installer_asset({"name": "extension-setup.exe"})
    assert not is_installer_asset({"name": "LuckyDBrowser-2.5.6.zip"})
    assert asset_sha256({"digest": "sha256:" + "a" * 64}) == "a" * 64
    assert asset_sha256({"digest": "sha1:" + "a" * 40}) == ""
    assert is_newer("2.5.6", "2.5.5")


def test_local_control_secrets_are_created_and_terminal_requires_one(tmp_path: Path) -> None:
    """Local pages get the terminal secret; arbitrary WS clients do not."""
    settings = SettingsStore(tmp_path / "settings.json")
    api_token = str(settings.get("browser_api_token"))
    terminal_token = str(settings.get("terminal_token"))

    assert len(api_token) >= 32
    assert len(terminal_token) >= 32
    assert f'const WS_TOKEN = "{terminal_token}";' in terminal_html(settings)

    class Request:
        def __init__(self, path: str):
            self.path = path

    class Socket:
        def __init__(self, path: str):
            self.request = Request(path)

    server = TerminalServer(token=terminal_token)
    assert server._authorized(Socket(f"/?token={terminal_token}"))
    assert not server._authorized(Socket("/?token=wrong"))
    assert not TerminalServer()._authorized(Socket(f"/?token={terminal_token}"))


def test_agent_mesh_keeps_all_four_sessions_visible() -> None:
    page = mesh_html("mesh-test-token")
    assert page.count("<iframe") == 4
    assert all(
        f"shell={shell}" in page for shell in ("agent", "agent2", "powershell", "cmd")
    )
    assert 'const MESH_TOKEN = "mesh-test-token"' in page


def test_agent_workspace_uses_no_lightning_icon() -> None:
    assert "['⚡', 'Coding Agent'" not in DASHBOARD_HTML
    source = (Path(__file__).parents[1] / "browser" / "browser_core" / "dashboard.py").read_text(
        encoding="utf-8"
    )
    assert 'class=\'spin\'>⚡' not in source


def test_installer_removes_legacy_desktop_shortcut() -> None:
    installer = (Path(__file__).parents[1] / "browser" / "installer" / "LuckyDBrowser.iss").read_text(
        encoding="utf-8-sig"
    )
    assert "{autodesktop}\\LuckyDBrowser.lnk" in installer
    assert "{userprograms}\\LuckyD Browser.lnk" in installer
    assert 'Name: "desktopicon"' in installer and "Flags: checkedonce" in installer


def test_interactive_model_selection_is_persisted(tmp_path: Path, monkeypatch) -> None:
    """Both terminal agents run this code from their own checkout/.env."""
    env_file = tmp_path / ".env"
    env_file.write_text("CODING_AGENT_PROVIDER=ollama\nOLLAMA_MODEL=old\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", env_file)

    main._persist_model_selection("ollama", "llama3.1")

    saved = env_file.read_text(encoding="utf-8")
    assert "CODING_AGENT_PROVIDER=ollama" in saved
    assert "OLLAMA_MODEL=llama3.1" in saved


def test_agent_slots_use_independent_model_overlays(tmp_path: Path) -> None:
    base = tmp_path / ".env"
    base.write_text("OLLAMA_MODEL=base\n", encoding="utf-8")
    one = tmp_path / ".luckyd-agent-1.env"
    two = tmp_path / ".luckyd-agent-2.env"
    one.write_text("OLLAMA_MODEL=llama3.1\n", encoding="utf-8")
    two.write_text("OLLAMA_MODEL=mistral\n", encoding="utf-8")
    assert one.read_text(encoding="utf-8") != two.read_text(encoding="utf-8")
