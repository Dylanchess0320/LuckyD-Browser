"""Ctrl+K command palette fix (Dylan 2026-10-22: "looks broken and confusing").

Diagnosis on real Qt found: the palette window was never sized (100x30,
clipping an unanchored child card whose stylesheet background never painted
on a plain QWidget), Escape was starved by the main window's Esc shortcut,
Enter did nothing after a mouse click, there was no click-away dismissal,
rows never said what they were, and opening dumped ~150 unsorted rows.

Two layers of tests:
- source invariants (always run, no Qt needed), and
- real-Qt offscreen proofs (run only when a REAL PySide6 is importable —
  in CI they skip; locally: QT_QPA_PLATFORM=offscreen <venv with PySide6>).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))

_SRC = (_BROWSER_DIR / "browser_ui" / "palette.py").read_text(encoding="utf-8")


# ── source invariants ────────────────────────────────────────────────────


def test_palette_has_no_hardcoded_hex_colors() -> None:
    """Every color must come from the active theme's tokens."""
    assert not re.findall(r"#[0-9a-fA-F]{6}\b", _SRC)
    assert not re.findall(r"#[0-9a-fA-F]{3}\b", _SRC)


def test_palette_window_is_the_card() -> None:
    """Layout lives on the window itself; no unanchored child card."""
    assert "QVBoxLayout(self)" in _SRC
    assert "self._cont" not in _SRC
    assert "WA_StyledBackground" in _SRC
    assert "drawRoundedRect" in _SRC  # card painted deterministically


def test_palette_escape_is_claimed_before_the_window_shortcut() -> None:
    assert "ShortcutOverride" in _SRC
    assert "_on_focus_changed" in _SRC  # click-away dismissal
    assert "WindowDeactivate" in _SRC


def test_palette_enter_works_after_mouse_click() -> None:
    """itemActivated covers Enter-in-list; itemDoubleClicked must NOT also
    be connected to activation or actions would fire twice."""
    assert "itemActivated.connect" in _SRC
    assert "itemDoubleClicked.connect" not in _SRC


def test_palette_rows_explain_themselves() -> None:
    assert "ROLE_KIND" in _SRC and "ROLE_HINT" in _SRC and "ROLE_DETAIL" in _SRC
    assert '"Ctrl+H"' in _SRC and '"Ctrl+Shift+A"' in _SRC  # real shortcut hints
    assert "Search tabs, bookmarks, history, actions" in _SRC  # placeholder


def test_palette_caps_and_empty_query_order() -> None:
    assert "MAX_ROWS = 50" in _SRC
    assert "MAX_VISIBLE_ROWS = 9" in _SRC
    assert "EMPTY_CAPS" in _SRC
    assert "_is_subsequence" in _SRC  # subsequence queries match, not just substrings


# ── real-Qt proofs (skip without a real PySide6) ────────────────────────

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")

import pytest


def _real_qt_available() -> bool:
    try:
        from PySide6.QtWidgets import QWidget
    except Exception:
        return False
    return isinstance(QWidget, type) and QWidget.__module__.startswith("PySide6")


@pytest.fixture(scope="module")
def qt_app():
    if not _real_qt_available():
        pytest.skip("needs a real PySide6")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(["test-palette"])
    yield app


@pytest.fixture()
def window(qt_app, tmp_path):
    from browser_core.adblock import AdBlockInterceptor
    from browser_core.permissions import PermissionStore
    from browser_core.profile import default_profile
    from browser_core.session import SessionStore
    from browser_core.settings import SettingsStore
    from browser_core.storage import Storage
    from browser_core.workspaces import WorkspaceStore

    class FakeApp:
        def __init__(self, **kw):
            self.__dict__.update(kw)

        def __getattr__(self, _name):
            return lambda *a, **k: None

    storage = Storage(tmp_path / "storage.db")
    for i in range(5):
        storage.add_bookmark(f"https://bookmark{i}.example.com/page", f"Bookmark {i}")
    storage.add_bookmark("https://www.facebook.com/", "Facebook")
    for i in range(8):
        storage.add_visit(f"https://history{i}.example.com/article", f"History page {i}")

    fake_app = FakeApp(
        qapp=qt_app,
        settings=SettingsStore(tmp_path / "settings.json"),
        storage=storage,
        workspaces=WorkspaceStore(),
        permissions=PermissionStore(),
        session_store=SessionStore(),
        profile=default_profile(),
        adblock=AdBlockInterceptor(enabled=False),
        harness=None,
        control_server=None,
        terminal_server=None,
        install_local_auth_cookies=lambda profile: None,
    )
    from browser_ui.main_window import MainWindow

    win = MainWindow(fake_app)
    fake_app.windows = [win]
    win.resize(1280, 800)
    win.show()
    for _ in range(20):
        qt_app.processEvents()
    win.new_tab()
    win.new_tab()
    for _ in range(20):
        qt_app.processEvents()
    yield win, storage
    win.close()


def _open(qt_app, win):
    win.show_palette()
    for _ in range(10):
        qt_app.processEvents()
    return win._palette


