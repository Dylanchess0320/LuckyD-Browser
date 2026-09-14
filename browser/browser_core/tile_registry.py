r"""TileRegistry — config-driven platform tiles for the LuckyD dashboard.

Phase 2 of the platform plan: "adding a tool = adding one config entry,
zero Python changes." This module is deliberately dependency-free (stdlib
only, no Qt imports) so it can ship and be unit-tested without touching
the running browser. Wiring it into dashboard.py is a 3-line change:

    from .tile_registry import load_tiles, probe_tile, tile_anchor
    for t in load_tiles():
        status = probe_tile(t)
        tiles_html += tile_anchor(t, status)

Config: platform_tiles.json next to this file.
    { "tiles": [ { id, name, icon, url, health_url, autostart, enabled } ] }

Path tokens usable in url/command/cwd:
    %APPDIR% / {app}    -> folder holding the exe (dev: browser package root)
    %RESDIR% / {resdir} -> folder bundled *data* lives in (frozen: _MEIPASS,
                           which is <app>/_internal for a onedir build)
Use %RESDIR% for anything the PyInstaller spec ships: %APPDIR% is only the
exe's own folder, so %APPDIR%\studio does not exist in an installed build.

A tile with enabled=false is hidden but remembered — flipping it on later
is a JSON edit, not a code change.
"""

from __future__ import annotations

import atexit
import contextlib
import html
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path


def _debug_log(msg: str) -> None:
    """Best-effort trace of registry decisions (temp dir), so silent
    failures inside frozen builds can be diagnosed from disk."""
    try:
        import tempfile

        with open(
            os.path.join(tempfile.gettempdir(), "luckyd_tiles.log"),
            "a",
            encoding="utf-8",
        ) as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


def _resolve_config_path() -> Path:
    """Locate platform_tiles.json in both dev and frozen (PyInstaller) layouts.

    Dev:  the JSON sits next to this file.
    Frozen: this module lives in the PYZ archive, so __file__ points at a
    virtual location — the real config ships as data under
    <_MEIPASS>/browser/browser_core/.
    """
    here = Path(__file__).with_name("platform_tiles.json")
    if here.exists():
        return here
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cand = Path(meipass) / "browser" / "browser_core" / "platform_tiles.json"
        if cand.exists():
            return cand
        cand2 = Path(meipass) / "browser_core" / "platform_tiles.json"
        if cand2.exists():
            return cand2
    return here


CONFIG_PATH = _resolve_config_path()
_debug_log(
    f"tile_registry import: CONFIG_PATH={CONFIG_PATH} "
    f"exists={CONFIG_PATH.exists()} meipass={getattr(sys, '_MEIPASS', None)!r} "
    f"file={__file__!r}"
)
PROBE_TIMEOUT_SEC = 1.5
AUTOSTART_RETRY_SEC = 60.0


@dataclass(frozen=True)
class Tile:
    id: str
    name: str
    icon: str
    url: str
    health_url: str = ""
    autostart: bool = False
    enabled: bool = True
    extra_class: str = ""
    command: tuple[str, ...] = field(default_factory=tuple)
    cwd: str = ""


def _app_dir() -> str:
    """Folder holding the running exe (frozen) or the browser package root (dev).

    This is what %APPDIR% / {app} expand to. PyInstaller onedir builds keep
    nothing here but the exe and _internal, so bundled payloads are reached
    through _resource_dir() instead.
    """
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve().parent)
    return str(Path(__file__).resolve().parent.parent)


