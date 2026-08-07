"""Futuristic design system: palettes, accent pairs, and the global QSS.

Four themes ship with the browser:
  neon    — Neon Night (default): deep space blue, electric blue + violet
  cyber   — Cyber Grove: carbon black-green, lime + teal glow
  solar   — Solar Dusk: warm obsidian, amber + rose glow
  arctic  — Arctic Light: crisp light theme, sapphire + violet

The whole app is styled from one generated Qt stylesheet so every window,
dialog, dock, menu and popup stays consistent. Theme + accent persist in
settings.json and apply live (no restart) via apply_to_app().
"""

from __future__ import annotations

THEMES: dict[str, dict[str, str]] = {
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
        "tab_inactive": "#121826",
        "scroll": "#2a3450",
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
        "tab_inactive": "#101812",
        "scroll": "#24382b",
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
        "tab_inactive": "#1c130e",
        "scroll": "#43301f",
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
        "tab_inactive": "#dde5f2",
        "scroll": "#b3c0d8",
    },
    # The secret fifth — unlock it with the Konami code on the new-tab page
    # (↑↑↓↓←→←→BA) or just pick it in Settings like a normal person.
    "synthwave": {
        "label": "Synthwave Sunset",
        "window": "#12081f",
        "panel": "#180b29",
        "panel2": "#1f1033",
        "card": "#251640",
        "border": "#3a1f5c",
        "text": "#f3e9ff",
        "muted": "#9d8bb8",
        "accent": "#ff71ce",
        "accent2": "#01cdfe",
        "danger": "#ff5b6e",
        "ok": "#05ffa1",
        "tab_inactive": "#160a26",
        "scroll": "#40215f",
    },
}

DEFAULT_THEME = "neon"


from string import Template

_QSS_A = """
/* ── base ─────────────────────────────────────────────────────────── */
QWidget {
    background-color: $window;
    color: $text;
    font-family: "Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif;
    font-size: 13px;
    font-weight: 600;
}
QMainWindow::separator { background: $border; width: 3px; height: 3px; }
QMainWindow::separator:hover { background: $accent; }

/* ── menus ────────────────────────────────────────────────────────── */
QMenuBar { background: $window; border: none; padding: 2px 4px; }
QMenuBar::item { background: transparent; padding: 6px 11px; border-radius: 8px; }
QMenuBar::item:selected { background: $card; color: $accent; }
QMenuBar::item:pressed { background: $panel2; }
QMenu {
    background: $panel; border: 1px solid $border; border-radius: 12px;
    padding: 6px;
}
QMenu::item { padding: 7px 26px 7px 16px; border-radius: 8px; }
QMenu::item:selected {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 $accent, stop:1 $accent2);
    color: #ffffff;
}
QMenu::item:disabled { color: $muted; }
QMenu::separator { height: 1px; background: $border; margin: 5px 10px; }
QMenu::icon { padding-left: 6px; }

/* ── toolbar ──────────────────────────────────────────────────────── */
QToolBar {
    background: $window; border: none;
    border-bottom: 1px solid $border;
    padding: 6px 8px; spacing: 4px;
}
QToolButton {
    background: transparent; border: none; border-radius: 9px;
    padding: 6px 9px; color: $text; font-size: 15px;
}
QToolButton:hover { background: $card; color: $accent; }
QToolButton:pressed { background: $panel2; }
QToolButton:disabled { color: $muted; }
QToolButton#ai_button {
    color: #ffffff; font-weight: 700; padding: 6px 14px;
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 $accent, stop:1 $accent2);
}
QToolButton#ai_button:hover { color: #ffffff; }

/* ── tabs ─────────────────────────────────────────────────────────── */
QTabWidget::pane { border: none; background: $window; }
QTabWidget::tab-bar { alignment: left; }
QTabBar { background: $window; border: none; border-bottom: 1px solid $border; }
QTabBar::tab {
    background: $tab_inactive; color: $muted;
    border: 1px solid transparent; border-bottom: none;
    border-top-left-radius: 12px; border-top-right-radius: 12px;
    padding: 8px 14px; margin: 6px 2px 0 2px; min-width: 110px; max-width: 210px;
}
QTabBar::tab:hover { background: $card; color: $text; }
QTabBar::tab:selected {
    background: $panel2; color: $text;
    border-color: $border; border-top: 2px solid $accent;
}
QTabBar::close-button { image: none; border-radius: 8px; padding: 2px; }
QTabBar::close-button:hover { background: $danger; color: #ffffff; }
QToolButton#tab_plus {
    font-size: 17px; font-weight: 700; color: $accent;
    border-radius: 10px; margin: 4px;
}

/* ── omnibox / inputs ─────────────────────────────────────────────── */
QLineEdit {
    background: $panel2; border: 1px solid $border; border-radius: 14px;
    padding: 8px 14px; selection-background-color: $accent;
}
QLineEdit:focus { border: 1px solid $accent; background: $card; }
QLineEdit#omnibox { border-radius: 16px; font-size: 14px; padding: 9px 16px; }
QComboBox {
    background: $panel2; border: 1px solid $border; border-radius: 10px;
    padding: 6px 10px;
}
QComboBox:hover { border-color: $accent; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background: $panel; border: 1px solid $border; border-radius: 10px;
    padding: 4px; outline: none;
}
QComboBox QAbstractItemView::item { padding: 6px 10px; border-radius: 7px; }
QComboBox QAbstractItemView::item:selected { background: $accent; color: #fff; }
"""


