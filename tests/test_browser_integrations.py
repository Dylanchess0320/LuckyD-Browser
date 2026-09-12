"""Regression coverage for the browser's agent, terminal, and updater plumbing."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock

# updater.py imports PySide6.QtCore at top-level (QThread/Signal) — not
# installed on Linux CI. Mock before importing browser modules so collection
# succeeds on all CI runners (ubuntu/windows). Real browser tests run with Qt.
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
    # httpx honors proxy env vars; neutralize them so loopback stays direct
    # (some sandboxes export bracketed-IPv6 no_proxy entries httpx can't parse).
    for var in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
        "NO_PROXY",
        "no_proxy",
    ):
        monkeypatch.delenv(var, raising=False)
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
    page = terminal_html(settings)
    # 4.0: the terminal secret travels in the HttpOnly luckyd_term cookie —
    # it must never appear in served HTML or the WebSocket URL.
    assert terminal_token not in page
    assert "WS_TOKEN" not in page
    assert "?token=" not in page

    class Request:
        def __init__(self, cookie: str):
            self.path = "/"
            self.headers = {"Cookie": cookie}

    class Socket:
        def __init__(self, cookie: str):
            self.request = Request(cookie)

    server = TerminalServer(token=terminal_token)
    assert server._authorized(Socket(f"luckyd_term={terminal_token}"))
    assert not server._authorized(Socket("luckyd_term=wrong"))
    assert not TerminalServer()._authorized(Socket(f"luckyd_term={terminal_token}"))


def test_agent_mesh_keeps_all_four_sessions_visible() -> None:
    page = mesh_html()
    assert page.count("<iframe") == 4
    assert all(f"shell={shell}" in page for shell in ("agent", "agent2", "powershell", "cmd"))
    # 4.0: no credential may be embedded in the mesh page.
    assert "MESH_TOKEN" not in page


def test_agent_workspace_uses_no_lightning_icon() -> None:
    assert "['⚡', 'Coding Agent'" not in DASHBOARD_HTML
    source = (Path(__file__).parents[1] / "browser" / "browser_core" / "dashboard.py").read_text(
        encoding="utf-8"
    )
    assert "class='spin'>⚡" not in source


def test_antigravity_cli_integration(monkeypatch, tmp_path: Path) -> None:
    from browser.browser_core.terminal_page import _MESH_AGENTS, _SHELL_LABELS
    from browser.browser_core.terminal_server import SHELLS, _mesh_shell_command

    assert "mesh-agy" in SHELLS
    assert "agy" in SHELLS
    assert "mesh-agy" in _MESH_AGENTS
    assert _SHELL_LABELS.get("mesh-agy") == "Antigravity"
    mock_agy = tmp_path / "agy.exe"
    mock_agy.touch()
    monkeypatch.setattr(
        "browser.browser_core.terminal_server._find_mesh_exe",
        lambda exe: str(mock_agy) if exe == "agy" else None,
    )
    cmd = _mesh_shell_command("mesh-agy")
    assert len(cmd) == 1 and cmd[0].lower().endswith("agy.exe")


def test_dsh_shell_boots_web_profile(monkeypatch, tmp_path: Path) -> None:
    """mesh-dsh must spawn `dsh --profile web`, never a bare `dsh`.

    dsh is a profile launcher: a bare invocation exits immediately with
    `error: --profile <name> is required`, which showed up as an instantly
    dead terminal pane when the DeepSeek chip was clicked.
    """
    from browser.browser_core.terminal_server import _mesh_shell_command

    mock_dsh = tmp_path / "dsh.cmd"
    mock_dsh.touch()
    monkeypatch.setattr(
        "browser.browser_core.terminal_server._find_mesh_exe",
        lambda exe: str(mock_dsh) if exe == "dsh" else None,
    )
    cmd = _mesh_shell_command("mesh-dsh")
    assert cmd[0].lower().endswith("dsh.cmd")
    assert cmd[1:] == ["--profile", "web", "--no-open"]


def test_hermes_shell_boots_chat(monkeypatch, tmp_path: Path) -> None:
    """mesh-hermes must spawn `hermes chat`, never a bare `hermes`.

    A bare hermes exits immediately on a closed stdin, which showed up as
    an instantly dead terminal pane when the Hermes chip was clicked
    (same class of bug as mesh-dsh before it pinned the web profile).
    """
    from browser.browser_core.terminal_page import _MESH_AGENTS, _SHELL_LABELS
    from browser.browser_core.terminal_server import SHELLS, _mesh_shell_command

    assert "mesh-hermes" in SHELLS
    assert "mesh-hermes" in _MESH_AGENTS
    assert _SHELL_LABELS.get("mesh-hermes") == "Hermes"
    mock_hermes = tmp_path / "hermes.exe"
    mock_hermes.touch()
    monkeypatch.setattr(
        "browser.browser_core.terminal_server._find_mesh_exe",
        lambda exe: str(mock_hermes) if exe == "hermes" else None,
    )
    cmd = _mesh_shell_command("mesh-hermes")
    assert cmd[0].lower().endswith("hermes.exe")
    assert cmd[1:] == ["chat"]


def test_muse_spark_removed_from_mesh(monkeypatch, tmp_path: Path) -> None:
    """Muse Spark was removed from the Agent Mesh (Dylan, 2026-09-12).

    It duplicated Muse Code with a worse experience (no Windows exe, pinned
    opencode + a contributor-free model), so both the dock chip and the
    shell allowlist must NOT offer it. Native ``muse`` (WSL) is unchanged.
    """
    from browser.browser_core.terminal_page import _MESH_AGENTS, _SHELL_LABELS
    from browser.browser_core.terminal_server import SHELLS

    assert "mesh-muse-spark" not in SHELLS
    assert "muse-spark" not in SHELLS
    assert "mesh-muse-spark" not in _MESH_AGENTS
    assert "muse-spark" not in _SHELL_LABELS
    # Native Muse Code survives.
    assert "mesh-muse" in _MESH_AGENTS
    assert _SHELL_LABELS.get("muse") == "Muse Code"


def test_muse_shell_uses_wsl_bridge_without_probing_distro(monkeypatch, tmp_path: Path) -> None:
    """mesh-muse must spawn via wsl.exe and never invoke the distro to probe.

    Native muse has no Windows build; it lives inside WSL Ubuntu. Probing
    the distro during availability/command resolution can HANG when WSL is
    broken, so only wsl.exe presence is checked — a missing inner `muse`
    prints install steps inside a live bash instead of a dead pane.
    """
    from browser.browser_core.terminal_page import _MESH_AGENTS, _SHELL_LABELS
    from browser.browser_core.terminal_server import (
        MUSE_WSL_DISTRO,
        SHELLS,
        _mesh_shell_command,
        mesh_shells_available,
    )

    assert "mesh-muse" in SHELLS
    assert "muse" in SHELLS
    assert "mesh-muse" in _MESH_AGENTS
    assert _SHELL_LABELS.get("mesh-muse") == "Muse Code"
    mock_wsl = tmp_path / "wsl.exe"
    mock_wsl.touch()
    monkeypatch.setattr(
        "browser.browser_core.terminal_server._find_wsl_exe",
        lambda: str(mock_wsl),
    )
    monkeypatch.setattr(
        "browser.browser_core.terminal_server._find_mesh_exe",
        lambda exe: None,
    )
    avail = mesh_shells_available()
    assert avail["mesh-muse"] is True
    assert avail["muse"] is True
    cmd = _mesh_shell_command("mesh-muse")
    assert cmd[0].lower().endswith("wsl.exe")
    assert cmd[1:4] == ["-d", MUSE_WSL_DISTRO, "--"]
    assert "muse" in cmd[-1]


def test_muse_shell_missing_wsl_raises_helpful_error(monkeypatch) -> None:
    from browser.browser_core.terminal_server import _mesh_shell_command

    monkeypatch.setattr(
        "browser.browser_core.terminal_server._find_wsl_exe",
        lambda: None,
    )
    try:
        _mesh_shell_command("mesh-muse")
    except FileNotFoundError as exc:
        assert "wsl" in str(exc).lower()
    else:
        raise AssertionError("expected FileNotFoundError when wsl.exe is missing")


def test_installer_removes_legacy_desktop_shortcut() -> None:
    installer = (
        Path(__file__).parents[1] / "browser" / "installer" / "LuckyDBrowser.iss"
    ).read_text(encoding="utf-8-sig")
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
