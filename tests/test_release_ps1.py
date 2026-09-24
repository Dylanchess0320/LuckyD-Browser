"""B5: release.ps1 matches the retired updater + the real repo location.

- The Desktop copy used to assume the repo sits directly on Desktop
  (parent-of-repo == Desktop). The repo lives at ~/LuckyD-Browser now,
  so the script resolves the real Desktop folder instead of writing
  installers into the home directory.
- Banner/comments promised automatic in-app updates; the updater was
  retired in favor of the GitHub releases page, so the script says so.
"""

from __future__ import annotations

from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "browser" / "installer" / "release.ps1"


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8-sig")


class TestReleaseScript:
    def test_desktop_resolved_not_derived(self):
        text = _text()
        assert "GetFolderPath('Desktop')" in text or 'GetFolderPath("Desktop")' in text
        assert "repo sits directly on Desktop" not in text

    def test_no_stale_repo_location(self):
        assert "coding-agent" not in _text().lower()

    def test_no_false_auto_update_promise(self):
        text = _text()
        assert "Existing installs will be offered this update automatically" not in text
        assert "updater.py" not in text or "retired" in text.lower()
