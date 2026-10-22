"""Retired tools (2026-10-22): LuckyD HQ, Workflows, Deep Researcher and
Deck Studio are hidden from every UI surface behind the single
``show_retired_tools`` setting (default off) — but stay fully built in.

These tests pin the hiding behaviour and the reversibility of the switch.
The underlying modules/routes are covered by their own suites and are
deliberately untouched.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from browser_core.dashboard import dashboard_html
from browser_core.settings import DEFAULTS
from browser_core.tile_registry import CONFIG_PATH


def _fake_settings(**overrides):
    vals = {"theme": "neon", **overrides}
    return SimpleNamespace(get=lambda k, d=None: vals.get(k, d))


def test_switch_defaults_to_hidden() -> None:
    assert DEFAULTS["show_retired_tools"] is False


def test_dashboard_hides_retired_apps_and_tiles_by_default() -> None:
    html = dashboard_html()
    assert 'const RETIRED_APPS = ["Agent HQ", "Deep Research", "Workflows"];' in html
    # Deck Studio only ever reaches the dashboard via the tile registry.
    assert "Deck Studio" not in html
    assert "__RETIRED_APPS__" not in html
    assert "__PLATFORM_TILES__" not in html


def test_dashboard_restores_retired_surfaces_when_switch_on() -> None:
    html = dashboard_html(_fake_settings(show_retired_tools=True))
    assert "const RETIRED_APPS = [];" in html
    assert "Deck Studio" in html


def test_shipped_deck_studio_tile_stays_configured() -> None:
    """The tile config itself is untouched (built in) — the switch hides it
    at the dashboard/autostart consumers, not by deleting registry state."""
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    entry = next(t for t in raw["tiles"] if t["id"] == "deck-studio")
    assert entry["health_url"].endswith("/health")
    assert (entry["command"] or [])[:1] == ["node"]


def test_ensure_autostart_skips_retired_tiles_unless_switch_on(monkeypatch) -> None:
    from browser_core import tile_registry

    tile = tile_registry.Tile(
        id="deck-studio",
        name="Deck Studio",
        icon="",
        url="lucky://down",
        autostart=True,
        command=(sys.executable, "-c", "pass"),
    )
    monkeypatch.setattr(tile_registry, "load_tiles", lambda *a, **k: [tile])
    tile_registry._last_attempt.pop("deck-studio", None)
    tile_registry._launched.pop("deck-studio", None)

    monkeypatch.setattr(tile_registry, "_show_retired_tools", lambda: False)
    tile_registry.ensure_autostart()
    assert "deck-studio" not in tile_registry._launched

    monkeypatch.setattr(tile_registry, "_show_retired_tools", lambda: True)
    tile_registry.ensure_autostart()
    try:
        assert "deck-studio" in tile_registry._launched
    finally:
        tile_registry.shutdown_autostart()
        tile_registry._launched.pop("deck-studio", None)
        tile_registry._last_attempt.pop("deck-studio", None)


def test_retired_modules_still_present_and_importable() -> None:
    from browser_core import workflows  # noqa: F401
    from browser_core.dashboard import hq_shell_html, hq_splash_html  # noqa: F401
    from browser_core.research_page import research_html  # noqa: F401

    assert (_BROWSER_DIR / "studio" / "studio-server.js").is_file()


def test_ui_surfaces_gate_on_the_switch() -> None:
    main_window = (_BROWSER_DIR / "browser_ui" / "main_window.py").read_text(encoding="utf-8")
    palette = (_BROWSER_DIR / "browser_ui" / "palette.py").read_text(encoding="utf-8")
    sidebar = (_BROWSER_DIR / "browser_ui" / "ai_sidebar.py").read_text(encoding="utf-8")
    dialogs = (_BROWSER_DIR / "browser_ui" / "dialogs.py").read_text(encoding="utf-8")
    assert "_show_retired_tools" in main_window
    assert 'self._add(tools_menu, "Coding Agent", self.open_hq, "Ctrl+Shift+H")' in main_window
    assert '"show_retired_tools"' in palette
    # Pass 2 removed the sidebar Research button unconditionally (Dylan,
    # 2026-10-22) — it no longer needs the switch, it is simply gone.
    assert "🔬 Research" not in sidebar
    assert "_open_research_from_input" not in sidebar
    assert '"show_retired_tools", self.retired_tools.isChecked()' in dialogs
    # The open_* handlers themselves are NOT removed — hiding is UI-only.
    assert "def open_hq" in main_window
    assert "def open_workflows" in main_window
    assert "def open_deep_research" in main_window
