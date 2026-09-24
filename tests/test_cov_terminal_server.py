"""Coverage push for browser_core/terminal_server.py — CLI resolution, mesh
launchers, _spawn_pty env handling, pump_out paths, and start()/stop().

Earlier suites (test_night1_netmon_terminal.py, test_night4_terminal.py)
covered shell-command dispatch, _client_options/_client_token parsing, the
fail-closed auth check, and the _handle message-routing core. This file
covers the rest: _desktop_exe/_agent2_dir desktop probing, the
_cli_command/_agent2_command resolution chains, mesh-exe fallback probing,
_wsl_muse_command, _spawn_pty (with a hand-written winpty stand-in — pywinpty
is Windows-only), the pump_out data/error branches, and the start() success
path, using real filesystem fixtures and hand-written fakes only.
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time
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
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

import browser_core.terminal_server as ts
import pytest
from browser_core.terminal_server import (
    MUSE_WSL_BOOT,
    MUSE_WSL_DISTRO,
    TERM_COOKIE,
    TerminalServer,
    _agent2_command,
    _agent2_cwd,
    _agent2_dir,
    _agent_cwd,
    _cli_command,
    _client_token,
    _desktop_exe,
    _find_mesh_exe,
    _find_wsl_exe,
    _mesh_shell_command,
    _python_for_scripts,
    _shell_command,
    _spawn_pty,
    _wsl_muse_command,
)

TOKEN = "cov-term-secret"


def _home_to(monkeypatch, path: Path) -> None:
    """Pin Path.home() to a fixture dir so desktop probes stay hermetic."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: path))


def _fake_ctypes(monkeypatch, desktop: Path, rc: int = 0):
    """Stand-in ctypes whose SHGetFolderPathW reports *desktop* as the Desktop.

    *rc* nonzero simulates the API failing (no redirection-aware path), so
    the caller falls through to the plain/OneDrive home fallbacks.
    """

    class _Buf:
        def __init__(self):
            self.value = ""

    def _shget(*args):
        args[4].value = str(desktop)
        return rc

    fake = SimpleNamespace(
        create_unicode_buffer=lambda n: _Buf(),
        windll=SimpleNamespace(shell32=SimpleNamespace(SHGetFolderPathW=_shget)),
    )
    monkeypatch.setitem(sys.modules, "ctypes", fake)


# ── _desktop_exe ─────────────────────────────────────────────────────


def test_desktop_exe_ctypes_desktop(monkeypatch, tmp_path) -> None:
    exe = tmp_path / "luckyd-cli.exe"
    exe.touch()
    _fake_ctypes(monkeypatch, tmp_path)
    _home_to(monkeypatch, tmp_path / "nohome")
    monkeypatch.delenv("ONEDRIVE", raising=False)
    assert _desktop_exe() == exe


def test_desktop_exe_onedrive_env(monkeypatch, tmp_path) -> None:
    exe = tmp_path / "Desktop" / "luckyd-cli.exe"
    exe.parent.mkdir(parents=True)
    exe.touch()
    monkeypatch.setenv("ONEDRIVE", str(tmp_path))
    _home_to(monkeypatch, tmp_path / "nohome")
    assert _desktop_exe() == exe


def test_desktop_exe_home_fallback(monkeypatch, tmp_path) -> None:
    exe = tmp_path / "Desktop" / "luckyd-cli.exe"
    exe.parent.mkdir(parents=True)
    exe.touch()
    _home_to(monkeypatch, tmp_path)
    monkeypatch.delenv("ONEDRIVE", raising=False)
    assert _desktop_exe() == exe


def test_desktop_exe_nothing_found(monkeypatch, tmp_path) -> None:
    _home_to(monkeypatch, tmp_path)
    monkeypatch.delenv("ONEDRIVE", raising=False)
    assert _desktop_exe() is None


