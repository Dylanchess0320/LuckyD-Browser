"""Regression pins for the 2026-10-23 Twitch / Gmail / Poki fix.

Dylan, on the shipped 10.8.3 build, hit three defects:

1. Twitch Error 4000 ("video unavailable or not supported") and silent
   Poki games — root cause is the shipped Qt WebEngine build's missing
   proprietary decoders (H.264 + AAC absent; VP9/Opus/AV1/MP3/OGG fine),
   proven by a real-Qt probe (canPlayType / MediaSource.isTypeSupported /
   decodeAudioData). That half is build-level and cannot be fixed here;
   what this file pins is the code-level half: the ad-block interceptor
   must NEVER eat Twitch HLS traffic or Google auth/static traffic, so
   request blocking can never masquerade as (or compound) a codec error.

2. Gmail sign-in rejected with Google's "browser or app may not be
   secure" — the profile UA silently fell back to a stale Chrome/126
   constant (desktop_ua() called .majorVersion() on the plain str that
   PySide6's qWebEngineChromiumVersion() actually returns), while the
   interceptor's sign-in pin rewrote headers to a full-version UA plus a
   fabricated "Google Chrome" Client-Hints brand that the JS-visible
   navigator.userAgent / userAgentData contradicted. These tests pin the
   fix: desktop_ua() parses the str engine version, the fallback
   constant tracks the shipped engine major, and sign-in requests are
   never blocked and never rewritten.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

for _mod in (
    "PySide6",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# AdBlockInterceptor subclasses QWebEngineUrlRequestInterceptor — a
# MagicMock base would silently degrade it to a mock (no real methods).
_QTWE = sys.modules["PySide6.QtWebEngineCore"]
if not isinstance(getattr(_QTWE, "QWebEngineUrlRequestInterceptor", None), type):

    class _InterceptorStub:
        def __init__(self, *args, **kwargs):
            pass

    _QTWE.QWebEngineUrlRequestInterceptor = _InterceptorStub


class _FakeInfo:
    """QWebEngineUrlRequestInfo fake: records block() and setHttpHeader()."""

    def __init__(self, url: str):
        self._url = url
        self.blocked = False
        self.headers: dict[str, str] = {}

    def requestUrl(self):  # noqa: N802 - mirrors QWebEngine API
        url = self._url

        class _U:
            def host(self):
                return url.split("://", 1)[1].split("/", 1)[0].split(":")[0]

            def toString(self):  # noqa: N802
                return url

        return _U()

    def block(self, b: bool):
        self.blocked = bool(b)

    def setHttpHeader(self, name, value):  # noqa: N802 - mirrors QWebEngine API
        self.headers[bytes(name).decode()] = bytes(value).decode()


# ── Interceptor: media + auth traffic always passes ───────────────────

_MUST_PASS_URLS = [
    # Twitch playback path (HLS via usher + edge hosts, player assets)
    "https://www.twitch.tv/somechannel",
    "https://usher.ttvnw.net/api/channel/hls/somechannel.m3u8?token=abc&sig=def",
    "https://video-edge-a1b2c3.ord03.abs.hls.ttvnw.net/v1/segment-00001.ts",
    "https://vod-secure.ttvnw.net/abc123/chunked/index.m3u8",
    "https://hls-cdn.akamaized.net/hls/live/1234567/master.m3u8",
    "https://assets.twitch.tv/assets/player-core.js",
    # Google sign-in + Gmail + static auth assets
    "https://accounts.google.com/v3/signin/identifier",
    "https://myaccount.google.com/",
    "https://mail.google.com/mail/u/0/",
    "https://www.gstatic.com/accounts/static/_/js/k=boq.js",
    "https://ssl.gstatic.com/gb/images/sprites.png",
]


def test_media_and_auth_hosts_are_never_blocked() -> None:
    """With the app's REAL blocklist and blocking ON (Dylan's config),
    no Twitch HLS / Google auth request may be blocked."""
    from browser_core.adblock import AdBlockInterceptor

    interceptor = AdBlockInterceptor(enabled=True)  # default = real list
    for url in _MUST_PASS_URLS:
        info = _FakeInfo(url)
        interceptor.interceptRequest(info)
        assert not info.blocked, f"must not block: {url}"
    assert interceptor.blocked_count == 0


def test_interceptor_still_blocks_real_ad_hosts() -> None:
    """Counter-proof the matrix above is live: known ad hosts DO block."""
    from browser_core.adblock import AdBlockInterceptor

    interceptor = AdBlockInterceptor(enabled=True)
    info = _FakeInfo("https://www.google-analytics.com/g/collect?v=2")
    interceptor.interceptRequest(info)
    assert info.blocked
    assert interceptor.blocked_count == 1


def test_signin_requests_get_no_header_rewrites() -> None:
    """The old pin's header surgery must never come back: Google
    cross-checks headers against the JS-visible identity, and any
    divergence reads as an embedded browser."""
    from browser_core.adblock import AdBlockInterceptor

    interceptor = AdBlockInterceptor(enabled=True)
    for url in (
        "https://accounts.google.com/v3/signin/identifier",
        "https://myaccount.google.com/personal-info",
    ):
        info = _FakeInfo(url)
        interceptor.interceptRequest(info)
        assert not info.blocked
        assert info.headers == {}


# ── Profile UA: the str-version regression ────────────────────────────


def _load_profile():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "profile_under_test_tg", _BROWSER_DIR / "browser_core" / "profile.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_desktop_ua_uses_engine_major_from_str_version(monkeypatch) -> None:
    """PySide6's qWebEngineChromiumVersion() returns a plain str.

    The pre-fix desktop_ua() called .majorVersion() on it, swallowed the
    AttributeError, and shipped the stale Chrome/126 fallback while the
    engine was Chromium 140 — a UA/engine mismatch Google can detect.
    """
    mod = _load_profile()
    monkeypatch.setattr(_QTWE, "qWebEngineChromiumVersion", lambda: "140.0.7339.225", raising=False)
    assert mod.chromium_version() == "140.0.7339.225"
    assert mod.desktop_ua() == (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    )


def test_desktop_ua_falls_back_when_version_query_raises(monkeypatch) -> None:
    mod = _load_profile()

    def _boom():
        raise RuntimeError("no engine")

    monkeypatch.setattr(_QTWE, "qWebEngineChromiumVersion", _boom, raising=False)
    assert mod.chromium_version() == ""
    assert mod.desktop_ua() == mod.DESKTOP_UA


def test_desktop_ua_falls_back_on_garbage_version(monkeypatch) -> None:
    """A non-version return (mock reprs etc.) must never leak into a UA."""
    mod = _load_profile()
    monkeypatch.setattr(
        _QTWE, "qWebEngineChromiumVersion", lambda: "<MagicMock id='123'>", raising=False
    )
    assert mod.chromium_version() == ""
    assert mod.desktop_ua() == mod.DESKTOP_UA


def test_fallback_ua_tracks_shipped_engine_major() -> None:
    """DESKTOP_UA is the last known-good UA for the shipped engine.

    If the bundled Chromium major moves, this pin fails on purpose: the
    fallback must be bumped with it, or a query failure would silently
    re-create the stale-UA mismatch fixed on 2026-10-23.
    """
    mod = _load_profile()
    assert mod.chrome_major(mod.DESKTOP_UA) == 140
    assert "QtWebEngine" not in mod.DESKTOP_UA
