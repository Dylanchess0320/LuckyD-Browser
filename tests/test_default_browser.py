"""P2: default-browser registration + backend kill list in the installer.

The setup registers per-user (HKCU) StartMenuInternet capabilities so
LuckyD appears in Default Apps, and PrepareToInstall stops the agent
backends too (orphaned luckyd-code/cli held _internal files locked and
failed two 10.2.3 upgrades with DeleteFile code 5).
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ISS = REPO / "browser" / "installer" / "LuckyDBrowser.iss"


def _text() -> str:
    return ISS.read_text(encoding="utf-8-sig")


class TestDefaultBrowserRegistration:
    def test_capabilities_and_registered_app(self):
        text = _text()
        assert "[Registry]" in text
        assert "StartMenuInternet" in text
        assert "Software\\RegisteredApplications" in text
        assert "LuckyDHTML" in text

    def test_per_user_only(self):
        text = _text()
        assert "Root: HKLM" not in text
        assert "Root: HKCU" in text

    def test_protocol_and_file_associations(self):
        text = _text()
        assert '"http"' in text and '"https"' in text
        assert '".htm"' in text and '".html"' in text
        assert '""%1""' in text  # open command receives the URL

    def test_backend_kill_list(self):
        text = _text()
        assert "luckyd-code.exe" in text
        assert "luckyd-cli.exe" in text
        assert "QtWebEngineProcess.exe" in text