def test_desktop_exe_ctypes_failure_falls_through(monkeypatch, tmp_path) -> None:
    exe = tmp_path / "Desktop" / "luckyd-cli.exe"
    exe.parent.mkdir(parents=True)
    exe.touch()
    _fake_ctypes(monkeypatch, tmp_path / "bogus-desktop", rc=1)
    _home_to(monkeypatch, tmp_path)
    monkeypatch.delenv("ONEDRIVE", raising=False)
    assert _desktop_exe() == exe


# ── _python_for_scripts ──────────────────────────────────────────────


def test_python_for_scripts_unfrozen() -> None:
    assert _python_for_scripts() == sys.executable


def test_python_for_scripts_frozen_finds_python(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(shutil, "which", lambda name: {"python": "/usr/bin/python"}.get(name))
    assert _python_for_scripts() == "/usr/bin/python"


def test_python_for_scripts_frozen_none_on_path(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert _python_for_scripts() is None


# ── _cli_command ─────────────────────────────────────────────────────


def test_cli_command_override_missing_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="terminal_cli does not exist"):
        _cli_command(str(tmp_path / "nope.exe"))


def test_cli_command_override_headless_hq_server_raises(tmp_path) -> None:
    (tmp_path / "luckyd-code.exe").touch()
    with pytest.raises(FileNotFoundError, match="headless HQ server"):
        _cli_command(str(tmp_path / "luckyd-code.exe"))


def test_cli_command_override_hq_server_sibling_cli_wins(tmp_path) -> None:
    (tmp_path / "luckyd-code.exe").touch()
    cli = tmp_path / "luckyd-cli.exe"
    cli.touch()
    assert _cli_command(str(tmp_path / "luckyd-code.exe")) == [str(cli)]


def test_cli_command_override_plain_exe(tmp_path) -> None:
    exe = tmp_path / "mycli.exe"
    exe.touch()
    assert _cli_command(str(exe)) == [str(exe)]


def test_cli_command_override_py_uses_interpreter(monkeypatch, tmp_path) -> None:
    script = tmp_path / "cli.py"
    script.write_text("# cli")
    monkeypatch.setattr(ts, "_python_for_scripts", lambda: "/usr/bin/python3")
    assert _cli_command(str(script)) == ["/usr/bin/python3", str(script)]


def test_cli_command_override_py_no_interpreter_raises(monkeypatch, tmp_path) -> None:
    script = tmp_path / "cli.py"
    script.write_text("# cli")
    monkeypatch.setattr(ts, "_python_for_scripts", lambda: None)
    with pytest.raises(FileNotFoundError, match="no Python"):
        _cli_command(str(script))


def test_cli_command_env_var_override(monkeypatch, tmp_path) -> None:
    exe = tmp_path / "envcli.exe"
    exe.touch()
    monkeypatch.setenv("LUCKYD_CLI", str(exe))
    assert _cli_command("") == [str(exe)]


def test_cli_command_desktop_exe(monkeypatch, tmp_path) -> None:
    exe = tmp_path / "luckyd-cli.exe"
    exe.touch()
    monkeypatch.setattr(ts, "_desktop_exe", lambda: exe)
    monkeypatch.delenv("LUCKYD_CLI", raising=False)
    assert _cli_command("") == [str(exe)]


def test_cli_command_repo_root_candidate(monkeypatch, tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    exe = root / "luckyd-cli.exe"
    exe.touch()
    monkeypatch.setattr(ts, "_REPO_ROOT", root)
    monkeypatch.setattr(ts, "_desktop_exe", lambda: None)
    monkeypatch.delenv("LUCKYD_CLI", raising=False)
    assert _cli_command("") == [str(exe)]


def test_cli_command_frozen_beside_exe(monkeypatch, tmp_path) -> None:
    appdir = tmp_path / "app"
    appdir.mkdir()
    exe = appdir / "luckyd-cli.exe"
    exe.touch()
    monkeypatch.setattr(ts, "_REPO_ROOT", tmp_path / "emptyrepo")
    monkeypatch.setattr(ts, "_desktop_exe", lambda: None)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(appdir / "LuckyDBrowser.exe"))
    monkeypatch.delenv("LUCKYD_CLI", raising=False)
    assert _cli_command("") == [str(exe)]


def test_cli_command_live_main_py(monkeypatch, tmp_path) -> None:
    root = tmp_path / "live"
    root.mkdir()
    main = root / "main.py"
    main.write_text("# live cli")
    monkeypatch.setattr(ts, "_REPO_ROOT", root)
    monkeypatch.setattr(ts, "_desktop_exe", lambda: None)
    monkeypatch.delenv("LUCKYD_CLI", raising=False)
    assert _cli_command("") == [sys.executable, str(main)]


def test_cli_command_nothing_found_raises(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ts, "_REPO_ROOT", tmp_path / "emptyrepo2")
    monkeypatch.setattr(ts, "_desktop_exe", lambda: None)
    monkeypatch.delenv("LUCKYD_CLI", raising=False)
    with pytest.raises(FileNotFoundError, match="no LuckyD Code CLI found"):
        _cli_command("")


def test_cli_command_frozen_falls_through_to_live_main_py(monkeypatch, tmp_path) -> None:
    """Frozen with no CLI beside the exe still finds live main.py (dev runs)."""
    appdir = tmp_path / "app"
    appdir.mkdir()
    root = tmp_path / "live"
    root.mkdir()
    main = root / "main.py"
    main.write_text("# live cli")
    monkeypatch.setattr(ts, "_REPO_ROOT", root)
    monkeypatch.setattr(ts, "_desktop_exe", lambda: None)
    monkeypatch.setattr(ts, "_python_for_scripts", lambda: sys.executable)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(appdir / "LuckyDBrowser.exe"))
    monkeypatch.delenv("LUCKYD_CLI", raising=False)
    assert _cli_command("") == [sys.executable, str(main)]


