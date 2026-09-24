"""Night-4 browser-core audit: updater.py round 2.

Night-1 covered: parse_version, is_newer, is_installer_asset, asset_sha256,
fetch_latest (installer pick, fallback, no assets), atom happy path, checker
(update/up-to-date/404/403-fallback/offline), downloader non-https reject.
This file extends into the remaining paths: fetch_latest error/malformed
payloads, atom with no parseable version (no HEAD probes fired), the
checker 500 path, downloader success/size/sha/cancel paths, and
current_version()/_exe_file_version() branches. Qt is mocked with a stub
QThread; signals get fresh MagicMocks per test (see AGENTS.md: with
PySide6 mocked, Signal() returns one shared mock).
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest

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


class _QtThreadStub:
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


sys.modules["PySide6.QtCore"].QThread = _QtThreadStub

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from browser_core import updater
from browser_core.updater import (
    GitHubReleasesSource,
    ReleaseDownloader,
    ReleaseInfo,
    UpdateChecker,
    _asset_reachable,
    _exe_file_version,
    current_version,
)


@pytest.fixture()
def signals():
    """Distinct signal mocks — the mocked Signal() shares one object."""
    UpdateChecker.update_available = MagicMock()
    UpdateChecker.up_to_date = MagicMock()
    UpdateChecker.failed = MagicMock()
    ReleaseDownloader.progress = MagicMock()
    ReleaseDownloader.finished_ok = MagicMock()
    ReleaseDownloader.failed = MagicMock()
    ReleaseDownloader.cancelled = MagicMock()
    return UpdateChecker


class _FakeHTTPResponse:
    def __init__(self, payload: bytes, headers=None):
        self._payload = payload
        self.headers = headers or {}

    def read(self, n=-1):
        if n and n > 0:
            chunk, self._payload = self._payload[:n], self._payload[n:]
            return chunk
        data, self._payload = self._payload, b""
        return data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(code):
    return urllib.error.HTTPError("https://x", code, "e", {}, BytesIO(b""))


# ── fetch_latest error paths ─────────────────────────────────────────


def test_fetch_latest_http_error_propagates(monkeypatch) -> None:
    def _boom(req, timeout=None):
        raise _http_error(500)

    monkeypatch.setattr(updater.urllib.request, "urlopen", _boom)
    with pytest.raises(urllib.error.HTTPError):
        GitHubReleasesSource().fetch_latest()


def test_fetch_latest_malformed_json_raises(monkeypatch) -> None:
    monkeypatch.setattr(
        updater.urllib.request,
        "urlopen",
        lambda req, timeout=None: _FakeHTTPResponse(b"not json"),
    )
    with pytest.raises(json.JSONDecodeError):
        GitHubReleasesSource().fetch_latest()


def test_fetch_latest_missing_tag_uses_name(monkeypatch) -> None:
    payload = json.dumps(
        {"name": "LuckyD 9.3.0", "html_url": "https://x", "body": "", "assets": []}
    ).encode()
    monkeypatch.setattr(
        updater.urllib.request,
        "urlopen",
        lambda req, timeout=None: _FakeHTTPResponse(payload),
    )
    info = GitHubReleasesSource().fetch_latest()
    assert info.version == "LuckyD 9.3.0"  # falls back to name verbatim


def test_fetch_latest_sends_user_agent_and_accept(monkeypatch) -> None:
    seen = {}

    def _open(req, timeout=None):
        seen["headers"] = dict(req.headers)
        return _FakeHTTPResponse(b'{"tag_name": "v1.0.0", "assets": []}')

    monkeypatch.setattr(updater.urllib.request, "urlopen", _open)
    GitHubReleasesSource().fetch_latest()
    assert seen["headers"]["Accept"] == "application/vnd.github+json"
    assert "LuckyDBrowser" in seen["headers"]["User-agent"]


# ── fetch_latest_atom edge cases ─────────────────────────────────────


def test_atom_no_version_fires_no_head_probes(monkeypatch) -> None:
    feed = "<feed><entry><id>no-version-here</id><title>beta</title></entry></feed>"
    monkeypatch.setattr(
        updater.urllib.request,
        "urlopen",
        lambda req, timeout=None: _FakeHTTPResponse(feed.encode()),
    )
    calls = []
    monkeypatch.setattr(
        updater, "_asset_reachable", lambda url, timeout=10.0: calls.append(url) or True
    )
    info = GitHubReleasesSource().fetch_latest_atom()
    assert info.version == ""  # falls back to title text
    assert info.installer_url == ""
    assert calls == []  # no version → no derived-URL probing


def test_atom_title_version_fallback(monkeypatch) -> None:
    feed = (
        "<feed><entry><id>tag:github.com,2026:Repository/1/xyz</id>"
        "<title>LuckyD v9.4.1</title>"
        '<link href="https://github.com/x/y/releases/tag/v9.4.1"/>'
        "</entry></feed>"
    )
    monkeypatch.setattr(
        updater.urllib.request,
        "urlopen",
        lambda req, timeout=None: _FakeHTTPResponse(feed.encode()),
    )
    monkeypatch.setattr(updater, "_asset_reachable", lambda url, timeout=10.0: True)
    info = GitHubReleasesSource().fetch_latest_atom()
    assert info.version == "9.4.1"
    assert info.installer_url.endswith("LuckyDBrowserSetup-9.4.1.exe")
    assert info.notes == "LuckyD v9.4.1"  # empty content → title used as notes


def test_atom_no_reachable_candidate(monkeypatch) -> None:
    feed = (
        "<feed><entry><id>tag:github.com,2026:Repository/1/v9.5.0</id>"
        "<title>v9.5.0</title></entry></feed>"
    )
    monkeypatch.setattr(
        updater.urllib.request,
        "urlopen",
        lambda req, timeout=None: _FakeHTTPResponse(feed.encode()),
    )
    monkeypatch.setattr(updater, "_asset_reachable", lambda url, timeout=10.0: False)
    info = GitHubReleasesSource().fetch_latest_atom()
    assert info.version == "9.5.0"
    assert info.installer_url == ""
    assert info.url == "https://github.com/Dylanchess0320/LuckyD-Browser/releases/latest"


# ── _asset_reachable ─────────────────────────────────────────────────


def test_asset_reachable_true(monkeypatch) -> None:
    class _R:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda req, timeout=None: _R())
    assert _asset_reachable("https://x/setup.exe") is True


def test_asset_reachable_404_false(monkeypatch) -> None:
    def _boom(req, timeout=None):
        raise _http_error(404)

    monkeypatch.setattr(updater.urllib.request, "urlopen", _boom)
    assert _asset_reachable("https://x/setup.exe") is False


def test_asset_reachable_network_error_false(monkeypatch) -> None:
    def _boom(req, timeout=None):
        raise OSError("dns")

    monkeypatch.setattr(updater.urllib.request, "urlopen", _boom)
    assert _asset_reachable("https://x/setup.exe") is False


# ── UpdateChecker extra paths ────────────────────────────────────────


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


def test_checker_500_fails_with_code(signals) -> None:
    UpdateChecker(_Src(_http_error(500))).run()
    UpdateChecker.failed.emit.assert_called_once()
    assert "HTTP 500" in UpdateChecker.failed.emit.call_args[0][0]
    UpdateChecker.up_to_date.emit.assert_not_called()


def test_checker_newer_version_payload_shape(signals, monkeypatch) -> None:
    monkeypatch.setattr(updater, "CURRENT_VERSION", "9.0.0")
    info = ReleaseInfo(
        version="9.1.0",
        url="https://x",
        installer_url="https://x/setup.exe",
        installer_size=7,
        installer_sha256="ab" * 32,
        notes="n",
        name="9.1.0",
    )
    UpdateChecker(_Src(info)).run()
    payload = UpdateChecker.update_available.emit.call_args[0][0]
    assert payload == {
        "version": "9.1.0",
        "url": "https://x",
        "installer_url": "https://x/setup.exe",
        "installer_size": 7,
        "installer_sha256": "ab" * 32,
        "notes": "n",
        "name": "9.1.0",
    }


def test_checker_same_version_is_up_to_date(signals, monkeypatch) -> None:
    monkeypatch.setattr(updater, "CURRENT_VERSION", "9.1.0")
    UpdateChecker(_Src(ReleaseInfo(version="9.1.0", url="https://x"))).run()
    UpdateChecker.up_to_date.emit.assert_called_once()
    UpdateChecker.update_available.emit.assert_not_called()


def test_checker_403_with_working_atom(signals) -> None:
    src = _Src(_http_error(403))
    src.fetch_latest_atom = lambda: ReleaseInfo(version="10.99.0", url="https://x")
    UpdateChecker(src).run()
    UpdateChecker.update_available.emit.assert_called_once()


# ── ReleaseDownloader paths ──────────────────────────────────────────


def _download(monkeypatch, payload: bytes, **kw):
    dl = ReleaseDownloader("https://x/setup.exe", kw.pop("dest"), **kw)
    monkeypatch.setattr(
        updater.urllib.request,
        "urlopen",
        lambda req, timeout=None: _FakeHTTPResponse(
            payload, headers={"Content-Length": str(len(payload))}
        ),
    )
    return dl


def test_downloader_success(tmp_path, monkeypatch, signals) -> None:
    dest = tmp_path / "setup.exe"
    payload = b"x" * 100
    dl = _download(monkeypatch, payload, dest=dest)
    dl.isInterruptionRequested = lambda: False
    dl.run()
    ReleaseDownloader.finished_ok.emit.assert_called_once_with(str(dest))
    assert dest.read_bytes() == payload
    assert not (tmp_path / "setup.exe.part").exists()  # .part renamed away


def test_downloader_size_mismatch(tmp_path, monkeypatch, signals) -> None:
    dest = tmp_path / "setup.exe"
    dl = _download(monkeypatch, b"x" * 10, dest=dest, expected_size=999)
    dl.isInterruptionRequested = lambda: False
    dl.run()
    ReleaseDownloader.failed.emit.assert_called_once()
    assert "size did not match" in ReleaseDownloader.failed.emit.call_args[0][0]
    assert not dest.exists() and not (tmp_path / "setup.exe.part").exists()


def test_downloader_sha256_mismatch(tmp_path, monkeypatch, signals) -> None:
    dest = tmp_path / "setup.exe"
    dl = _download(monkeypatch, b"data", dest=dest, expected_sha256="ab" * 32)
    dl.isInterruptionRequested = lambda: False
    dl.run()
    assert "SHA-256 did not match" in ReleaseDownloader.failed.emit.call_args[0][0]
    assert not dest.exists()


def test_downloader_sha256_ok(tmp_path, monkeypatch, signals) -> None:
    dest = tmp_path / "setup.exe"
    payload = b"good bytes"
    digest = hashlib.sha256(payload).hexdigest()
    dl = _download(monkeypatch, payload, dest=dest, expected_sha256=digest.upper())
    dl.isInterruptionRequested = lambda: False
    dl.run()
    ReleaseDownloader.finished_ok.emit.assert_called_once_with(str(dest))


def test_downloader_cancel_mid_stream(tmp_path, monkeypatch, signals) -> None:
    dest = tmp_path / "setup.exe"
    dl = _download(monkeypatch, b"x" * 100, dest=dest)
    dl.isInterruptionRequested = lambda: True  # cancel before first chunk
    dl.run()
    ReleaseDownloader.cancelled.emit.assert_called_once()
    ReleaseDownloader.finished_ok.emit.assert_not_called()
    assert not dest.exists()


def test_downloader_network_error(tmp_path, monkeypatch, signals) -> None:
    dest = tmp_path / "setup.exe"
    dl = ReleaseDownloader("https://x/setup.exe", dest)

    def _boom(req, timeout=None):
        raise OSError("conn reset")

    monkeypatch.setattr(updater.urllib.request, "urlopen", _boom)
    dl.run()
    assert "conn reset" in ReleaseDownloader.failed.emit.call_args[0][0]


def test_downloader_cancel_method_requests_interruption(tmp_path) -> None:
    dl = ReleaseDownloader("https://x/setup.exe", tmp_path / "s.exe")
    assert dl.isInterruptionRequested() is False
    dl.cancel()
    assert dl.isInterruptionRequested() is True


def test_downloader_stores_expected_sha_lowered(tmp_path) -> None:
    dl = ReleaseDownloader("https://x/s.exe", tmp_path / "s.exe", expected_sha256="AB" * 32)
    assert dl.expected_sha256 == "ab" * 32


# ── version plumbing ─────────────────────────────────────────────────


def test_exe_file_version_non_windows_is_empty() -> None:
    assert _exe_file_version() == ""  # Linux CI: early return, no ctypes


def test_current_version_prefers_set_value(monkeypatch) -> None:
    monkeypatch.setattr(updater, "CURRENT_VERSION", "9.0.0")
    assert current_version() == "9.0.0"


def test_current_version_falls_back_to_package(monkeypatch) -> None:
    import browser

    monkeypatch.setattr(updater, "CURRENT_VERSION", "")
    assert current_version() == browser.__version__
    assert browser.__version__ == updater.CURRENT_VERSION  # cached back
