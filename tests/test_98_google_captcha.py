"""task_0003: Google robot/CAPTCHA fix - DDG default, adblock allowlist."""

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

# adblock.py imports PySide6.QtWebEngineCore at top level, which is not
# installed headless — mock it like test_night1_updater.py does. The stub
# base keeps AdBlockInterceptor a real subclass with testable logic.
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


class _QtInterceptorStub:
    def __init__(self, *args, **kwargs):
        pass


sys.modules["PySide6.QtWebEngineCore"].QWebEngineUrlRequestInterceptor = _QtInterceptorStub

from browser_core.adblock import AdBlockInterceptor, _is_captcha_safe_url
from browser_core.settings import DEFAULTS, SEARCH_ENGINES, SettingsStore


def test_default_search_engine_is_ddg() -> None:
    assert DEFAULTS["search_engine"] == "DuckDuckGo"


def test_search_url_for_builds_ddg_url(tmp_path: Path) -> None:
    s = SettingsStore(tmp_path / "settings.json")
    assert s.get("search_engine") == "DuckDuckGo"
    url = s.search_url_for("hello world")
    assert url.startswith("https://duckduckgo.com/?q=")
    assert "hello+world" in url


def test_google_template_still_resolvable(tmp_path: Path) -> None:
    s = SettingsStore(tmp_path / "settings.json")
    s.set("search_engine", "Google")
    url = s.search_url_for("captcha test")
    assert url.startswith("https://www.google.com/search?q=")
    assert SEARCH_ENGINES["Google"] == "https://www.google.com/search?q={query}"


def test_unknown_engine_falls_back_to_ddg(tmp_path: Path) -> None:
    s = SettingsStore(tmp_path / "settings.json")
    s.set("search_engine", "Nope")
    assert s.search_url_for("x").startswith("https://duckduckgo.com/?q=")


class _Info:
    def __init__(self, url: str):
        self._url = url
        self.blocked = False

    def requestUrl(self):  # noqa: N802 - mirrors QWebEngine API
        url = self._url

        class _U:
            def host(self):
                return url.split("://", 1)[1].split("/", 1)[0].split(":")[0]

            def toString(self):  # noqa: N802 - mirrors QWebEngine API
                return url

        return _U()

    def block(self, b: bool):
        self.blocked = b


def _aggressive_interceptor(tmp_path: Path) -> AdBlockInterceptor:
    p = tmp_path / "adblock.txt"
    p.write_text("google.com\nads.example.com\n", encoding="utf-8")
    return AdBlockInterceptor(enabled=True, list_path=p)


def test_google_search_never_blocked_even_when_listed(tmp_path: Path) -> None:
    i = _aggressive_interceptor(tmp_path)
    info = _Info("https://www.google.com/search?q=cats")
    i.interceptRequest(info)
    assert not info.blocked


def test_sorry_image_never_blocked(tmp_path: Path) -> None:
    i = _aggressive_interceptor(tmp_path)
    info = _Info("https://www.google.com/sorry/image?id=ABC&hl=en")
    i.interceptRequest(info)
    assert not info.blocked
    assert _is_captcha_safe_url("https://www.google.com/sorry/image?id=ABC&hl=en", "www.google.com")


def test_sorry_index_never_blocked(tmp_path: Path) -> None:
    i = _aggressive_interceptor(tmp_path)
    info = _Info("https://www.google.com/sorry/index?continue=x")
    i.interceptRequest(info)
    assert not info.blocked


def test_consent_never_blocked(tmp_path: Path) -> None:
    i = _aggressive_interceptor(tmp_path)
    for url, host in [
        ("https://consent.google.com/ml?continue=x", "consent.google.com"),
        ("https://consent.google.com/m?continue=y", "consent.google.com"),
    ]:
        info = _Info(url)
        i.interceptRequest(info)
        assert not info.blocked, url
        assert _is_captcha_safe_url(url, host)


def test_url_matches_ad_pattern_false_for_sorry_consent(tmp_path: Path) -> None:
    i = AdBlockInterceptor(enabled=True, list_path=tmp_path / "nope.txt")
    assert not i._url_matches_ad_pattern("https://www.google.com/sorry/image?id=ABC&hl=en")
    assert not i._url_matches_ad_pattern("https://consent.google.com/ml?continue=x")
    assert not i._url_matches_ad_pattern("https://www.google.com/sorry/index?continue=x")