def test_cli_command_live_main_py_no_interpreter_raises(monkeypatch, tmp_path) -> None:
    root = tmp_path / "live"
    root.mkdir()
    (root / "main.py").write_text("# live cli")
    monkeypatch.setattr(ts, "_REPO_ROOT", root)
    monkeypatch.setattr(ts, "_desktop_exe", lambda: None)
    monkeypatch.setattr(ts, "_python_for_scripts", lambda: None)
    monkeypatch.delenv("LUCKYD_CLI", raising=False)
    with pytest.raises(FileNotFoundError, match="no LuckyD Code CLI found"):
        _cli_command("")


# ── _agent2_dir ──────────────────────────────────────────────────────


def test_agent2_dir_ctypes_desktop(monkeypatch, tmp_path) -> None:
    checkout = tmp_path / "coding-agent"
    checkout.mkdir()
    _fake_ctypes(monkeypatch, tmp_path)
    _home_to(monkeypatch, tmp_path / "nohome")
    monkeypatch.delenv("ONEDRIVE", raising=False)
    assert _agent2_dir() == checkout


def test_agent2_dir_onedrive_env(monkeypatch, tmp_path) -> None:
    checkout = tmp_path / "Desktop" / "coding-agent"
    checkout.mkdir(parents=True)
    # Neutralize the real Known-Folder Desktop: on a dev box whose actual
    # Desktop holds a coding-agent checkout, the unpatched probe would win
    # and the test would go non-hermetic.
    _fake_ctypes(monkeypatch, tmp_path / "bogus-desktop", rc=1)
    monkeypatch.setenv("ONEDRIVE", str(tmp_path))
    _home_to(monkeypatch, tmp_path / "nohome")
    assert _agent2_dir() == checkout


def test_agent2_dir_home_fallback(monkeypatch, tmp_path) -> None:
    checkout = tmp_path / "Desktop" / "coding-agent"
    checkout.mkdir(parents=True)
    _fake_ctypes(monkeypatch, tmp_path / "bogus-desktop", rc=1)
    _home_to(monkeypatch, tmp_path)
    monkeypatch.delenv("ONEDRIVE", raising=False)
    assert _agent2_dir() == checkout


