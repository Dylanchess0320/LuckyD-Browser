"""Google Jules CLI in the Agent Mesh (10.1).

mesh-jules rides alongside OpenCode, MiniMax Code, and Cline — it coexists
with them and must never displace them. These tests pin the wiring:
shell allowlist entries, exe resolution, dock metadata, and availability
reporting.
"""

from pathlib import Path


def test_jules_shells_allowlisted() -> None:
    """mesh-jules (and the bare `jules` alias) must be allowlisted shells.

    2026-09-24: mesh-aider / aider ride the same mesh rails.
    """
    from browser.browser_core.terminal_server import MESH_SHELLS, SHELLS

    assert "mesh-jules" in SHELLS
    assert "jules" in SHELLS
    assert MESH_SHELLS["mesh-jules"] == "jules"
    assert MESH_SHELLS["jules"] == "jules"
    assert "mesh-aider" in SHELLS
    assert "aider" in SHELLS
    assert MESH_SHELLS["mesh-aider"] == "aider"
    assert MESH_SHELLS["aider"] == "aider"


def test_jules_mesh_dock_entry() -> None:
    """The dock chip metadata must exist with the 4-tuple shape.

    2026-09-24: Aider's dock chip gets the same guarantee.
    """
    from browser.browser_core.terminal_page import _MESH_AGENTS, _SHELL_LABELS

    entry = _MESH_AGENTS["mesh-jules"]
    assert isinstance(entry, tuple) and len(entry) == 4
    label, emoji, accent, blurb = entry
    assert label == "Jules"
    assert emoji and accent.startswith("#") and blurb
    assert _SHELL_LABELS.get("mesh-jules") == "Jules"
    assert _SHELL_LABELS.get("jules") == "Jules"

    aider = _MESH_AGENTS["mesh-aider"]
    assert isinstance(aider, tuple) and len(aider) == 4
    alabel, aemoji, aaccent, ablurb = aider
    assert alabel == "Aider"
    assert aemoji and aaccent.startswith("#") and ablurb
    assert _SHELL_LABELS.get("mesh-aider") == "Aider"
    assert _SHELL_LABELS.get("aider") == "Aider"


def test_jules_mesh_emoji_unused() -> None:
    """Jules' dock emoji must not collide with another agent's.

    2026-09-24: same guarantee for Aider's emoji.
    """
    from browser.browser_core.terminal_page import _MESH_AGENTS

    emojis = [meta[1] for shell, meta in _MESH_AGENTS.items() if shell != "mesh-jules"]
    assert _MESH_AGENTS["mesh-jules"][1] not in emojis
    aider_emojis = [meta[1] for shell, meta in _MESH_AGENTS.items() if shell != "mesh-aider"]
    assert _MESH_AGENTS["mesh-aider"][1] not in aider_emojis


def test_jules_shell_command_resolves(monkeypatch, tmp_path: Path) -> None:
    """mesh-jules spawns the real `jules` CLI, no extra args."""
    from browser.browser_core.terminal_server import _mesh_shell_command

    mock_jules = tmp_path / "jules.exe"
    mock_jules.touch()
    monkeypatch.setattr(
        "browser.browser_core.terminal_server._find_mesh_exe",
        lambda exe: str(mock_jules) if exe == "jules" else None,
    )
    cmd = _mesh_shell_command("mesh-jules")
    assert len(cmd) == 1 and cmd[0].lower().endswith("jules.exe")
    # Bare alias resolves to the same executable.
    cmd2 = _mesh_shell_command("jules")
    assert cmd2[0].lower().endswith("jules.exe")


def test_jules_availability_reported(monkeypatch) -> None:
    """mesh_shells_available() reflects whether `jules` is on PATH."""
    from browser.browser_core.terminal_server import mesh_shells_available

    monkeypatch.setattr(
        "browser.browser_core.terminal_server._find_mesh_exe",
        lambda exe: "/fake/jules" if exe == "jules" else None,
    )
    avail = mesh_shells_available()
    assert avail["mesh-jules"] is True
    assert avail["jules"] is True

    monkeypatch.setattr("browser.browser_core.terminal_server._find_mesh_exe", lambda exe: None)
    avail = mesh_shells_available()
    assert avail["mesh-jules"] is False
    assert avail["jules"] is False


def test_jules_coexists_with_other_agents() -> None:
    """Adding Jules must not disturb OpenCode, MiniMax Code, or Cline.

    2026-09-24: Aider (mesh-aider) joins the same way — it coexists too.
    """
    from browser.browser_core.terminal_page import _MESH_AGENTS
    from browser.browser_core.terminal_server import MESH_SHELLS, SHELLS

    for shell in ("mesh-opencode", "mesh-mcode", "mesh-mmx", "mesh-cline", "mesh-aider"):
        assert shell in SHELLS
        assert shell in MESH_SHELLS
        assert shell in _MESH_AGENTS