def test_url_matches_ad_pattern_true_for_real_youtube_ad(tmp_path: Path) -> None:
    i = AdBlockInterceptor(enabled=True, list_path=tmp_path / "nope.txt")
    assert i._url_matches_ad_pattern("https://www.youtube.com/pagead/ads?x=1")
    assert i._url_matches_ad_pattern("https://imasdk.googleapis.com/js/sdkloader/x.js")


def test_real_tracker_still_blocked(tmp_path: Path) -> None:
    i = _aggressive_interceptor(tmp_path)
    info = _Info("https://ads.example.com/banner.js")
    i.interceptRequest(info)
    assert info.blocked


def test_dashboard_default_engine_is_ddg() -> None:
    text = (_BROWSER_DIR / "browser_core" / "dashboard.py").read_text(encoding="utf-8")
    assert "localStorage.getItem('ld_engine') || 'ddg'" in text
    assert "ddg: 'https://duckduckgo.com/?q='" in text
    assert "google: 'https://www.google.com/search?q='" in text


class _FakeProfile:
    def __init__(self, with_methods: bool = True):
        self.calls: dict = {}
        if with_methods:
            self.setHttpAcceptLanguage = self._record_lang  # type: ignore[attr-defined]
            self.setHttpUserAgent = self._record_ua  # type: ignore[attr-defined]

    def _record_lang(self, value: str) -> None:
        self.calls["lang"] = value

    def _record_ua(self, value: str) -> None:
        self.calls["ua"] = value


def test_profile_helpers_exist_and_are_startup_safe() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "profile_under_test", _BROWSER_DIR / "browser_core" / "profile.py"
    )
    assert spec is not None and spec.loader is not None
    profile_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(profile_mod)  # type: ignore[union-attr]

    assert hasattr(profile_mod, "_enable_language")
    assert hasattr(profile_mod, "_enable_desktop_ua")
    assert "Chrome/126" in profile_mod.DESKTOP_UA
    assert "en-US" in profile_mod.ACCEPT_LANGUAGE
    fake = _FakeProfile(with_methods=True)
    profile_mod._enable_language(fake)  # type: ignore[arg-type]
    profile_mod._enable_desktop_ua(fake)  # type: ignore[arg-type]
    assert fake.calls["lang"] == profile_mod.ACCEPT_LANGUAGE
    assert fake.calls["ua"] == profile_mod.DESKTOP_UA
    bare = _FakeProfile(with_methods=False)
    profile_mod._enable_language(bare)  # type: ignore[arg-type]
    profile_mod._enable_desktop_ua(bare)  # type: ignore[arg-type]
    assert bare.calls == {}


def test_dialogs_default_falls_back_to_ddg() -> None:
    text = (_BROWSER_DIR / "browser_ui" / "dialogs.py").read_text(encoding="utf-8")
    assert 'settings.get("search_engine", "DuckDuckGo")' in text


def test_desktop_ua_falls_back_to_constant_when_engine_query_fails() -> None:
    """desktop_ua() never raises and returns a Chrome UA.

    With Qt mocked, qWebEngineChromiumVersion() is a MagicMock whose
    majorVersion() is not an int >= 100, so the fallback fires.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "profile_under_test2", _BROWSER_DIR / "browser_core" / "profile.py"
    )
    assert spec is not None and spec.loader is not None
    profile_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(profile_mod)  # type: ignore[union-attr]

    ua = profile_mod.desktop_ua()
    assert isinstance(ua, str)
    assert "Chrome/" in ua and "Safari/537.36" in ua
    # mocked Qt → fallback to the last known-good constant
    assert ua == profile_mod.DESKTOP_UA


def test_is_google_sorry_url() -> None:
    from browser_ui.web_view import _is_google_sorry_url as is_sorry

    assert is_sorry("https://www.google.com/sorry/index?continue=...")
    assert is_sorry("https://sorry.google.com/sorry/index")
    assert is_sorry("HTTPS://WWW.GOOGLE.COM/SORRY/INDEX")
    assert not is_sorry("https://www.google.com/search?q=hello")
    assert not is_sorry("https://www.bing.com/")
    assert not is_sorry("")


def test_omnibox_uses_settings_search_url_for() -> None:
    text = (_BROWSER_DIR / "browser_ui" / "omnibox.py").read_text(encoding="utf-8")
    assert "self._settings.search_url_for(text)" in text
    assert "google.com/search" not in text
