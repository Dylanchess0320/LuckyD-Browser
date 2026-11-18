"""Fullscreen state/geometry regression tests.

The old implementation tracked a single ``_fs_was_maximized`` boolean and
called ``showNormal()`` for an unmatched element-fullscreen toggle-off. That
un-maximized the window and re-showed chrome the user had hidden (the AI
sidebar), which produced the "browser forced/stretched" glitch after a site
exited fullscreen.

These tests exercise the real restore helpers against a fake window object,
so they run quickly and do not need a QWebEngine renderer.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

_BROWSER_DIR = Path(__file__).resolve().parent.parent / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


def _real_qt_available() -> bool:
    try:
        from PySide6.QtWidgets import QWidget
    except Exception:
        return False
    return isinstance(QWidget, type) and QWidget.__module__.startswith("PySide6")


pytestmark = pytest.mark.skipif(
    not _real_qt_available(), reason="needs a real PySide6 (collection stubs skip)"
)

# Import at collection time so PySide6.QtWebEngineCore is real and cached
# before sibling test modules install their MagicMock stubs.
if _real_qt_available():
    from browser_ui.main_window import MainWindow as _MainWindow
else:  # pragma: no cover - stub suites skip this module
    _MainWindow = None


def _qt():
    from PySide6.QtCore import QRect, Qt

    return QRect, Qt


class _FakeWindow:
    def __init__(self, state, geometry=None, normal=None):
        QRect, _Qt = _qt()
        self._state = state
        self._geometry = geometry or QRect(120, 80, 1100, 760)
        self._normal = normal or self._geometry
        self.calls: list = []

    def windowState(self):
        return self._state

    def geometry(self):
        return self._geometry

    def normalGeometry(self):
        return self._normal

    def showNormal(self):
        self.calls.append("normal")

    def showMaximized(self):
        self.calls.append("maximized")

    def showFullScreen(self):
        self.calls.append("fullscreen")

    def setGeometry(self, geometry):
        self.calls.append(("geometry", geometry))


class _FakeMainWindow(_FakeWindow):
    """Just enough of MainWindow for the fullscreen methods to run."""

    def __init__(self, state, video_fs=True):
        super().__init__(state)
        self._video_fs = video_fs
        self._fs_view = MagicMock(name="owner-view")
        self._fs_hidden = [MagicMock(name="nav"), MagicMock(name="sidebar")]
        self._fs_prev_state = None
        self._window_fs_prev = None
        # Give the fake the real helper methods, bound to this instance, so
        # the production code's internal self._help() calls resolve.
        cls = _main_window_class()
        for name in (
            "_restore_window_state",
            "_enter_video_fullscreen",
            "_exit_video_fullscreen",
            "_enter_window_fullscreen",
            "_exit_window_fullscreen",
            "on_view_closing",
        ):
            setattr(self, name, getattr(cls, name).__get__(self, type(self)))
        # staticmethod: keep it unbound so the production call
        # self._window_state_snapshot(self) still passes one window.
        self._window_state_snapshot = cls._window_state_snapshot


def _main_window_class():
    return _MainWindow


def test_snapshot_captures_state_and_both_geometries() -> None:
    QRect, Qt = _qt()
    win = _FakeWindow(
        Qt.WindowState.WindowMaximized,
        geometry=QRect(5, 6, 700, 500),
        normal=QRect(10, 20, 900, 600),
    )
    state, geometry, normal = _main_window_class()._window_state_snapshot(win)
    assert state == Qt.WindowState.WindowMaximized
    assert geometry == QRect(5, 6, 700, 500)
    assert normal == QRect(10, 20, 900, 600)


def test_restore_returns_to_maximized_not_normal() -> None:
    QRect, Qt = _qt()
    win = _FakeWindow(Qt.WindowState.WindowNoState)
    snapshot = (
        Qt.WindowState.WindowMaximized,
        QRect(1, 2, 800, 600),
        QRect(30, 40, 1000, 700),
    )
    _main_window_class()._restore_window_state(win, snapshot)
    assert "maximized" in win.calls
    # showNormal is an intermediate step to apply the restore rectangle;
    # the final state must be maximized.
    assert win.calls[-1] == "maximized"
    # The restore target is applied before maximizing again.
    assert ("geometry", QRect(30, 40, 1000, 700)) in win.calls


def test_restore_returns_to_exact_normal_geometry() -> None:
    QRect, Qt = _qt()
    win = _FakeWindow(Qt.WindowState.WindowFullScreen)
    saved = QRect(64, 32, 1234, 777)
    _main_window_class()._restore_window_state(
        win, (Qt.WindowState.WindowNoState, saved, saved)
    )
    assert win.calls[0] == "normal"
    assert ("geometry", saved) in win.calls


def test_restore_keeps_an_f11_fullscreen_window_fullscreen() -> None:
    QRect, Qt = _qt()
    win = _FakeWindow(Qt.WindowState.WindowNoState)
    snapshot = (
        Qt.WindowState.WindowFullScreen,
        QRect(0, 0, 1280, 800),
        QRect(0, 0, 1280, 800),
    )
    _main_window_class()._restore_window_state(win, snapshot)
    assert win.calls == ["fullscreen"]


def test_unmatched_video_toggle_off_is_a_noop() -> None:
    QRect, Qt = _qt()
    win = _FakeMainWindow(Qt.WindowState.WindowNoState, video_fs=False)
    win._fs_hidden = []
    _main_window_class()._exit_video_fullscreen(win)
    # The old code called showNormal() here and wrecked the window geometry.
    assert win.calls == []
    assert win._fs_hidden == []


def test_toggle_off_from_a_different_view_cannot_end_the_owner_session() -> None:
    QRect, Qt = _qt()
    win = _FakeMainWindow(Qt.WindowState.WindowMaximized)
    win._fs_prev_state = (
        Qt.WindowState.WindowMaximized,
        win.geometry(),
        win.normalGeometry(),
    )
    hidden = list(win._fs_hidden)
    other_view = object()

    _main_window_class()._exit_video_fullscreen(win, view=other_view)

    assert win._video_fs is True
    assert win._fs_hidden == hidden
    assert win.calls == []


def test_exit_restores_window_and_only_previously_visible_chrome() -> None:
    QRect, Qt = _qt()
    win = _FakeMainWindow(Qt.WindowState.WindowMaximized)
    win._fs_prev_state = (
        Qt.WindowState.WindowMaximized,
        win.geometry(),
        win.normalGeometry(),
    )
    shown = list(win._fs_hidden)
    owner = win._fs_view

    _main_window_class()._exit_video_fullscreen(win, view=owner)

    assert win._video_fs is False
    assert win._fs_view is None
    assert "maximized" in win.calls
    for widget in shown:
        widget.show.assert_called_once()
    # The sidebar being closed is respected: it is not in _fs_hidden unless
    # it was visible when fullscreen started.
    assert win._fs_hidden == []


def test_closing_the_owner_tab_exits_fullscreen() -> None:
    QRect, Qt = _qt()
    win = _FakeMainWindow(Qt.WindowState.WindowNoState)
    win._fs_prev_state = (
        Qt.WindowState.WindowNoState,
        win.geometry(),
        win.normalGeometry(),
    )
    owner = win._fs_view
    _main_window_class().on_view_closing(win, owner)
    assert win._video_fs is False
    assert "normal" in win.calls


def test_closing_another_tab_does_not_exit_fullscreen() -> None:
    QRect, Qt = _qt()
    win = _FakeMainWindow(Qt.WindowState.WindowNoState)
    _main_window_class().on_view_closing(win, object())
    assert win._video_fs is True
    assert win.calls == []


def test_f11_exit_restores_maximized_state() -> None:
    QRect, Qt = _qt()
    win = _FakeMainWindow(Qt.WindowState.WindowNoState, video_fs=False)
    win._window_fs_prev = (
        Qt.WindowState.WindowMaximized,
        win.geometry(),
        win.normalGeometry(),
    )
    win.isFullScreen = lambda: True

    _main_window_class().toggle_fullscreen(win)

    assert win.calls[-1] == "maximized"
    assert win.calls.count("maximized") == 1


def test_f11_enter_saves_state_before_going_fullscreen() -> None:
    QRect, Qt = _qt()
    win = _FakeMainWindow(Qt.WindowState.WindowNoState, video_fs=False)
    win.isFullScreen = lambda: False

    _main_window_class().toggle_fullscreen(win)

    assert win.calls == ["fullscreen"]
    assert win._window_fs_prev is not None


def test_leaving_f11_from_windowed_restores_exact_geometry() -> None:
    QRect, Qt = _qt()
    win = _FakeMainWindow(Qt.WindowState.WindowNoState, video_fs=False)
    saved = QRect(200, 150, 900, 640)
    win._window_fs_prev = (Qt.WindowState.WindowNoState, saved, saved)

    _main_window_class()._exit_window_fullscreen(win)

    assert win.calls[0] == "normal"
    assert ("geometry", saved) in win.calls