def _resource_dir() -> str:
    """Folder the app's bundled *data* files land in.

    Frozen: sys._MEIPASS -- for a PyInstaller onedir build that is
    <app>/_internal, exactly where the spec's datas entries (studio/,
    assets/, project/, ...) are collected. Dev: the browser package root,
    which holds the same payloads, so one config entry serves both layouts.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return str(Path(meipass))
    return str(Path(__file__).resolve().parent.parent)


# Runtimes the frozen app may need but not have on its inherited PATH.
_RUNTIME_HINTS = (
    r"%ProgramFiles%\nodejs",
    r"%ProgramFiles(x86)%\nodejs",
    r"%LOCALAPPDATA%\Programs\nodejs",
    r"%LOCALAPPDATA%\Programs\node",
    r"%APPDATA%\npm",
)


def _resolve_exe(name: str) -> str:
    """Best-effort absolute path for a tile command's executable.

    A GUI app inherits the PATH its launcher had at logon, so a runtime
    installed afterwards (node, python, a CLI) can be invisible even though
    it works in a fresh terminal. Look past PATH at the usual Windows install
    locations before handing the bare name to the OS. Never raises.
    """
    if not name or os.path.dirname(name):
        return name  # empty, or an explicit path: trust the config as-is
    found = shutil.which(name)
    if found:
        return found
    exe = name if name.lower().endswith(".exe") else f"{name}.exe"
    for hint in _RUNTIME_HINTS:
        cand = Path(os.path.expandvars(hint)) / exe
        with contextlib.suppress(OSError):
            if cand.is_file():
                return str(cand)
    return name


def _script_arg(command: tuple[str, ...]) -> str:
    """The script a tile command runs (e.g. studio-server.js in
    ``node studio-server.js 8770``), used to validate a fallback cwd."""
    for arg in command[1:]:
        if not arg.startswith("-") and Path(arg).suffix:
            return arg
    return ""


def _resolve_cwd(tile: Tile) -> str | None:
    """Working directory to launch a tile command in.

    Prefers the configured cwd. When it does not exist -- the frozen case is
    ``cwd: "%APPDIR%\\studio"`` while PyInstaller collected the payload into
    ``<app>\\_internal\\studio``, which made Popen die with
    NotADirectoryError(winerror 267) so the tile never came up -- fall back to
    the bundled-data folder (then the app folder) whenever the command's
    script actually lives there.
    """
    if tile.cwd:
        if Path(tile.cwd).is_dir():
            return tile.cwd
        _debug_log(f"{tile.id}: cwd missing {tile.cwd!r} - trying bundle fallbacks")
    script = _script_arg(tile.command)
    script_is_path = bool(script) and Path(script).is_absolute()
    roots = (Path(_resource_dir()), Path(_app_dir()), Path(_app_dir()) / "_internal")
    base = Path(tile.cwd).name if tile.cwd else ""
    candidates: list[Path] = []
    for root in roots:
        candidates.append(root)
        # An older config may point at a payload subfolder that moved with the
        # bundle (e.g. %APPDIR%\studio whose files now live in _internal\).
        if base and base not in ("", ".", ".."):
            candidates.append(root / base)
    for cand in dict.fromkeys(candidates):  # dedupe, keep order
        if not cand.is_dir():
            continue
        if script and not script_is_path and not (cand / script).is_file():
            continue
        _debug_log(f"{tile.id}: cwd -> {str(cand)!r} (script={script or '-'})")
        return str(cand)
    return tile.cwd or None


def _expand(p: str) -> str:
    out = (p or "").strip()
    if not out:
        return ""
    # Tokens first: expandvars would otherwise swallow a %RESDIR%-shaped name
    # if the machine happened to export an env var called that.
    if "%RESDIR%" in out or "{resdir}" in out.lower():
        out = out.replace("%RESDIR%", _resource_dir()).replace("{resdir}", _resource_dir())
    out = os.path.expandvars(os.path.expanduser(out))
    # %APPDIR% = folder containing the running exe (frozen) or the browser
    # package root (dev) — lets tiles reference files bundled with the app.
    if "%APPDIR%" in out or "{app}" in out.lower():
        if getattr(sys, "frozen", False):
            appdir = str(Path(sys.executable).resolve().parent)
        else:
            appdir = str(Path(__file__).resolve().parent.parent)
        out = out.replace("%APPDIR%", appdir)
        out = out.replace("{app}", appdir)
    return out


def load_tiles(config_path: Path | None = None) -> list[Tile]:
    """Load enabled tiles from the registry config. Never raises: a missing
    or malformed config degrades to an empty tile list so the dashboard
    still renders."""
    path = config_path or CONFIG_PATH
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        entries = raw.get("tiles", [])
    except Exception as exc:
        _debug_log(f"load_tiles FAILED path={path!r}: {exc!r}")
        return []
    tiles = []
    for e in entries:
        if not isinstance(e, dict) or not e.get("enabled", True):
            continue
        if not e.get("id") or not e.get("url"):
            continue
        tiles.append(
            Tile(
                id=str(e["id"]),
                name=str(e.get("name", e["id"])),
                icon=str(e.get("icon", "🔗")),
                url=str(e["url"]),
                health_url=str(e.get("health_url", "")),
                autostart=bool(e.get("autostart", False)),
                enabled=True,
                extra_class=str(e.get("extra_class", "")),
                command=tuple(_expand(str(c)) for c in e.get("command", []) or []),
                cwd=_expand(str(e.get("cwd", "") or "")),
            )
        )
    return tiles


def probe_tile(tile: Tile, timeout: float = PROBE_TIMEOUT_SEC) -> dict:
    """Health-probe one tile's service. Returns {'up': bool} (+ 'tools' when a
    LuckyD harness answers, matching the existing dashboard pill contract)."""
    result: dict = {"id": tile.id, "up": False}
    if not tile.health_url:
        # No health endpoint declared — assume external URLs are reachable.
        result["up"] = tile.url.startswith(("http://", "https://"))
        return result
    try:
        with urllib.request.urlopen(tile.health_url, timeout=timeout) as resp:  # nosec B310
            result["up"] = resp.status == 200
            if result["up"]:
                body = resp.read(4096)
                try:
                    data = json.loads(body)
                    tools = data.get("tools")
                    if tools is not None:
                        result["tools"] = len(tools) if isinstance(tools, list) else tools
                except Exception:
                    pass
    except Exception:
        result["up"] = False
    return result


def probe_all(tiles: list[Tile] | None = None) -> dict[str, dict]:
    return {t.id: probe_tile(t) for t in (tiles if tiles is not None else load_tiles())}


# ── autostart ──────────────────────────────────────────────────────────
_last_attempt: dict[str, float] = {}
_launched: dict[str, subprocess.Popen] = {}
# ensure_autostart runs on the Control API's request threads — guard the
# shared launch tables so two /status polls can't double-spawn a tile.
_registry_lock = threading.Lock()


def _reap_finished() -> None:
    """Drop dead children from the launch table (reaps zombies on POSIX)."""
    for tid, proc in list(_launched.items()):
        if proc.poll() is not None:
            with contextlib.suppress(Exception):
                proc.wait(timeout=0)
            del _launched[tid]


def shutdown_autostart() -> None:
    """Terminate every tile process we launched. Best-effort; never raises."""
    with _registry_lock:
        procs = list(_launched.values())
        _launched.clear()
    for proc in procs:
        with contextlib.suppress(Exception):
            proc.terminate()
    # Give them a beat, then force-kill stragglers so the browser can exit.
    deadline = time.monotonic() + 3.0
    for proc in procs:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        with contextlib.suppress(Exception):
            proc.wait(timeout=remaining)
    for proc in procs:
        with contextlib.suppress(Exception):
            if proc.poll() is None:
                proc.kill()


atexit.register(shutdown_autostart)


def ensure_autostart(tiles: list[Tile] | None = None) -> None:
    """Launch any enabled+autostart tile whose service is down.

    Called from the Control API's /status route (the live dashboard polls it
    every 5s); internally rate-limited so each tile gets at most one launch
    attempt per AUTOSTART_RETRY_SEC. Never raises.
    """
    tiles = tiles if tiles is not None else load_tiles()
    _debug_log(f"ensure_autostart: {len(tiles)} tile(s) {[t.id for t in tiles]}")
    now = time.monotonic()
    with _registry_lock:
        _reap_finished()  # don't accumulate dead children between polls
        for t in tiles:
            if not (t.autostart and t.command):
                continue
            if now - _last_attempt.get(t.id, 0.0) < AUTOSTART_RETRY_SEC:
                continue
            _last_attempt[t.id] = now
            prev = _launched.get(t.id)
            if prev is not None and prev.poll() is None:
                continue  # our own child is still running
            if probe_tile(t)["up"]:
                continue  # something else already serves it
            cmd = [_resolve_exe(t.command[0]), *t.command[1:]]
            cwd = _resolve_cwd(t)
            try:
                kwargs: dict = {"cwd": cwd}
                if os.name == "nt":
                    kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                _launched[t.id] = subprocess.Popen(  # nosec B603 — config-owned argv
                    cmd, **kwargs
                )
                _debug_log(f"launched {t.id}: cmd={cmd} cwd={cwd!r}")
            except Exception as exc:
                _debug_log(f"launch FAILED {t.id}: cmd={cmd} cwd={cwd!r} {exc!r}")


def tile_anchor(tile: Tile, status: dict | None = None) -> str:
    """Render one <a class="tile"> matching dashboard.py's existing CSS
    (.tile / .ico / .hq), so no stylesheet changes are needed.

    Config-sourced strings are HTML-escaped: the JSON is local, but a
    hand-edited config must not be able to inject markup into the dashboard.
    """
    up = bool(status and status.get("up"))
    classes = "tile"
    if tile.extra_class:
        classes += f" {html.escape(tile.extra_class, quote=True)}"
    name = html.escape(tile.name, quote=True)
    url = html.escape(tile.url, quote=True)
    icon = html.escape(tile.icon, quote=True)
    title = html.escape(f"{tile.name} — {'running' if up else 'not responding'}", quote=True)
    dot_color = "#34d399" if up else "#9aa1b5"
    return (
        f'<a class="{classes}" href="{url}" title="{title}">'
        f'<span class="ico">{icon}</span>'
        f'<span style="display:flex;align-items:center;gap:5px;">'
        f'{name}<span style="width:7px;height:7px;border-radius:50%;'
        f'background:{dot_color};"></span></span></a>'
    )


def render_all_tiles() -> str:
    """Convenience: probe + render every enabled tile as one HTML string."""
    out = []
    for t in load_tiles():
        out.append(tile_anchor(t, probe_tile(t)))
    return "\n".join(out)


if __name__ == "__main__":
    # Self-test:  python -m browser_core.tile_registry
    import sys

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ts = load_tiles()
    print(f"registry OK — {len(ts)} enabled tile(s)")
    for st in probe_all().values():
        state = "UP" if st["up"] else "down"
        extra = f" ({st['tools']} tools)" if "tools" in st else ""
        print(f"  {st['id']:<12} {state}{extra}")
