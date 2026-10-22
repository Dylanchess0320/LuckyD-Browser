"""WebEngine profile management: persistent default + off-the-record incognito."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineSettings

# Desktop Chrome Windows UA — Google's bot risk-score flags QtWebEngine's
# default UA as automated, which pushes every google.com search into the
# unsolvable sorry-image CAPTCHA. Qt profiles set UA globally (no per-host
# override without a request interceptor), so we set a real-Chrome UA for
# all sites: it matches genuine Chrome and drops Google's risk score.
#
# The Chrome MAJOR is read from the real engine at runtime
# (qWebEngineChromiumVersion): a hardcoded UA goes stale (Chrome/126 in
# 2026), and claiming a version NEWER than the engine is itself a bot
# signal Google can spot via feature detection. DESKTOP_UA keeps the last
# known-good value as the fallback when the version query is unavailable.
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def desktop_ua() -> str:
    """Chrome UA whose major version matches the real QtWebEngine engine.

    Never raises: falls back to DESKTOP_UA when the version query fails
    (mocked Qt in tests, old builds, headless imports).
    """
    try:
        from PySide6.QtWebEngineCore import qWebEngineChromiumVersion

        ver: Any = qWebEngineChromiumVersion()
        major = int(ver.majorVersion())
        if major >= 100:
            return (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
            )
    except Exception:
        pass
    return DESKTOP_UA


def chromium_version() -> str:
    """The engine's full Chromium version ("126.0.6478.0"-style), or "".

    The global profile UA advertises ``{major}.0.0.0``; Google sign-in risk
    checks compare the UA against Client Hints and feature signals, so the
    sign-in flow pins the FULL real version instead (see adblock.py).
    Never raises.
    """
    try:
        from PySide6.QtWebEngineCore import qWebEngineChromiumVersion

        full = str(qWebEngineChromiumVersion())
        return full if full and full[0].isdigit() else ""
    except Exception:
        return ""


def signin_ua() -> str:
    """Full-version Chrome UA for Google sign-in hosts.

    Falls back to desktop_ua() when the engine version is unavailable, so
    the header set is never worse than the global UA. Never raises.
    """
    full = chromium_version()
    if full:
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{full} Safari/537.36"
        )
    return desktop_ua()


def chrome_major(ua: str) -> int:
    """The Chrome major version claimed by a UA string (0 when absent)."""
    import re

    match = re.search(r"Chrome/(\d+)", ua or "")
    return int(match.group(1)) if match else 0


def client_hints(ua: str) -> dict[str, str]:
    """Chromium Client Hints consistent with ``ua`` — real Chrome sends
    these on every secure request; QtWebEngine's own hints (when it sends
    any) can carry inconsistent brands, which Google sign-in flags."""
    major = chrome_major(ua)
    if major < 100:
        return {}
    return {
        "sec-ch-ua": f'"Google Chrome";v="{major}", "Chromium";v="{major}", "Not_A Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-ch-ua-platform-version": '"15.0.0"',
        "sec-ch-ua-arch": '"x86"',
        "sec-ch-ua-bitness": '"64"',
    }


# Accept-Language sent on every request. QtWebEngine sends none by default,
# which makes consent.google.com bounce in a redirect loop (no language →
# consent page → no language → …). Setting it breaks the loop.
ACCEPT_LANGUAGE = "en-US,en;q=0.9"


def default_profile() -> QWebEngineProfile:
    """The shared persistent profile (cookies, cache, logins survive restarts)."""
    profile = QWebEngineProfile.defaultProfile()
    _enable_spellcheck(profile)
    _enable_fullscreen(profile)
    _enable_language(profile)
    _enable_desktop_ua(profile)
    return profile


def incognito_profile(parent=None) -> QWebEngineProfile:
    """Off-the-record profile: nothing is written to disk, ever.

    A QWebEngineProfile created without a storage name is off-the-record.
    The caller must keep a reference alive for as long as the window lives.
    """
    profile = QWebEngineProfile(parent)
    _enable_spellcheck(profile)
    _enable_fullscreen(profile)
    _enable_language(profile)
    _enable_desktop_ua(profile)
    return profile


def _enable_fullscreen(profile: QWebEngineProfile) -> None:
    """Turn on the HTML5 Fullscreen API — opt-in and OFF by default in Qt
    WebEngine. Without it, requestFullscreen() throws "Fullscreen is not
    supported" and every video player's fullscreen button is dead. The window
    still has to honor fullScreenRequested (main_window hides the chrome)."""
    profile.settings().setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)


def _enable_language(profile: Any) -> None:
    """Send Accept-Language so consent.google.com stops redirect-looping.

    Guarded with hasattr/try-except so old Qt builds that lack
    setHttpAcceptLanguage never break browser startup.
    """
    try:
        setter = getattr(profile, "setHttpAcceptLanguage", None)
        if callable(setter):
            setter(ACCEPT_LANGUAGE)
    except Exception:
        pass  # language header is a nicety, never a startup blocker


def _enable_desktop_ua(profile: Any) -> None:
    """Set a desktop Chrome UA so Google's risk-score drops.

    Qt's default UA flags automation → unsolvable sorry-image CAPTCHA.
    The UA's Chrome major matches the real engine (desktop_ua()).
    Guarded with hasattr/try-except so old Qt never breaks startup.
    """
    try:
        setter = getattr(profile, "setHttpUserAgent", None)
        if callable(setter):
            setter(desktop_ua())
    except Exception:
        pass  # UA spoof is a nicety, never a startup blocker


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
