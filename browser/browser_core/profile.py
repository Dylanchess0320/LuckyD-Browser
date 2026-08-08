"""WebEngine profile management: persistent default + off-the-record incognito."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineSettings


def default_profile() -> QWebEngineProfile:
    """The shared persistent profile (cookies, cache, logins survive restarts)."""
    profile = QWebEngineProfile.defaultProfile()
    _enable_spellcheck(profile)
    _enable_fullscreen(profile)
    return profile


def incognito_profile(parent=None) -> QWebEngineProfile:
    """Off-the-record profile: nothing is written to disk, ever.

    A QWebEngineProfile created without a storage name is off-the-record.
    The caller must keep a reference alive for as long as the window lives.
    """
    profile = QWebEngineProfile(parent)
    _enable_spellcheck(profile)
    _enable_fullscreen(profile)
    return profile


def _enable_fullscreen(profile: QWebEngineProfile) -> None:
    """Turn on the HTML5 Fullscreen API — opt-in and OFF by default in Qt
    WebEngine. Without it, requestFullscreen() throws "Fullscreen is not
    supported" and every video player's fullscreen button is dead. The window
    still has to honor fullScreenRequested (main_window hides the chrome)."""
    profile.settings().setAttribute(
        QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True
    )


def _enable_spellcheck(profile: QWebEngineProfile) -> None:
    """Spell checking via hunspell .bdic dictionaries when they exist.

    Qt WebEngine needs compiled dictionaries (qtwebengine_dictionaries dir);
    without them it logs a warning and disables. We point the search path at
    our assets folder so users (and the installer) can drop en-US.bdic in.
    """
    try:
        import os

        dict_dir = Path(__file__).resolve().parent.parent / "assets" / "qtwebengine_dictionaries"
        dict_dir.mkdir(parents=True, exist_ok=True)
        if any(dict_dir.glob("*.bdic")):
            os.environ.setdefault("QTWEBENGINE_DICTIONARIES_PATH", str(dict_dir))
            profile.setSpellCheckEnabled(True)
            if not profile.spellCheckLanguages():
                profile.setSpellCheckLanguages(["en-US"])
    except Exception:
        pass  # spellcheck is a nicety, never a startup blocker
