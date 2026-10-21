"""Native title-bar theming for the Windows shell.

Qt styles the client area but not the Windows caption of a normal top-level
window. Without this the browser chrome is a dark, polished surface topped by
a stark white system title bar.  Windows 10/11 expose DWM attributes for the
caption colour, text colour, border colour and immersive dark mode; applying
them keeps the header visually part of the active LuckyD theme.

All calls are best-effort: unsupported attributes (Windows 10 predates the
colour attributes) simply fail and are ignored, and non-Windows platforms
are no-ops.
"""

from __future__ import annotations

import contextlib
import ctypes
import sys
from ctypes import wintypes

from .theme import palette

# Documented in dwmapi.h (Windows 11) / reverse-engineered names (Windows 10).
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19
_DWMWA_BORDER_COLOR = 34
_DWMWA_CAPTION_COLOR = 35
_DWMWA_TEXT_COLOR = 36

#: Sentinel meaning "let DWM decide" (DWMWA_COLOR_DEFAULT).
_COLOR_DEFAULT = 0xFFFFFFFF


def _is_dark(hex_color: str) -> bool:
    """True when a #rrggbb colour is perceptually dark."""
    value = (hex_color or "").lstrip("#")
    if len(value) != 6:
        return True
    try:
        r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except ValueError:
        return True
    # Rec. 601 luma — good enough to pick the caption text scheme.
    return (0.299 * r + 0.587 * g + 0.114 * b) < 128


def _colorref(hex_color: str) -> int:
    """#rrggbb -> Windows COLORREF (0x00BBGGRR)."""
    value = (hex_color or "").lstrip("#")
    try:
        r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except (ValueError, IndexError):
        return _COLOR_DEFAULT
    return r | (g << 8) | (b << 16)


def _dwm_set(hwnd: int, attribute: int, value: int) -> bool:
    """Set one DWM window attribute; False when unsupported/unavailable."""
    try:
        dwmapi = ctypes.windll.dwmapi
        dwmapi.DwmSetWindowAttribute.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
        data = ctypes.c_int(value)
        result = dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd),
            attribute,
            ctypes.byref(data),
            ctypes.sizeof(data),
        )
        return result == 0
    except Exception:
        return False


def apply_titlebar_theme(window, settings) -> None:
    """Paint ``window``'s native caption in the active theme's colours.

    Also tells Qt the app-level colour scheme so native dialogs, menus and
    scrollbars use the same light/dark baseline as the custom QSS.
    """
    if sys.platform != "win32":
        return

    colors = palette(settings)
    caption = colors["window"]
    dark = _is_dark(caption)
    text = colors["text"]
    border = colors["border"]

    with contextlib.suppress(Exception):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication

        qapp = QGuiApplication.instance()
        hints = qapp.styleHints() if qapp is not None else None
        if hints is not None and hasattr(hints, "setColorScheme"):
            hints.setColorScheme(Qt.ColorScheme.Dark if dark else Qt.ColorScheme.Light)

    try:
        hwnd = int(window.winId())
    except Exception:
        return
    if not hwnd:
        return

    if not _dwm_set(hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if dark else 0):
        _dwm_set(hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE_OLD, 1 if dark else 0)

    # Colour attributes are Windows 11 (build 22000+) only; failures are fine.
    _dwm_set(hwnd, _DWMWA_CAPTION_COLOR, _colorref(caption))
    _dwm_set(hwnd, _DWMWA_TEXT_COLOR, _colorref(text))
    _dwm_set(hwnd, _DWMWA_BORDER_COLOR, _colorref(border))


def apply_to_window(window, settings) -> None:
    """Convenience alias used by the main window / dialogs."""
    apply_titlebar_theme(window, settings)
