"""Night-1 browser-core audit: updater.py (version logic, release parsing,
checker thread) and adblock.py (domain/pattern blocking)."""

from __future__ import annotations

import json
import sys
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock

# updater.py imports PySide6.QtCore at top level; adblock.py imports
# PySide6.QtWebEngineCore. Neither is installed headless — mock both.
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
    """Stand-in for QWebEngineUrlRequestInterceptor.

    With a MagicMock base, `class AdBlockInterceptor(base)` degrades to a
    mock — the real blocking logic would be untestable. This stub keeps the
    subclass real while staying Qt-free.
    """

    def __init__(self, *args, **kwargs):
        pass


class _QtThreadStub:
    """Stand-in for QThread — same mock-degradation problem for the
    updater's UpdateChecker / ReleaseDownloader."""

    def __init__(self, *args, **kwargs):
        self._interrupted = False

    def start(self):
        pass

    def wait(self, *args):
        pass

    def isRunning(self):  # noqa: N802 — mirrors QThread API
        return False

    def requestInterruption(self):  # noqa: N802 — mirrors QThread API
        self._interrupted = True

    def isInterruptionRequested(self):  # noqa: N802 — mirrors QThread API
        return self._interrupted


sys.modules["PySide6.QtWebEngineCore"].QWebEngineUrlRequestInterceptor = _QtInterceptorStub
sys.modules["PySide6.QtCore"].QThread = _QtThreadStub

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from browser_core import updater
from browser_core.adblock import AdBlockInterceptor
from browser_core.updater import (
    GitHubReleasesSource,
    ReleaseDownloader,
    UpdateChecker,
    asset_sha256,
    is_installer_asset,
    is_newer,
    parse_version,
)

# ── version logic ────────────────────────────────────────────────────


def test_parse_version() -> None:
    assert parse_version("v1.4.0") == (1, 4, 0)
    assert parse_version("1.4") == (1, 4)
    assert parse_version("9.0.0") == (9, 0, 0)
    assert parse_version("") == (0,)
    assert parse_version("v2.5.8-beta") == (2, 5, 8)


def test_is_newer() -> None:
    assert is_newer("1.4.1", "1.4")
    assert is_newer("9.0.1", "9.0.0")
    assert not is_newer("1.4", "1.4.0")  # equal after padding
    assert not is_newer("1.3.9", "1.4")
    assert not is_newer("abc", "1.0")


def test_is_installer_asset() -> None:
    good = {"name": "LuckyDBrowserSetup-9.0.0.exe"}
    assert is_installer_asset(good)
    assert is_installer_asset(good, "9.0.0")
    assert is_installer_asset(good, "v9.0.0")
    assert not is_installer_asset(good, "8.0.0")
    assert not is_installer_asset({"name": "setup.exe"})
    assert not is_installer_asset({"name": "LuckyDBrowserSetup-9.0.0.zip"})
    assert not is_installer_asset({"name": "LuckyDBrowser-src.zip"})
    assert not is_installer_asset({})


def test_asset_sha256_validation() -> None:
    digest = "sha256:" + "ab" * 32
    assert asset_sha256({"digest": digest}) == "ab" * 32
    assert asset_sha256({"digest": "SHA256:" + "AB" * 32}) == "ab" * 32
    assert asset_sha256({"digest": "md5:" + "ab" * 32}) == ""
    assert asset_sha256({"digest": "sha256:xyz"}) == ""
    assert asset_sha256({}) == ""


# ── GitHub release source ────────────────────────────────────────────


class _FakeHTTPResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(payload: bytes):
    def _open(req, timeout=None):
        return _FakeHTTPResponse(payload)

    return _open


def _release_json(**kw):
    data = {
        "tag_name": "v9.1.0",
        "name": "LuckyD 9.1.0",
        "html_url": "https://github.com/x/y/releases/tag/v9.1.0",
        "body": "notes here",
        "assets": [
            {
                "name": "LuckyDBrowser-src.zip",
                "browser_download_url": "https://x/src.zip",
                "size": 1,
                "digest": "",
            },
            {
                "name": "LuckyDBrowserSetup-9.1.0.exe",
                "browser_download_url": "https://x/setup.exe",
                "size": 12345,
                "digest": "sha256:" + "cd" * 32,
            },
        ],
    }
    data.update(kw)
    return json.dumps(data).encode()