def palette(settings) -> dict[str, str]:
    """The active theme's color map (falls back to Neon Night)."""
    name = str(settings.get("theme", DEFAULT_THEME)) if settings else DEFAULT_THEME
    return THEMES.get(name, THEMES[DEFAULT_THEME])


def theme_name(settings) -> str:
    name = str(settings.get("theme", DEFAULT_THEME)) if settings else DEFAULT_THEME
    return name if name in THEMES else DEFAULT_THEME


_QSS_B = """
/* ── buttons ──────────────────────────────────────────────────────── */
/* ── toolbar ─────────────────────────────────────────────────────── */
QToolBar {
    background: $window;
    border: none;
    border-bottom: 1px solid $border;
    spacing: 4px;
    padding: 4px 6px;
}
QToolBar QToolButton {
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 6px 8px;
    margin: 1px;
    font-size: 15px;
}
QToolBar QToolButton:hover {
    background: $card;
}
QToolBar QToolButton:pressed {
    background: $panel2;
}
QToolBar QToolButton:checked {
    background: $card;
    border: 1px solid $accent;
}
QToolBar QToolButton#tab_plus {
    font-size: 16px;
    font-weight: bold;
    color: $muted;
    min-width: 28px;
    min-height: 24px;
}
QToolBar QToolButton#tab_plus:hover {
    color: $accent;
}

/* ── bookmark bar: quiet strip, pill hover, per-site tiles ─────────── */
QToolBar#bookmark_bar {
    background: $panel;
    border: none;
    border-bottom: 1px solid $border;
    padding: 1px 8px;
    spacing: 2px;
}
QToolBar#bookmark_bar QToolButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 3px 9px;
    color: $text;
    font-size: 12px;
    font-weight: 600;
}
QToolBar#bookmark_bar QToolButton:hover {
    background: $card;
    border-color: $border;
    color: $accent;
}
QToolBar#bookmark_bar QToolButton:pressed { background: $panel2; }
QToolBar#bookmark_bar QToolButton:disabled { color: $muted; font-style: italic; }

/* ── zoom pill (status bar) ────────────────────────────────────────── */
QPushButton#zoom_pill {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 9px;
    padding: 2px 10px;
    color: $muted;
    font-size: 11.5px;
    font-weight: 600;
}
QPushButton#zoom_pill:hover { background: $card; border-color: $border; color: $accent; }
QPushButton#zoom_pill[zoomed="true"] { color: $accent2; border-color: $border; }

/* ── tab bar ──────────────────────────────────────────────────────── */
QTabWidget::pane {
    background: $window;
    border: none;
    position: absolute;
}
QTabBar {
    background: $window;
    border: none;
}
QTabBar::tab {
    background: $tab_inactive;
    color: $muted;
    border: none;
    border-radius: 8px;
    padding: 5px 16px 5px 12px;
    margin: 3px 1px 3px 1px;
    min-height: 26px;
}
QTabBar::tab:selected {
    background: $panel;
    color: $text;
    border: 1px solid $border;
    border-bottom: 1px solid $panel;
    margin-bottom: -1px;
}
QTabBar::tab:hover:!selected {
    background: $panel2;
}
QTabBar::tab:!selected {
    margin-top: 4px;
}
QTabBar::close-button {
    image: none;
    width: 16px;
    height: 16px;
}
QTabBar QToolButton {
    background: transparent;
    border: none;
    border-radius: 4px;
    padding: 2px;
}
QTabBar QToolButton:hover {
    background: $card;
}

QPushButton {
    background: $card; border: 1px solid $border; border-radius: 10px;
    padding: 7px 14px;
}
QPushButton:hover { border-color: $accent; color: $accent; }
QPushButton:pressed { background: $panel2; }
QPushButton:default, QPushButton#accent {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 $accent, stop:1 $accent2);
    color: #ffffff; border: none; font-weight: 600;
}
QPushButton:default:hover, QPushButton#accent:hover { color: #ffffff; }
QCheckBox { spacing: 8px; background: transparent; }
QCheckBox::indicator {
    width: 16px; height: 16px; border-radius: 5px;
    border: 1px solid $border; background: $panel2;
}
QCheckBox::indicator:checked { background: $accent; border-color: $accent; }

/* ── lists / text surfaces ────────────────────────────────────────── */
QListWidget, QTextBrowser, QTextEdit, QPlainTextEdit {
    background: $panel; border: 1px solid $border; border-radius: 12px;
    padding: 6px; outline: none;
}
QListWidget::item { padding: 6px 8px; border-radius: 8px; }
QListWidget::item:selected { background: $panel2; color: $accent; }
QListWidget::item:hover { background: $card; }
QDialog { background: $window; }
QDialogButtonBox QPushButton { min-width: 84px; }
QLabel { background: transparent; }
QLabel#muted { color: $muted; }
QLabel#accent { color: $accent; font-weight: 600; }

/* ── docks ────────────────────────────────────────────────────────── */
QDockWidget { color: $muted; font-weight: 600; }
QDockWidget::title {
    background: $window; border-bottom: 1px solid $border; padding: 8px 12px;
}

/* ── progress / status ────────────────────────────────────────────── */
QProgressBar {
    background: $panel2; border: 1px solid $border; border-radius: 7px;
    max-height: 12px; text-align: center;
}
QProgressBar::chunk {
    border-radius: 6px;
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 $accent, stop:1 $accent2);
}
QStatusBar {
    background: $window; border-top: 1px solid $border; color: $muted;
}
QStatusBar::item { border: none; }

/* ── scrollbars: slim, glow on hover ──────────────────────────────── */
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical {
    background: $scroll; border-radius: 5px; min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: $accent; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    height: 0; background: none; border: none;
}
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal {
    background: $scroll; border-radius: 5px; min-width: 30px;
}
QScrollBar::handle:horizontal:hover { background: $accent; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    width: 0; background: none; border: none;
}

/* ── tooltips / find bar ──────────────────────────────────────────── */
QToolTip {
    background: $panel; color: $text;
    border: 1px solid $border; border-radius: 8px; padding: 6px 9px;
}
QToolBar#find_bar { border: none; border-top: 1px solid $border; }
"""


def stylesheet(p: dict[str, str]) -> str:
    """The full application QSS for one palette."""
    return Template(_QSS_A + _QSS_B).substitute(p)


def apply_to_app(qapp, settings) -> None:
    """(Re)apply the active theme to every widget in the process — live."""
    qapp.setStyleSheet(stylesheet(palette(settings)))
