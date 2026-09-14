"""Coverage tests for browser_core/tile_registry.py — launch/cwd fallbacks,
autostart, probes, shutdown, rendering.

Complements tests/test_platform_tiles.py (the Deck Studio regression
pins); this file targets the remaining uncovered ranges: frozen config
resolution, runtime-hint exe search, frozen %APPDIR% expansion, probe
branches, autostart guards, shutdown paths, and rendering.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

import urllib.request

from browser_core import tile_registry
from browser_core.tile_registry import (
    Tile,
    _debug_log,
    _expand,
    _last_attempt,
    _launched,
    _registry_lock,
    _resolve_config_path,
    _resolve_exe,
    ensure_autostart,
    load_tiles,
    probe_all,
    probe_tile,
    render_all_tiles,
    shutdown_autostart,
    tile_anchor,
)


def _reset_autostart(*ids: str) -> None:
    with _registry_lock:
        for tid in ids:
            _launched.pop(tid, None)
            _last_attempt.pop(tid, None)


# ── _debug_log ───────────────────────────────────────────────────────


def test_debug_log_never_raises(monkeypatch):
    def boom():
        raise OSError("no temp dir")

    monkeypatch.setattr("tempfile.gettempdir", boom)
    _debug_log("hello")  # must not raise


def test_debug_log_writes_to_temp_log(monkeypatch, tmp_path):
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    _debug_log("marker-123")
    assert "marker-123" in (tmp_path / "luckyd_tiles.log").read_text(encoding="utf-8")


# ── _resolve_config_path (frozen layouts) ────────────────────────────


def _hide_real_config(monkeypatch):
    """Make the shipped platform_tiles.json look absent so the _MEIPASS fallbacks run."""
    real_exists = Path.exists
    here = Path(tile_registry.__file__).resolve().with_name("platform_tiles.json")

    def fake_exists(self):
        if self.resolve() == here:
            return False
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", fake_exists)


def test_resolve_config_path_frozen_first_candidate(monkeypatch, tmp_path):
    meipass = tmp_path / "mp"
    cand = meipass / "browser" / "browser_core"
    cand.mkdir(parents=True)
    (cand / "platform_tiles.json").write_text("{}", encoding="utf-8")
    _hide_real_config(monkeypatch)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    assert _resolve_config_path() == cand / "platform_tiles.json"


def test_resolve_config_path_frozen_second_candidate(monkeypatch, tmp_path):
    meipass = tmp_path / "mp"
    cand2 = meipass / "browser_core"
    cand2.mkdir(parents=True)
    (cand2 / "platform_tiles.json").write_text("{}", encoding="utf-8")
    _hide_real_config(monkeypatch)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    assert _resolve_config_path() == cand2 / "platform_tiles.json"


def test_resolve_config_path_frozen_nothing_found(monkeypatch, tmp_path):
    _hide_real_config(monkeypatch)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "empty"), raising=False)
    assert _resolve_config_path() == Path(tile_registry.__file__).with_name("platform_tiles.json")


def test_resolve_config_path_no_meipass(monkeypatch):
    _hide_real_config(monkeypatch)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert _resolve_config_path() == Path(tile_registry.__file__).with_name("platform_tiles.json")


# ── _resolve_exe runtime hints ───────────────────────────────────────


def test_resolve_exe_searches_runtime_hints(monkeypatch, tmp_path):
    """The hint loop: which() misses, but a hint dir holds the exe."""
    monkeypatch.setattr(shutil, "which", lambda name: None)
    hint_dir = tmp_path / "nodejs"
    hint_dir.mkdir()
    (hint_dir / "node.exe").write_text("stub", encoding="utf-8")
    # The shipped hints are Windows %VAR% paths; point the loop at a POSIX
    # dir instead — the search logic under test is identical.
    monkeypatch.setattr(tile_registry, "_RUNTIME_HINTS", (str(hint_dir),))
    assert _resolve_exe("node") == str(hint_dir / "node.exe")


def test_resolve_exe_returns_bare_name_when_nowhere_found(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert _resolve_exe("definitely-not-a-real-tool-xyz") == "definitely-not-a-real-tool-xyz"
    # A name that already ends in .exe is not doubled.
    assert _resolve_exe("tool.exe") == "tool.exe"


# ── _expand frozen branch ────────────────────────────────────────────


def test_expand_appdir_dev(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert _expand(r"%APPDIR%\studio") == str(
        Path(tile_registry.__file__).resolve().parent.parent / "studio"
    )


def test_expand_appdir_frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    exe = tmp_path / "app" / "LuckyDBrowser.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", str(exe), raising=False)
    assert _expand(r"%APPDIR%\studio") == str(exe.parent / "studio")


def test_expand_skips_separator_normalization_on_windows_sep(monkeypatch):
    monkeypatch.setattr(os, "sep", "\\")
    assert _expand("a\\b") == "a\\b"


# ── load_tiles edge cases ────────────────────────────────────────────


def _cfg(tmp_path, tiles) -> Path:
    p = tmp_path / "platform_tiles.json"
    p.write_text(json.dumps({"tiles": tiles}), encoding="utf-8")
    return p


def test_load_tiles_missing_file_returns_empty(tmp_path):
    assert load_tiles(tmp_path / "nope.json") == []


def test_load_tiles_malformed_returns_empty(tmp_path):
    p = tmp_path / "platform_tiles.json"
    p.write_text("{bad json", encoding="utf-8")
    assert load_tiles(p) == []


def test_load_tiles_skips_bad_and_disabled_entries(tmp_path):
    p = _cfg(
        tmp_path,
        [
            "junk",
            {"id": "no-url"},
            {"url": "http://127.0.0.1:1/"},
            {"id": "off", "url": "http://127.0.0.1:2/", "enabled": False},
            {"id": "ok", "name": "OK", "url": "http://127.0.0.1:3/"},
        ],
    )
    tiles = load_tiles(p)
    assert [t.id for t in tiles] == ["ok"]
    assert tiles[0].name == "OK"


# ── probe_tile ───────────────────────────────────────────────────────


class _FakeHTTPResponse:
    def __init__(self, status=200, body=b""):
        self.status = status
        self._body = body

    def read(self, n=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _svc_tile() -> Tile:
    return Tile(
        id="svc",
        name="Svc",
        icon="",
        url="http://127.0.0.1:9/",
        health_url="http://127.0.0.1:9/health",
    )


def _fake_urlopen(status=200, body=b""):
    def opener(url, timeout=None):
        assert url == "http://127.0.0.1:9/health"
        return _FakeHTTPResponse(status, body)

    return opener


def test_probe_tile_health_up_with_tool_list(monkeypatch):
    body = json.dumps({"tools": ["a", "b", "c"]}).encode()
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(200, body))
    assert probe_tile(_svc_tile()) == {"id": "svc", "up": True, "tools": 3}


def test_probe_tile_health_up_with_scalar_tools(monkeypatch):
    body = json.dumps({"tools": 7}).encode()
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(200, body))
    assert probe_tile(_svc_tile())["tools"] == 7


def test_probe_tile_health_up_non_json_body(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(200, b"ok"))
    assert probe_tile(_svc_tile()) == {"id": "svc", "up": True}


def test_probe_tile_health_up_json_without_tools(monkeypatch):
    body = json.dumps({"ok": True}).encode()
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(200, body))
    assert probe_tile(_svc_tile()) == {"id": "svc", "up": True}


def test_probe_tile_health_non_200(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(503, b""))
    assert probe_tile(_svc_tile()) == {"id": "svc", "up": False}


def test_probe_tile_health_unreachable(monkeypatch):
    def boom(url, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert probe_tile(_svc_tile()) == {"id": "svc", "up": False}


def test_probe_tile_no_health_url_assumes_external():
    up = Tile(id="a", name="a", icon="", url="https://example.com/")
    assert probe_tile(up) == {"id": "a", "up": True}
    down = Tile(id="b", name="b", icon="", url="lucky://internal")
    assert probe_tile(down) == {"id": "b", "up": False}


def test_probe_all_maps_by_id():
    tiles = [
        Tile(id="a", name="a", icon="", url="https://example.com/"),
        Tile(id="b", name="b", icon="", url="lucky://x"),
    ]
    assert probe_all(tiles) == {
        "a": {"id": "a", "up": True},
        "b": {"id": "b", "up": False},
    }


# ── shutdown_autostart ───────────────────────────────────────────────


class _FakeProc:
    def __init__(self):
        self.terminated = False
        self.killed = False
        self._poll = 0

    def poll(self):
        return self._poll

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True
        self._poll = -9


class _StragglerProc(_FakeProc):
    def poll(self):
        return None  # never exits on its own


def test_shutdown_autostart_terminates_and_clears():
    procs = {"t1": _FakeProc(), "t2": _FakeProc()}
    with _registry_lock:
        _launched.update(procs)
    shutdown_autostart()
    assert all(p.terminated for p in procs.values())
    assert not any(p.killed for p in procs.values())  # exited cleanly: no kill
    with _registry_lock:
        assert _launched == {}


def test_shutdown_autostart_kills_stragglers():
    proc = _StragglerProc()
    with _registry_lock:
        _launched["strag"] = proc
    shutdown_autostart()
    assert proc.terminated and proc.killed
    with _registry_lock:
        assert _launched == {}


def test_shutdown_autostart_deadline_break(monkeypatch):
    calls = {"n": 0}

    def fake_monotonic():
        calls["n"] += 1
        return 1000.0 if calls["n"] == 1 else 9999.0

    monkeypatch.setattr(time, "monotonic", fake_monotonic)
    proc = _StragglerProc()
    with _registry_lock:
        _launched["strag2"] = proc
    shutdown_autostart()  # wait loop breaks immediately; kill loop still runs
    assert proc.killed
    with _registry_lock:
        assert _launched == {}


def test_shutdown_autostart_empty_is_noop():
    with _registry_lock:
        _launched.clear()
    shutdown_autostart()  # must not raise


# ── ensure_autostart guards ──────────────────────────────────────────


def _auto_tile(tid="t", **kw) -> Tile:
    base = dict(
        id=tid,
        name=tid,
        icon="",
        url="http://127.0.0.1:1/",
        autostart=True,
        command=(sys.executable, "-c", "pass"),
    )
    base.update(kw)
    return Tile(**base)


def test_ensure_autostart_skips_non_autostart_tiles():
    _reset_autostart("skip1")
    ensure_autostart([_auto_tile("skip1", autostart=False)])
    with _registry_lock:
        assert "skip1" not in _launched


def test_ensure_autostart_skips_tiles_without_command():
    _reset_autostart("skip2")
    ensure_autostart([_auto_tile("skip2", command=())])
    with _registry_lock:
        assert "skip2" not in _launched


def test_ensure_autostart_rate_limits_retries():
    tile = _auto_tile("rl", url="lucky://down")  # probe reports down
    _reset_autostart("rl")
    try:
        ensure_autostart([tile])  # launches
        with _registry_lock:
            proc = _launched["rl"]
        assert proc.wait(timeout=60) == 0
        # Next poll reaps the dead child, then the retry window skips relaunch.
        ensure_autostart([tile])
        with _registry_lock:
            assert "rl" not in _launched
    finally:
        _reset_autostart("rl")


def test_ensure_autostart_skips_running_child():
    tile = _auto_tile("run", url="lucky://down")
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    with _registry_lock:
        _launched["run"] = proc
        _last_attempt.pop("run", None)
    try:
        ensure_autostart([tile])
        with _registry_lock:
            assert _launched["run"] is proc  # not replaced
    finally:
        proc.kill()
        proc.wait()
        _reset_autostart("run")


def test_ensure_autostart_skips_when_service_already_up():
    tile = _auto_tile("up", url="https://example.com/")  # probe reports up
    _reset_autostart("up")
    ensure_autostart([tile])
    with _registry_lock:
        assert "up" not in _launched
    _reset_autostart("up")


def test_ensure_autostart_launch_failure_is_logged_not_raised(monkeypatch):
    tile = _auto_tile("boom", url="lucky://down")
    _reset_autostart("boom")

    def boom_popen(*a, **k):
        raise RuntimeError("nope")

    monkeypatch.setattr(subprocess, "Popen", boom_popen)
    ensure_autostart([tile])  # must not raise
    with _registry_lock:
        assert "boom" not in _launched
    _reset_autostart("boom")


# ── rendering ────────────────────────────────────────────────────────


def test_tile_anchor_escapes_and_marks_status():
    tile = Tile(
        id="x",
        name='A <b>& "q"',
        icon="x",
        url="https://example.com/?a=1&b=2",
        extra_class="hq",
    )
    html_out = tile_anchor(tile, {"up": True})
    assert 'class="tile hq"' in html_out
    assert "<b>" not in html_out
    assert "&lt;b&gt;" in html_out
    assert "#34d399" in html_out  # up dot
    assert "running" in html_out
    down = tile_anchor(tile, {"up": False})
    assert "#9aa1b5" in down  # down dot
    assert "not responding" in down
    assert "not responding" in tile_anchor(tile)  # no status at all


def test_render_all_tiles_probes_and_renders(monkeypatch):
    tiles = [
        Tile(id="a", name="A", icon="a", url="https://a.example/"),
        Tile(id="b", name="B", icon="b", url="https://b.example/"),
    ]
    monkeypatch.setattr(tile_registry, "load_tiles", lambda config_path=None: tiles)
    monkeypatch.setattr(
        tile_registry,
        "probe_tile",
        lambda t, timeout=1.5: {"id": t.id, "up": t.id == "a"},
    )
    out = render_all_tiles()
    assert out.count('class="tile"') == 2
    assert ">A<" in out and ">B<" in out
    assert "#34d399" in out and "#9aa1b5" in out
