"""Regression tests for platform-tile launch paths (Deck Studio).

The Deck Studio tile shipped with ``cwd: "%APPDIR%\\studio"`` while the
PyInstaller spec collects that payload into ``<app>\\_internal\\studio``
(``sys._MEIPASS``). The configured directory therefore never existed in an
installed build, ``subprocess.Popen`` died with
``NotADirectoryError(winerror 267)`` and the tile stayed dark -- the browser
showed "Deck Studio is not responding" no matter how often it retried.

These tests pin the bundled-data token (``%RESDIR%``) and the fallback that
keeps a stale cwd from killing the launch.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from browser_core.tile_registry import (
    CONFIG_PATH,
    Tile,
    _app_dir,
    _expand,
    _last_attempt,
    _launched,
    _registry_lock,
    _resolve_cwd,
    _resolve_exe,
    _resource_dir,
    ensure_autostart,
    load_tiles,
)

# ── the bundled-data folder ──────────────────────────────────────────


def test_resource_dir_holds_the_bundled_studio_payload():
    """%RESDIR% must point at the folder that actually has studio/."""
    res = Path(_resource_dir())
    assert res.is_dir()
    assert (res / "studio" / "studio-server.js").is_file()


def test_resdir_token_is_not_hijacked_by_an_env_var(monkeypatch):
    """A stray RESDIR env var must not win over the bundle location."""
    monkeypatch.setenv("RESDIR", os.path.join(os.sep, "nope"))
    assert Path(_expand(r"%RESDIR%\studio")) == Path(_resource_dir()) / "studio"
    assert Path(_expand("{resdir}/studio")) == Path(_resource_dir()) / "studio"


# ── cwd resolution ───────────────────────────────────────────────────


def test_existing_configured_cwd_is_used_as_is(tmp_path):
    real = tmp_path / "studio"
    real.mkdir()
    tile = Tile(
        id="t",
        name="t",
        icon="",
        url="http://127.0.0.1:1/",
        command=("node", "studio-server.js"),
        cwd=str(real),
    )
    assert _resolve_cwd(tile) == str(real)


def test_missing_cwd_falls_back_to_the_bundle(tmp_path, monkeypatch):
    """The exact v9.0.0 bug: %APPDIR%\\studio does not exist once installed."""
    appdir = tmp_path / "app"
    resdir = appdir / "_internal"
    appdir.mkdir()
    (resdir / "studio").mkdir(parents=True)
    (resdir / "studio" / "studio-server.js").write_text("// stub", encoding="utf-8")
    (appdir / "LuckyDBrowser.exe").write_text("", encoding="utf-8")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(resdir), raising=False)
    monkeypatch.setattr(sys, "executable", str(appdir / "LuckyDBrowser.exe"), raising=False)

    assert _app_dir() == str(appdir)
    assert _resource_dir() == str(resdir)

    stale = Tile(
        id="deck-studio",
        name="Deck Studio",
        icon="",
        url="http://127.0.0.1:8770/",
        health_url="http://127.0.0.1:8770/health",
        autostart=True,
        command=("node", "studio-server.js", "8770"),
        cwd=_expand(r"%APPDIR%\studio"),  # what the shipped config said
    )
    assert stale.cwd == str(appdir / "studio")
    assert not Path(stale.cwd).is_dir(), "this is what made Popen raise 267"
    assert _resolve_cwd(stale) == str(resdir / "studio")


def test_cwd_fallback_ignores_a_folder_without_the_script(tmp_path):
    """A fallback dir that lacks the script is not good enough."""
    tile = Tile(
        id="t",
        name="t",
        icon="",
        url="http://127.0.0.1:1/",
        command=("node", "definitely-not-here-server.js"),
        cwd=str(tmp_path / "gone"),
    )
    # Nothing anywhere holds that script, so the configured value is returned
    # unchanged and the failure is logged with the resolved cmd/cwd.
    assert _resolve_cwd(tile) == str(tmp_path / "gone")


# ── autostart still launches ─────────────────────────────────────────


def test_autostart_launches_with_a_stale_cwd(tmp_path):
    """Autostart must not die with winerror 267 on a stale cwd."""
    tile = Tile(
        id="__test_stale_cwd__",
        name="stale",
        icon="",
        url="not-a-url",  # probe_tile() reports down, so the launch proceeds
        autostart=True,
        command=(sys.executable, "-c", "pass"),
        cwd=str(tmp_path / "gone"),
    )
    with _registry_lock:
        _launched.pop(tile.id, None)
        _last_attempt.pop(tile.id, None)
    proc = None
    try:
        ensure_autostart([tile])
        with _registry_lock:
            proc = _launched.pop(tile.id, None)
        assert proc is not None, "tile was not launched"
        assert proc.wait(timeout=60) == 0
    finally:
        with _registry_lock:
            _launched.pop(tile.id, None)
            _last_attempt.pop(tile.id, None)
        if proc is not None and proc.poll() is None:
            proc.kill()


# ── executable + config plumbing ─────────────────────────────────────


def test_resolve_exe_keeps_explicit_paths(tmp_path):
    explicit = str(tmp_path / "node.exe")
    assert _resolve_exe(explicit) == explicit
    assert _resolve_exe("") == ""


def test_resolve_exe_returns_a_usable_bare_name():
    """Either an existing absolute path, or the bare name unchanged."""
    resolved = _resolve_exe("node")
    assert resolved == "node" or Path(resolved).is_file()


def test_command_args_expand_tokens(tmp_path):
    cfg = tmp_path / "platform_tiles.json"
    cfg.write_text(
        json.dumps(
            {
                "tiles": [
                    {
                        "id": "x",
                        "name": "X",
                        "icon": "x",
                        "url": "http://127.0.0.1:1/",
                        "autostart": True,
                        "command": [r"%RESDIR%\node.exe", "studio-server.js"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    tiles = load_tiles(cfg)
    assert Path(tiles[0].command[0]) == Path(_resource_dir()) / "node.exe"


def test_shipped_deck_studio_tile_targets_the_bundle_dir():
    """The config that ships with the app must not use %APPDIR%\\studio."""
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    entry = next(t for t in raw["tiles"] if t["id"] == "deck-studio")
    assert "%RESDIR%" in entry["cwd"]
    assert entry["health_url"].endswith("/health")
    resolved = Path(_expand(entry["cwd"]))
    assert resolved.is_dir()
    assert (resolved / "studio-server.js").is_file()
