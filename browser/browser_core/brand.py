"""Single source of truth for the LuckyD product's visual identity.

Every surface — Qt chrome (via browser_ui.theme), the live dashboard, the
HQ splash, the fallback new-tab page, and the AI sidebar — pulls its colors
from the active theme's palette so the whole browser reads as one product
instead of a pile of parts.

The HTML surfaces can't import Qt, so they read a small JSON of CSS custom
properties that the Control API injects. Qt surfaces import this module
directly. Both resolve to the same theme palettes defined in
browser_ui.theme.THEMES (kept here as plain dicts so there is no Qt import
in the core layer).
"""

from __future__ import annotations

import json
from pathlib import Path


# Persisted settings — same store SettingsStore writes (browser/data/
# settings.json; beside the exe when frozen). Resolved lazily so a theme
# change picked up by the next served page needs no restart.
def _settings_path() -> Path:
    import sys

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "data" / "settings.json"
    return Path(__file__).resolve().parent.parent / "data" / "settings.json"


def _load_stored() -> dict:
    try:
        path = _settings_path()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


# Mirror of browser_ui.theme.THEMES, kept Qt-free so browser_core can use it.
# These are the canonical design tokens; theme.py consumes the same values.
PALETTES: dict[str, dict[str, str]] = {
    "neon": {
        "label": "Neon Night",
        "window": "#0b0f1a",
        "panel": "#10151f",
        "panel2": "#141a28",
        "card": "#1a2132",
        "border": "#232c42",
        "text": "#e8ecf5",
        "muted": "#8b93a7",
        "accent": "#5b9dff",
        "accent2": "#b46bff",
        "danger": "#ff5b6e",
        "ok": "#34d399",
    },
    "cyber": {
        "label": "Cyber Grove",
        "window": "#090e0b",
        "panel": "#0d1410",
        "panel2": "#111a14",
        "card": "#16211a",
        "border": "#1f2f24",
        "text": "#e6f2e9",
        "muted": "#7f957f",
        "accent": "#4ade80",
        "accent2": "#22d3ee",
        "danger": "#fb7185",
        "ok": "#4ade80",
    },
    "solar": {
        "label": "Solar Dusk",
        "window": "#130d0b",
        "panel": "#1a120e",
        "panel2": "#201610",
        "card": "#281c14",
        "border": "#3a281c",
        "text": "#f5ebe2",
        "muted": "#a3907f",
        "accent": "#fb923c",
        "accent2": "#f43f5e",
        "danger": "#ef4444",
        "ok": "#fbbf24",
    },
    "arctic": {
        "label": "Arctic Light",
        "window": "#eef2f9",
        "panel": "#e4eaf4",
        "panel2": "#dbe3f0",
        "card": "#ffffff",
        "border": "#c6d0e2",
        "text": "#1a2233",
        "muted": "#5d6b85",
        "accent": "#2563eb",
        "accent2": "#7c3aed",
        "danger": "#dc2626",
        "ok": "#059669",
    },
}

DEFAULT_THEME = "neon"


def active_name(settings=None) -> str:
    """The configured theme key, validated against the known palettes.

    Settings-less HTML surfaces (the proxy pages the Control API serves for
    HQ) fall back to the persisted settings store, so they pick up the same
    theme the Qt chrome is using even when no SettingsStore was passed in.
    """
    if settings is None:
        settings = _load_stored()
    name = str(settings.get("theme", DEFAULT_THEME)) if settings else DEFAULT_THEME
    return name if name in PALETTES else DEFAULT_THEME


def tokens(settings=None) -> dict[str, str]:
    """The active theme's color tokens (falls back to Neon Night)."""
    return PALETTES[active_name(settings)]


def css_vars(settings=None) -> str:
    """The active theme as a `:root{ --ld-*: … }` CSS block for HTML surfaces."""
    t = tokens(settings)
    # A subtle two-stop gradient per theme, derived from window + a hint of accent.
    grad_a = t["window"]
    grad_b = t["panel2"]
    lines = [
        f"--ld-window:{t['window']}",
        f"--ld-panel:{t['panel']}",
        f"--ld-panel2:{t['panel2']}",
        f"--ld-card:{t['card']}",
        f"--ld-border:{t['border']}",
        f"--ld-text:{t['text']}",
        f"--ld-muted:{t['muted']}",
        f"--ld-accent:{t['accent']}",
        f"--ld-accent2:{t['accent2']}",
        f"--ld-ok:{t['ok']}",
        f"--ld-danger:{t['danger']}",
        f"--ld-grad:linear-gradient(135deg,{grad_a} 0%,{grad_b} 60%,{t['panel']} 100%)",
    ]
    return ":root{" + ";".join(lines) + "}"