def test_fetch_latest_picks_installer(monkeypatch) -> None:
    monkeypatch.setattr(updater.urllib.request, "urlopen", _fake_urlopen(_release_json()))
    info = GitHubReleasesSource().fetch_latest()
    assert info.version == "9.1.0"
    assert info.installer_url == "https://x/setup.exe"
    assert info.installer_size == 12345
    assert info.installer_sha256 == "cd" * 32
    assert info.notes == "notes here"


def test_fetch_latest_falls_back_to_any_installer(monkeypatch) -> None:
    """Version-scoped pick misses (asset renamed) → unfiltered pick still wins."""
    monkeypatch.setattr(
        updater.urllib.request,
        "urlopen",
        _fake_urlopen(_release_json(tag_name="v9.2.0")),
    )
    info = GitHubReleasesSource().fetch_latest()
    assert info.installer_url == "https://x/setup.exe"


def test_fetch_latest_no_assets(monkeypatch) -> None:
    monkeypatch.setattr(updater.urllib.request, "urlopen", _fake_urlopen(_release_json(assets=[])))
    info = GitHubReleasesSource().fetch_latest()
    assert info.version == "9.1.0" and info.installer_url == ""


def test_fetch_latest_atom(monkeypatch) -> None:
    feed = """<?xml version="1.0"?><feed>
<entry><id>tag:github.com,2026:Repository/1/v9.2.0</id>
<title>v9.2.0</title><link href="https://github.com/x/y/releases/tag/v9.2.0"/>
<content>cool &lt;stuff&gt;</content></entry></feed>"""
    monkeypatch.setattr(updater.urllib.request, "urlopen", _fake_urlopen(feed.encode()))
    monkeypatch.setattr(updater, "_asset_reachable", lambda url, timeout=10.0: True)
    info = GitHubReleasesSource().fetch_latest_atom()
    assert info.version == "9.2.0"
    assert info.installer_url.endswith("LuckyDBrowserSetup-9.2.0.exe")
    assert info.notes == "cool <stuff>"


# ── UpdateChecker thread ─────────────────────────────────────────────


class _Src:
    def __init__(self, behavior):
        self._behavior = behavior

    def fetch_latest(self):
        b = self._behavior
        if isinstance(b, Exception):
            raise b
        return b

    def fetch_latest_atom(self):
        return None


def _checker_signals():
    # NOTE: with PySide6 mocked, every `Signal(...)` call returns the SAME
    # mock object, so the three class attrs are aliases. Give each test run
    # distinct signals to assert on.
    UpdateChecker.update_available = MagicMock()
    UpdateChecker.up_to_date = MagicMock()
    UpdateChecker.failed = MagicMock()


def test_checker_reports_update(monkeypatch) -> None:
    from browser_core.updater import ReleaseInfo

    _checker_signals()
    src = _Src(ReleaseInfo(version="99.0.0", url="https://x"))
    monkeypatch.setattr(updater, "CURRENT_VERSION", "9.0.0")
    UpdateChecker(src).run()
    UpdateChecker.update_available.emit.assert_called_once()
    payload = UpdateChecker.update_available.emit.call_args[0][0]
    assert payload["version"] == "99.0.0"


def test_checker_up_to_date(monkeypatch) -> None:
    from browser_core.updater import ReleaseInfo

    _checker_signals()
    monkeypatch.setattr(updater, "CURRENT_VERSION", "9.0.0")
    UpdateChecker(_Src(ReleaseInfo(version="9.0.0", url="https://x"))).run()
    UpdateChecker.up_to_date.emit.assert_called_once()


def test_checker_404_means_up_to_date() -> None:
    _checker_signals()
    err = urllib.error.HTTPError("http://x", 404, "nf", {}, BytesIO(b""))
    UpdateChecker(_Src(err)).run()
    UpdateChecker.up_to_date.emit.assert_called_once()
    UpdateChecker.failed.emit.assert_not_called()


def test_checker_403_falls_back_to_atom(monkeypatch) -> None:
    """Rate-limited API → Atom fallback; both failing → failed signal."""
    from browser_core.updater import ReleaseInfo

    _checker_signals()
    err = urllib.error.HTTPError("http://x", 403, "rl", {}, BytesIO(b""))
    src = _Src(err)
    src.fetch_latest_atom = lambda: ReleaseInfo(version="9.0.0", url="https://x")
    monkeypatch.setattr(updater, "CURRENT_VERSION", "9.0.0")
    UpdateChecker(src).run()
    UpdateChecker.up_to_date.emit.assert_called_once()