@pytest.mark.parametrize("theme", ["neon", "arctic"])
def test_palette_geometry_card_and_selection_render(qt_app, window, theme) -> None:
    from browser_ui.palette import ROLE_KIND
    from browser_ui.theme import palette as theme_palette
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QPixmap

    win, _storage = window
    win.settings.set("theme", theme)
    p = theme_palette(win.settings)
    pal = _open(qt_app, win)
    assert pal.isVisible()
    # window size IS the card size now (was a 100x30 sliver)
    assert pal.width() >= 600
    expect_x = win.x() + (win.width() - pal.width()) // 2
    assert abs(pal.x() - expect_x) <= 2
    assert pal.y() + pal.height() <= win.y() + win.height()
    # empty query: actions lead, kinds are carried on the rows
    kinds = [pal._lst.item(i).data(ROLE_KIND) for i in range(pal._lst.count())]
    assert kinds[0] == "Action"
    assert kinds.count("Tab") == 3
    # rendered pixels: card paints the panel token, selection the accent
    pm = QPixmap(pal.size())
    pm.fill(Qt.GlobalColor.transparent)
    pal.render(pm, QPoint(0, 0))
    img = pm.toImage()
    assert img.pixelColor(4, pal.height() // 2).name().lower() == p["panel"].lower()
    r = pal._lst.visualItemRect(pal._lst.item(0))
    got = img.pixelColor(pal._lst.x() + 60, pal._lst.y() + r.center().y()).name().lower()
    assert got == p["accent"].lower()
    pal.close_palette()


@pytest.mark.parametrize("theme", ["neon", "arctic"])
def test_palette_escape_closes_and_starves_window_shortcut(qt_app, window, theme) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    win, _storage = window
    win.settings.set("theme", theme)
    fired = []
    win._escape = lambda: fired.append(1)
    pal = _open(qt_app, win)
    QTest.keyClick(pal._e, Qt.Key.Key_Escape)
    qt_app.processEvents()
    assert not pal.isVisible()
    assert not fired  # the main window's Esc shortcut must not eat palette Esc
    # and from the list, after a mouse click
    pal = _open(qt_app, win)
    r = pal._lst.visualItemRect(pal._lst.item(2))
    QTest.mouseClick(pal._lst.viewport(), Qt.MouseButton.LeftButton, pos=r.center())
    qt_app.processEvents()
    QTest.keyClick(pal._lst, Qt.Key.Key_Escape)
    qt_app.processEvents()
    assert not pal.isVisible()


def test_palette_enter_after_mouse_click_activates(qt_app, window) -> None:
    from browser_ui.palette import ROLE_ACTION
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    win, _storage = window
    pal = _open(qt_app, win)
    fired = []
    pal._lst.item(2).setData(ROLE_ACTION, lambda: fired.append(1))
    r = pal._lst.visualItemRect(pal._lst.item(2))
    QTest.mouseClick(pal._lst.viewport(), Qt.MouseButton.LeftButton, pos=r.center())
    qt_app.processEvents()
    QTest.keyClick(pal._lst, Qt.Key.Key_Return)
    qt_app.processEvents()
    assert fired == [1]
    assert not pal.isVisible()


def test_palette_click_away_dismisses(qt_app, window) -> None:
    win, _storage = window
    pal = _open(qt_app, win)
    win.focus_omnibox()  # what clicking the page/chrome does to keyboard focus
    for _ in range(10):
        qt_app.processEvents()
    assert not pal.isVisible()


def test_palette_rows_hints_and_matching(qt_app, window) -> None:
    from browser_ui.palette import ROLE_HINT

    win, _storage = window
    pal = _open(qt_app, win)
    hints = {
        pal._lst.item(i).text(): pal._lst.item(i).data(ROLE_HINT) for i in range(pal._lst.count())
    }
    assert hints.get("History") == "Ctrl+H"
    assert hints.get("Settings") == "Ctrl+,"
    assert "bookmarks" in pal._e.placeholderText()
    # subsequence matching: "fb" finds the Facebook bookmark
    pal._e.setText("fb")
    qt_app.processEvents()
    texts = [pal._lst.item(i).text() for i in range(pal._lst.count())]
    assert any("Facebook" in t for t in texts)
    # no-results: one info row, Enter does not close the palette
    pal._e.setText("zzzzqqqq")
    qt_app.processEvents()
    assert pal._lst.count() == 1
    assert "No matches" in pal._lst.item(0).text()
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    QTest.keyClick(pal._e, Qt.Key.Key_Return)
    qt_app.processEvents()
    assert pal.isVisible()
    pal.close_palette()


def test_palette_row_cap_and_overflow_footer(qt_app, window) -> None:
    win, storage = window
    for i in range(60):
        storage.add_visit(f"https://hist{i}.example.com/x", f"History page extra {i}")
    pal = _open(qt_app, win)
    pal._e.setText("history")
    qt_app.processEvents()
    assert pal._lst.count() == 51  # 50 rows + footer
    assert "more matches" in (pal._lst.item(50).text() or "")
    pal.close_palette()


def test_palette_small_window_stays_inside(qt_app, window) -> None:
    win, _storage = window
    win.resize(800, 600)
    for _ in range(10):
        qt_app.processEvents()
    pal = _open(qt_app, win)
    assert pal.x() >= win.x()
    assert pal.x() + pal.width() <= win.x() + win.width()
    assert pal.y() + pal.height() <= win.y() + win.height()
    pal.close_palette()