def test_agent2_dir_nothing_found(monkeypatch, tmp_path) -> None:
    _fake_ctypes(monkeypatch, tmp_path / "bogus-desktop", rc=1)
    _home_to(monkeypatch, tmp_path)
    monkeypatch.delenv("ONEDRIVE", raising=False)
    assert _agent2_dir() is None


def test_agent2_dir_ctypes_failure_falls_through(monkeypatch, tmp_path) -> None:
    checkout = tmp_path / "Desktop" / "coding-agent"
    checkout.mkdir(parents=True)
    _fake_ctypes(monkeypatch, tmp_path / "bogus-desktop", rc=1)
    _home_to(monkeypatch, tmp_path)
    monkeypatch.delenv("ONEDRIVE", raising=False)
    assert _agent2_dir() == checkout


# ── _agent_cwd / _agent2_cwd ─────────────────────────────────────────


def test_agent_cwd_override_uses_file_parent(tmp_path) -> None:
    assert _agent_cwd(str(tmp_path / "proj" / "cli.exe")) == tmp_path / "proj"


def test_agent_cwd_env_override(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LUCKYD_CLI", str(tmp_path / "w" / "main.py"))
    assert _agent_cwd("") == tmp_path / "w"


def test_agent_cwd_default_is_repo_root(monkeypatch) -> None:
    monkeypatch.delenv("LUCKYD_CLI", raising=False)
    assert _agent_cwd("") == ts._REPO_ROOT


def test_agent2_cwd_override_uses_file_parent(tmp_path) -> None:
    assert _agent2_cwd(str(tmp_path / "ca" / "main.py")) == tmp_path / "ca"


def test_agent2_cwd_prefers_checkout_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("LUCKYD_CLI2", raising=False)
    monkeypatch.setattr(ts, "_agent2_dir", lambda: tmp_path)
    assert _agent2_cwd("") == tmp_path


def test_agent2_cwd_falls_back_to_home(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("LUCKYD_CLI2", raising=False)
    monkeypatch.setattr(ts, "_agent2_dir", lambda: None)
    _home_to(monkeypatch, tmp_path)
    assert _agent2_cwd("") == tmp_path


# ── _agent2_command ──────────────────────────────────────────────────


def test_agent2_command_override_py(monkeypatch, tmp_path) -> None:
    script = tmp_path / "main.py"
    script.write_text("# agent2")
    monkeypatch.setattr(ts, "_python_for_scripts", lambda: "/usr/bin/python3")
    assert _agent2_command(str(script)) == ["/usr/bin/python3", str(script)]


def test_agent2_command_override_py_no_interpreter_raises(monkeypatch, tmp_path) -> None:
    script = tmp_path / "main.py"
    script.write_text("# agent2")
    monkeypatch.setattr(ts, "_python_for_scripts", lambda: None)
    with pytest.raises(FileNotFoundError, match="no Python"):
        _agent2_command(str(script))


def test_agent2_command_override_bat_runs_via_cmd(tmp_path) -> None:
    bat = tmp_path / "run.bat"
    bat.touch()
    assert _agent2_command(str(bat)) == ["cmd.exe", "/c", str(bat)]


def test_agent2_command_override_exe_passthrough(tmp_path) -> None:
    exe = tmp_path / "agent2.exe"
    exe.touch()
    assert _agent2_command(str(exe)) == [str(exe)]


def test_agent2_command_env_var_override(monkeypatch, tmp_path) -> None:
    bat = tmp_path / "run.bat"
    bat.touch()
    monkeypatch.setenv("LUCKYD_CLI2", str(bat))
    assert _agent2_command("") == ["cmd.exe", "/c", str(bat)]


def test_agent2_command_checkout_main_py(monkeypatch, tmp_path) -> None:
    main = tmp_path / "main.py"
    main.write_text("# agent2")
    monkeypatch.setattr(ts, "_agent2_dir", lambda: tmp_path)
    monkeypatch.delenv("LUCKYD_CLI2", raising=False)
    assert _agent2_command("") == [sys.executable, str(main)]


def test_agent2_command_no_checkout_raises(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ts, "_agent2_dir", lambda: None)
    monkeypatch.delenv("LUCKYD_CLI2", raising=False)
    with pytest.raises(FileNotFoundError, match="no 2nd agent found"):
        _agent2_command("")


def test_agent2_command_checkout_main_py_no_interpreter_raises(monkeypatch, tmp_path) -> None:
    checkout = tmp_path / "coding-agent"
    checkout.mkdir()
    (checkout / "main.py").write_text("# agent2")
    monkeypatch.setattr(ts, "_agent2_dir", lambda: checkout)
    monkeypatch.setattr(ts, "_python_for_scripts", lambda: None)
    monkeypatch.delenv("LUCKYD_CLI2", raising=False)
    with pytest.raises(FileNotFoundError, match="no 2nd agent found"):
        _agent2_command("")


def test_agent2_command_checkout_without_main_py_raises(monkeypatch, tmp_path) -> None:
    empty = tmp_path / "empty-checkout"
    empty.mkdir()
    monkeypatch.setattr(ts, "_agent2_dir", lambda: empty)
    monkeypatch.delenv("LUCKYD_CLI2", raising=False)
    with pytest.raises(FileNotFoundError, match="no 2nd agent found"):
        _agent2_command("")


# ── _find_mesh_exe ───────────────────────────────────────────────────


def test_find_mesh_exe_on_path(monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/agy")
    assert _find_mesh_exe("agy") == "/usr/bin/agy"


def test_find_mesh_exe_agy_appdata_fallback(monkeypatch, tmp_path) -> None:
    exe = tmp_path / "AppData" / "Local" / "agy" / "bin" / "agy.exe"
    exe.parent.mkdir(parents=True)
    exe.touch()
    monkeypatch.setattr(shutil, "which", lambda name: None)
    _home_to(monkeypatch, tmp_path)
    assert _find_mesh_exe("agy") == str(exe)


def test_find_mesh_exe_grok_user_install_fallback(monkeypatch, tmp_path) -> None:
    exe = tmp_path / ".grok" / "bin" / "agent.exe"
    exe.parent.mkdir(parents=True)
    exe.touch()
    monkeypatch.setattr(shutil, "which", lambda name: None)
    _home_to(monkeypatch, tmp_path)
    assert _find_mesh_exe("grok") == str(exe)


def test_find_mesh_exe_agy_no_candidates(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    _home_to(monkeypatch, tmp_path)
    assert _find_mesh_exe("agy") is None


def test_find_mesh_exe_grok_no_candidates(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    _home_to(monkeypatch, tmp_path)
    assert _find_mesh_exe("grok") is None


# ── _find_wsl_exe ────────────────────────────────────────────────────


def test_find_wsl_exe_on_path(monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/wsl")
    assert _find_wsl_exe() == "/usr/bin/wsl"


def test_find_wsl_exe_system32_fallback(monkeypatch, tmp_path) -> None:
    exe = tmp_path / "System32" / "wsl.exe"
    exe.parent.mkdir(parents=True)
    exe.touch()
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setenv("SYSTEMROOT", str(tmp_path))
    assert _find_wsl_exe() == str(exe)


def test_find_wsl_exe_missing_returns_none(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setenv("SYSTEMROOT", str(tmp_path))
    assert _find_wsl_exe() is None


# ── _wsl_muse_command ────────────────────────────────────────────────


def test_wsl_muse_command_shape(monkeypatch, tmp_path) -> None:
    exe = tmp_path / "wsl.exe"
    exe.touch()
    monkeypatch.setattr(ts, "_find_wsl_exe", lambda: str(exe))
    assert _wsl_muse_command() == [
        str(exe.resolve()),
        "-d",
        MUSE_WSL_DISTRO,
        "--",
        "bash",
        "-lic",
        MUSE_WSL_BOOT,
    ]


def test_wsl_muse_command_missing_wsl_raises(monkeypatch) -> None:
    monkeypatch.setattr(ts, "_find_wsl_exe", lambda: None)
    with pytest.raises(FileNotFoundError, match="needs WSL"):
        _wsl_muse_command()


def test_wsl_muse_command_non_file_raises(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ts, "_find_wsl_exe", lambda: str(tmp_path / "ghost-wsl.exe"))
    with pytest.raises(FileNotFoundError, match="non-file"):
        _wsl_muse_command()


# ── _mesh_shell_command ──────────────────────────────────────────────


def test_mesh_shell_command_wsl_shell_delegates(monkeypatch) -> None:
    monkeypatch.setattr(ts, "_wsl_muse_command", lambda: ["wsl-cmd"])
    assert _mesh_shell_command("mesh-muse") == ["wsl-cmd"]


def test_mesh_shell_command_appends_default_args(monkeypatch, tmp_path) -> None:
    exe = tmp_path / "hermes"
    exe.touch()
    monkeypatch.setattr(ts, "_find_mesh_exe", lambda name: str(exe))
    assert _mesh_shell_command("mesh-hermes") == [str(exe.resolve()), "chat"]


def test_mesh_shell_command_plain_exe(monkeypatch, tmp_path) -> None:
    exe = tmp_path / "codex"
    exe.touch()
    monkeypatch.setattr(ts, "_find_mesh_exe", lambda name: str(exe))
    assert _mesh_shell_command("mesh-codex") == [str(exe.resolve())]


def test_mesh_shell_command_not_installed_raises(monkeypatch) -> None:
    monkeypatch.setattr(ts, "_find_mesh_exe", lambda name: None)
    with pytest.raises(FileNotFoundError, match="not installed"):
        _mesh_shell_command("mesh-codex")


def test_mesh_shell_command_resolved_non_file_raises(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ts, "_find_mesh_exe", lambda name: str(tmp_path / "ghost"))
    with pytest.raises(FileNotFoundError, match="non-file"):
        _mesh_shell_command("mesh-codex")


# ── _shell_command dispatch ──────────────────────────────────────────


def test_shell_command_agent2_dispatch(monkeypatch, tmp_path) -> None:
    seen = {}

    def _fake(cli2_path=""):
        seen["cli2_path"] = cli2_path
        return ["agent2-cmd"]

    monkeypatch.setattr(ts, "_agent2_command", _fake)
    assert _shell_command("agent2", cli2_path="p2") == ["agent2-cmd"]
    assert seen["cli2_path"] == "p2"


def test_shell_command_mesh_dispatch(monkeypatch) -> None:
    monkeypatch.setattr(ts, "_mesh_shell_command", lambda shell: [f"{shell}-cmd"])
    assert _shell_command("mesh-codex") == ["mesh-codex-cmd"]
    assert _shell_command("muse") == ["muse-cmd"]


# ── _client_token error path ─────────────────────────────────────────


def test_client_token_broken_headers_returns_empty() -> None:
    class _RaisingHeaders(dict):
        def get(self, *args, **kwargs):
            raise OSError("headers unreadable")

    ws = SimpleNamespace(
        # non-empty: an empty dict is falsy and `or {}` would swap it out
        # before .get() is ever called.
        request=SimpleNamespace(path="", headers=_RaisingHeaders({"unrelated": "1"}))
    )
    assert _client_token(ws) == ""


# ── _spawn_pty (hand-written winpty stand-in) ─────────────────────────


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


def test_spawn_pty_system_shell(monkeypatch) -> None:
    _install_fake_winpty(monkeypatch)
    _spawn_pty(shell="powershell", cols=80, rows=24)
    inst = _FakePTYFactory.instances[-1]
    assert (inst.cols, inst.rows) == (80, 24)
    kw = inst.spawn_kwargs
    assert kw["appname"] == "powershell.exe"
    assert kw["cmdline"] == "-NoLogo -NoExit"
    assert kw["cwd"] == str(Path.home())
    parts = _env_parts(kw["env"])
    assert not any(p.startswith("LUCKYD_AGENT_SLOT=") for p in parts)


def test_spawn_pty_cmd_has_no_cmdline(monkeypatch) -> None:
    _install_fake_winpty(monkeypatch)
    _spawn_pty(shell="cmd")
    kw = _FakePTYFactory.instances[-1].spawn_kwargs
    assert kw["appname"] == "cmd.exe"
    assert kw["cmdline"] is None


def test_spawn_pty_agent_gets_workspace_cwd_and_env(monkeypatch, tmp_path) -> None:
    _install_fake_winpty(monkeypatch)
    exe = tmp_path / "cli.exe"
    exe.touch()
    _spawn_pty(shell="agent", cli_path=str(exe))
    kw = _FakePTYFactory.instances[-1].spawn_kwargs
    assert kw["cwd"] == str(tmp_path)
    parts = _env_parts(kw["env"])
    assert "LUCKYD_AGENT_SLOT=1" in parts
    assert "LUCKYD_AGENT_NAME=Agent 1" in parts
    assert "LUCKYD_AGENT_VERSION=v10.4.0" in parts


def test_spawn_pty_agent2_gets_slot2_env(monkeypatch, tmp_path) -> None:
    _install_fake_winpty(monkeypatch)
    script = tmp_path / "main.py"
    script.write_text("# agent2")
    _spawn_pty(shell="agent2", cli2_path=str(script))
    kw = _FakePTYFactory.instances[-1].spawn_kwargs
    assert kw["appname"] == sys.executable  # .py override runs under an interpreter
    assert kw["cmdline"] == str(script)
    assert kw["cwd"] == str(tmp_path)
    parts = _env_parts(kw["env"])
    assert "LUCKYD_AGENT_SLOT=2" in parts
    assert "LUCKYD_AGENT_NAME=Agent 2" in parts
    assert "LUCKYD_AGENT_VERSION=v10.4.0" in parts


def test_spawn_pty_env_block_sanitizes_bad_entries(monkeypatch) -> None:
    _install_fake_winpty(monkeypatch)
    monkeypatch.setattr(
        os,
        "environ",
        {
            "GOOD": "a\nb\rc",
            "BAD=KEY": "x",
            "": "empty-key",
            "PLAIN": "ok",
            # Path.home() on Windows reads USERPROFILE; without it expanduser()
            # raises "Could not determine home directory".
            "USERPROFILE": "C:\\Users\\test",
        },
    )
    _spawn_pty(shell="cmd")
    kw = _FakePTYFactory.instances[-1].spawn_kwargs
    parts = _env_parts(kw["env"])
    assert "GOOD=abc" in parts  # newlines/carriage returns stripped
    assert "PLAIN=ok" in parts
    assert not any(p.startswith("BAD=KEY=") for p in parts)  # '=' in key: skipped
    assert not any(p.startswith("=") for p in parts if p)  # empty key: skipped


# ── _handle: pump_out branches ────────────────────────────────────────


class _FakeWS:
    """Mutable fake websocket with an iterable message source."""

    def __init__(self, messages=(), path="", cookie=TOKEN):
        self.request = SimpleNamespace(
            path=path,
            headers={"Cookie": f"{TERM_COOKIE}={cookie}"} if cookie else {},
        )
        self._messages = messages
        self.sent = []
        self.closed = []

    def __iter__(self):
        return iter(self._messages)

    def send(self, msg):
        self.sent.append(msg)

    def close(self, **kw):
        self.closed.append(kw)


class _PumpPTY:
    """Hand-written PTY double for the pump_out loop."""

    def __init__(self, reads=(), alive=True, read_exc=None):
        self._reads = list(reads)
        self._alive = alive
        self._read_exc = read_exc
        self.written = []
        self.cancelled = False

    def isalive(self):
        return self._alive

    def read(self, blocking=False):
        if self._read_exc is not None:
            raise self._read_exc
        if self._reads:
            return self._reads.pop(0)
        return ""

    def write(self, text):
        self.written.append(text)

    def set_size(self, cols, rows):
        pass

    def cancel_io(self):
        self.cancelled = True


def _handle_with(monkeypatch, ws, pty, path="/?cols=80&rows=24&shell=cmd"):
    srv = TerminalServer(token=TOKEN)
    ws.request = SimpleNamespace(path=path, headers=ws.request.headers)
    monkeypatch.setattr(ts, "_spawn_pty", lambda *a, **k: pty)
    srv._handle(ws)
    time.sleep(0.25)  # let the daemon pump_out thread observe stop/finish
    return srv, ws, pty


def test_handle_pump_out_forwards_pty_data(monkeypatch) -> None:
    pty = _PumpPTY(reads=["hello-pty"])
    ws = _FakeWS(messages=[])
    _srv, ws, _pty = _handle_with(monkeypatch, ws, pty)
    assert "hello-pty" in ws.sent
    assert ws.closed != []  # pump_out closes the socket when the loop ends


def test_handle_pump_out_send_error_breaks(monkeypatch) -> None:
    pty = _PumpPTY(reads=["x"])
    ws = _FakeWS(messages=[])

    def _dead(msg):
        raise OSError("client gone")

    ws.send = _dead
    _srv, ws, _pty = _handle_with(monkeypatch, ws, pty)
    assert ws.closed != []  # pump_out broke out and closed the socket


def test_handle_pump_out_read_error_is_swallowed(monkeypatch) -> None:
    pty = _PumpPTY(read_exc=RuntimeError("pty exploded"))
    ws = _FakeWS(messages=[])
    _srv, ws, _pty = _handle_with(monkeypatch, ws, pty)
    assert ws.closed != []  # pump_out's except→finally still closes the socket


def test_handle_pump_out_dead_pty_breaks(monkeypatch) -> None:
    pty = _PumpPTY(alive=False)
    ws = _FakeWS(messages=[])
    _srv, ws, _pty = _handle_with(monkeypatch, ws, pty)
    assert ws.sent == []  # isalive() False → break before any read
    assert ws.closed != []


def test_handle_message_iterator_error_is_swallowed(monkeypatch) -> None:
    class _BadIterWS(_FakeWS):
        def __iter__(self):
            raise RuntimeError("socket died")

    pty = _PumpPTY(alive=False)
    ws = _BadIterWS(messages=[])
    srv, ws, _pty = _handle_with(monkeypatch, ws, pty)
    assert ws not in srv._clients  # cleanup still ran


# ── start / stop ─────────────────────────────────────────────────────


def test_start_returns_true_when_already_running() -> None:
    srv = TerminalServer(port=0)
    gate = threading.Event()
    thread = threading.Thread(target=gate.wait, daemon=True)
    thread.start()
    srv._thread = thread
    try:
        assert srv.start() is True  # short-circuits before any import/bind
    finally:
        gate.set()
        thread.join(timeout=2)


def test_start_binds_and_serves(monkeypatch) -> None:
    calls: dict = {}
    gate = threading.Event()

    class _FakeServer:
        def serve_forever(self):
            calls["served"] = True
            gate.wait(5)

        def shutdown(self):
            calls["shutdown"] = True
            gate.set()

    def _fake_serve(handler, host, port, max_size=None):
        calls["serve_args"] = (handler, host, port, max_size)
        return _FakeServer()

    monkeypatch.setitem(sys.modules, "websockets.sync.server", SimpleNamespace(serve=_fake_serve))
    srv = TerminalServer(host="127.0.0.1", port=0)
    try:
        assert srv.start() is True
        assert srv.running is True
        handler, host, port, max_size = calls["serve_args"]
        assert handler == srv._handle
        assert (host, port, max_size) == ("127.0.0.1", 0, 1 << 20)
    finally:
        srv.stop()
        gate.set()  # belt-and-braces: let the daemon thread exit
    assert calls.get("shutdown") is True


def test_stop_shuts_down_server_and_closes_clients() -> None:
    srv = TerminalServer(port=0)
    shutdown: list = []

    class _Server:
        def shutdown(self):
            shutdown.append(True)

    srv._server = _Server()
    ws = _FakeWS()
    srv._clients.add(ws)
    srv.stop()
    assert shutdown == [True]
    assert ws.closed != []
    assert srv._clients == set()