def test_checker_offline_no_fallback_fails() -> None:
    _checker_signals()
    UpdateChecker(_Src(RuntimeError("dns down"))).run()
    UpdateChecker.failed.emit.assert_called_once()
    assert "dns down" in UpdateChecker.failed.emit.call_args[0][0]


# ── ReleaseDownloader guards ─────────────────────────────────────────


def test_downloader_rejects_non_https(tmp_path: Path) -> None:
    ReleaseDownloader.failed.reset_mock()
    dl = ReleaseDownloader("http://evil/x.exe", tmp_path / "x.exe")
    dl.run()
    ReleaseDownloader.failed.emit.assert_called_once()
    assert "Unsupported download URL" in ReleaseDownloader.failed.emit.call_args[0][0]


# ── adblock ──────────────────────────────────────────────────────────


def _interceptor_with_list(tmp_path: Path, lines: list[str]) -> AdBlockInterceptor:
    p = tmp_path / "adblock.txt"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return AdBlockInterceptor(enabled=True, list_path=p)


class _Info:
    def __init__(self, url: str):
        self._url = url
        self.blocked = False

    def requestUrl(self):  # noqa: N802 — mirrors QWebEngine API
        class _U:
            def __init__(self, url):
                self._url = url

            def host(self):
                return self._url.split("://", 1)[1].split("/", 1)[0].split(":")[0]

            def toString(self):  # noqa: N802 — mirrors QWebEngine API
                return self._url

        return _U(self._url)

    def block(self, b: bool):
        self.blocked = b


def test_adblock_domain_suffix(tmp_path: Path) -> None:
    i = _interceptor_with_list(tmp_path, ["ads.example.com", "# comment", "", "tracker.io"])
    assert i._domain_is_blocked("ads.example.com")
    assert i._domain_is_blocked("sub.ads.example.com")
    assert i._domain_is_blocked("ADS.EXAMPLE.COM")
    assert not i._domain_is_blocked("example.com")
    assert not i._domain_is_blocked("notads.example.com")
    assert not i._domain_is_blocked("goodexample.com")


def test_adblock_missing_list_is_empty(tmp_path: Path) -> None:
    i = AdBlockInterceptor(enabled=True, list_path=tmp_path / "nope.txt")
    assert not i._domain_is_blocked("ads.example.com")


def test_adblock_youtube_ad_patterns(tmp_path: Path) -> None:
    i = AdBlockInterceptor(enabled=True, list_path=tmp_path / "nope.txt")
    assert i._url_matches_ad_pattern("https://www.youtube.com/pagead/ads?x=1")
    assert i._url_matches_ad_pattern("https://imasdk.googleapis.com/js/sdkloader/x.js")
    assert i._url_matches_ad_pattern("https://www.youtube.com/api/stats/ads?x")
    # content hosts are NOT blocked by these patterns
    assert not i._url_matches_ad_pattern("https://www.youtube.com/watch?v=abc")
    assert not i._url_matches_ad_pattern(
        "https://rr1---sn-xyz.googlevideo.com/videoplayback?ctier=1&x=1"
    )


def test_adblock_intercept_request(tmp_path: Path) -> None:
    i = _interceptor_with_list(tmp_path, ["ads.example.com"])
    bad, good = _Info("https://ads.example.com/banner.js"), _Info("https://a.com/app.js")
    i.interceptRequest(bad)
    i.interceptRequest(good)
    assert bad.blocked and not good.blocked
    assert i.blocked_count == 1


def test_adblock_disabled_never_blocks(tmp_path: Path) -> None:
    i = _interceptor_with_list(tmp_path, ["ads.example.com"])
    i.set_enabled(False)
    assert not i.is_enabled()
    info = _Info("https://ads.example.com/banner.js")
    i.interceptRequest(info)
    assert not info.blocked and i.blocked_count == 0


def test_adblock_reload_and_toggle(tmp_path: Path) -> None:
    p = tmp_path / "adblock.txt"
    p.write_text("one.com\n", encoding="utf-8")
    i = AdBlockInterceptor(enabled=True, list_path=p)
    assert i._domain_is_blocked("one.com")
    p.write_text("two.com\n", encoding="utf-8")
    i.reload()
    assert not i._domain_is_blocked("one.com")
    assert i._domain_is_blocked("two.com")
    i.set_enabled(False)
    assert not i.is_enabled()
