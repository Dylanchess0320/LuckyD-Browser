"""Redirect tests: Help > Check for Updates opens GitHub releases.

The in-app updater was retired as unreliable, so the update path is now a
plain navigation to the public releases page (no network, no threads).
The old toolbar ⬆ badge was removed in 2026-10-22 — Help is the one path.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"

RELEASES_PAGE_URL = "https://github.com/Dylanchess0320/LuckyD-Browser/releases"
MW_SRC = (_BROWSER_DIR / "browser_ui" / "main_window.py").read_text(encoding="utf-8")


def test_releases_url_constant_points_at_github_releases():
    assert f'RELEASES_PAGE_URL = "{RELEASES_PAGE_URL}"' in MW_SRC


def test_badge_and_menu_route_to_the_releases_page():
    # The toolbar ⬆ badge was removed (2026-10-22 — updates already live in
    # the menu); the Help → Check for Updates route to the releases page is
    # the one update path and must keep working.
    assert "self._update_act" not in MW_SRC
    assert '"Check for Updates…", self.check_for_updates' in MW_SRC
    assert "self.open_in_new_tab(QUrl(self._release_page()))" in MW_SRC


def test_no_silent_checks_or_download_threads_remain():
    for banned in (
        "UpdateChecker(",
        "ReleaseDownloader(",
        "QTimer.singleShot(8000",
        "check is already in flight",
        "Downloading update",
        "/VERYSILENT",
        "update_auto_check",
    ):
        assert banned not in MW_SRC, f"retired updater remnant: {banned!r}"
