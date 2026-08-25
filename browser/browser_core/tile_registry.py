"""TileRegistry — config-driven platform tiles for the LuckyD dashboard.

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

A tile with enabled=false is hidden but remembered — flipping it on later
is a JSON edit, not a code change.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
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


def _child_env() -> dict:
    """OS environment merged with the repo .env, for tiles spawned as child
    processes (e.g. Deck Studio's node server, which needs GOOGLE_API_KEY for
    AI image generation). Without this, subprocess.Popen only inherits the
    real Windows environment -- values that live solely in the repo .env
    (like GOOGLE_API_KEY) never reach the child, even though studio/.env's
    comment claims they're injected. .env values fill gaps; real OS-set vars
    still win so an explicit system env var is never shadowed."""
    merged = dict(os.environ)
    try:
        from .ai_bridge import _load_env

        for key, value in _load_env().items():
            merged.setdefault(key, value)
    except Exception:
        pass
    return merged


def _expand(p: str) -> str:
    out = os.path.expandvars(os.path.expanduser(p or ""))
    # %APPDIR% = folder containing the running exe (frozen) or the browser
    # package root (dev) — lets tiles reference files bundled with the app.
    if "%APPDIR%" in out or "{app}" in out.lower():
        if getattr(sys, "frozen", False):
            appdir = str(Path(sys.executable).resolve().parent)
        else:
            appdir = str(Path(__file__).resolve().parent.parent)
        out = out.replace("%APPDIR%", appdir)
        out = out.replace("{app}", appdir)
        # Frozen onedir builds keep bundled data (studio/, assets/, ...) in
        # the _internal folder (sys._MEIPASS), NOT beside the exe — so an
        # exe-relative path like <install>\studio doesn't exist and Popen
        # dies with NotADirectoryError(267). Fall back to the _MEIPASS copy.
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass and not os.path.exists(out) and out.startswith(appdir):
            alt = str(Path(meipass) / os.path.relpath(out, appdir))
            if os.path.exists(alt):
                out = alt
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
                command=tuple(str(c) for c in e.get("command", []) or []),
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
        with urllib.request.urlopen(tile.health_url, timeout=timeout) as resp:
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


def ensure_autostart(tiles: list[Tile] | None = None) -> None:
    """Launch any enabled+autostart tile whose service is down.

    Called from the Control API's /status route (the live dashboard polls it
    every 5s); internally rate-limited so each tile gets at most one launch
    attempt per AUTOSTART_RETRY_SEC. Never raises.
    """
    tiles = tiles if tiles is not None else load_tiles()
    _debug_log(f"ensure_autostart: {len(tiles)} tile(s) {[t.id for t in tiles]}")
    now = time.monotonic()
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
        try:
            kwargs: dict = {"cwd": t.cwd or None, "env": _child_env()}
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            _launched[t.id] = subprocess.Popen(  # nosec B603 — config-owned argv
                list(t.command), **kwargs
            )
            _debug_log(f"launched {t.id}: cmd={list(t.command)} cwd={t.cwd!r}")
        except Exception as exc:
            _debug_log(f"launch FAILED {t.id}: {exc!r}")


def tile_anchor(tile: Tile, status: dict | None = None) -> str:
    """Render one <a class="tile"> matching dashboard.py's existing CSS
    (.tile / .ico / .hq), so no stylesheet changes are needed."""
    up = bool(status and status.get("up"))
    classes = "tile"
    if tile.extra_class:
        classes += f" {tile.extra_class}"
    title = f"{tile.name} — {'running' if up else 'not responding'}"
    dot_color = "#34d399" if up else "#9aa1b5"
    return (
        f'<a class="{classes}" href="{tile.url}" title="{title}">'
        f'<span class="ico">{tile.icon}</span>'
        f'<span style="display:flex;align-items:center;gap:5px;">'
        f'{tile.name}<span style="width:7px;height:7px;border-radius:50%;'
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
