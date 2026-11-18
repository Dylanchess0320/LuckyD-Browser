"""The Qt runtime pin is a crash fix, not a style preference.

QtWebEngine 6.12.0 (Chromium 140.0.7339.225) SIGTRAPs its renderer on pages
that declare legacy encodings such as iso-8859-2 or windows-1251
(QTBUG-149435 / qutebrowser #9003). PySide6 6.11.3 ships Chromium
140.0.7339.264 and renders the same pages correctly.
"""

from __future__ import annotations

from pathlib import Path

_REQUIREMENTS = Path(__file__).resolve().parent.parent / "browser" / "requirements.txt"


def test_pyside6_is_pinned_to_the_non_crashing_engine() -> None:
    requirements = _REQUIREMENTS.read_text(encoding="utf-8")
    assert "PySide6==6.11.3" in requirements
    assert "PySide6>=6.6" not in requirements
    assert "PySide6==6.12" not in requirements
