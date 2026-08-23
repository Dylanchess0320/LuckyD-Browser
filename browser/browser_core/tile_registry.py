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
import time
from dataclasses import dataclass, field
from pathlib import Path
import urllib.request

CONFIG_PATH = Path(__file__).with_name("platform_tiles.json")
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


def _expand(p: str) -> str:
    return os.path.expandvars(os.path.expanduser(p or ""))


def load_tiles(config_path: Path | None = None) -> list[Tile]:
    """Load enabled tiles from the registry config. Never raises: a missing
    or malformed config degrades to an empty tile list so the dashboard
    still renders."""
    path = config_path or CONFIG_PATH
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        entries = raw.get("tiles", [])
    except Exception:
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
    now = time.monotonic()
    for t in (tiles if tiles is not None else load_tiles()):
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
            kwargs: dict = {"cwd": t.cwd or None}
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            _launched[t.id] = subprocess.Popen(  # nosec B603 — config-owned argv
                list(t.command), **kwargs
            )
        except Exception:
            pass


def tile_anchor(tile: Tile, status: dict | None = None) -> str:
    """Render one <a class="tile"> matching dashboard.py's existing CSS
    (.tile / .ico / .hq), so no stylesheet changes are needed."""
    up = bool(status and status.get("up"))
    classes = "tile"
    if tile.extra_class:
        classes += f" {tile.extra_class}"
    title = f'{tile.name} — {"running" if up else "not responding"}'
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
