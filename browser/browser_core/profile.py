"""WebEngine profile management: persistent default + off-the-record incognito."""

from __future__ import annotations

from PySide6.QtWebEngineCore import QWebEngineProfile


def default_profile() -> QWebEngineProfile:
    """The shared persistent profile (cookies, cache, logins survive restarts)."""
    return QWebEngineProfile.defaultProfile()


def incognito_profile(parent=None) -> QWebEngineProfile:
    """Off-the-record profile: nothing is written to disk, ever.

    A QWebEngineProfile created without a storage name is off-the-record.
    The caller must keep a reference alive for as long as the window lives.
    """
    return QWebEngineProfile(parent)
