"""Coverage push: browser_core/updater.py remaining paths.

Covers: _exe_file_version() frozen-Windows branch (faked ctypes.windll),
the module-level `from browser import __version__` fallback, current_version()
exe-metadata fallback, _atom_fallback() raising, downloader cancel during the
rename-retry loop, and downloader rename failures exhausting retries.
"""

from __future__ import annotations

import ctypes
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
    ReleaseDownloader,
    UpdateChecker,
    _exe_file_version,
    current_version,
)


@pytest.fixture()
def signals():
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


# ── _exe_file_version: frozen-Windows branch ──────────────────────────


class _FakeVersionApi:
    """Stand-in for ctypes.windll.version (Windows-only in reality)."""

    def __init__(
        self,
        size=64,
        info_ok=True,
        query_ok=True,
        second_query_ok=True,
        vlen=4,
        file_version="9.9.9",
    ):
        self._size = size
        self._info_ok = info_ok
        self._query_ok = query_ok
        self._second_query_ok = second_query_ok
        self._vlen = vlen
        # Keep the pointed-to buffers alive for the whole test.
        self._keepalive = []
        buf = ctypes.create_unicode_buffer(file_version)
        self._keepalive.append(buf)
        self._file_version_addr = ctypes.addressof(buf)
        arr = (ctypes.c_uint16 * 2)(0x0409, 0x04B0)
        self._keepalive.append(arr)
        self._translation_addr = ctypes.addressof(arr)

    def GetFileVersionInfoSizeW(self, path, _unused):  # noqa: N802 — mirrors Win32 API
        if isinstance(self._size, Exception):
            raise self._size
        return self._size

    def GetFileVersionInfoW(self, path, _zero, size, data):  # noqa: N802 — mirrors Win32 API
        return self._info_ok

    def VerQueryValueW(self, data, sub, p_val, p_vlen):  # noqa: N802 — mirrors Win32 API
        ok = self._query_ok if sub == r"\VarFileInfo\Translation" else self._second_query_ok
        if not ok:
            return False
        p_vlen._obj.value = self._vlen
        p_val._obj.value = (
            self._translation_addr
            if sub == r"\VarFileInfo\Translation"
            else self._file_version_addr
        )
        return True


class _FakeWindll:
    def __init__(self, version):
        self.version = version


@pytest.fixture()
def win32_frozen(monkeypatch):
    """Pretend to be a frozen Windows build for _exe_file_version()."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    return monkeypatch


def _install_windll(monkeypatch, **kw):
    fake = _FakeWindll(_FakeVersionApi(**kw))
    monkeypatch.setattr(ctypes, "windll", fake, raising=False)


def test_exe_file_version_windows_happy_path(win32_frozen) -> None:
    _install_windll(win32_frozen)
    assert _exe_file_version() == "9.9.9"


def test_exe_file_version_windows_no_size(win32_frozen) -> None:
    _install_windll(win32_frozen, size=0)
    assert _exe_file_version() == ""


def test_exe_file_version_windows_info_fails(win32_frozen) -> None:
    _install_windll(win32_frozen, info_ok=False)
    assert _exe_file_version() == ""


def test_exe_file_version_windows_translation_query_fails(win32_frozen) -> None:
    _install_windll(win32_frozen, query_ok=False)
    assert _exe_file_version() == ""


def test_exe_file_version_windows_short_translation(win32_frozen) -> None:
    _install_windll(win32_frozen, vlen=2)
    assert _exe_file_version() == ""


def test_exe_file_version_windows_file_version_query_fails(win32_frozen) -> None:
    _install_windll(win32_frozen, second_query_ok=False)
    assert _exe_file_version() == ""


def test_exe_file_version_windows_api_raises(win32_frozen) -> None:
    _install_windll(win32_frozen, size=RuntimeError("ctypes boom"))
    assert _exe_file_version() == ""


def test_exe_file_version_not_frozen_returns_empty(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    # sys.frozen absent -> early return before any windll access
    assert _exe_file_version() == ""


# ── current_version() / module import fallbacks ──────────────────────


def test_current_version_falls_back_to_exe_metadata(monkeypatch) -> None:
    import types

    monkeypatch.setitem(sys.modules, "browser", types.ModuleType("browser"))
    monkeypatch.setattr(updater, "CURRENT_VERSION", "1.0.0")
    monkeypatch.setattr(updater, "_exe_file_version", lambda: "9.9.9")
    assert current_version() == "9.9.9"
    assert updater.CURRENT_VERSION == "9.9.9"


def test_current_version_exe_empty_keeps_default(monkeypatch) -> None:
    import types

    monkeypatch.setitem(sys.modules, "browser", types.ModuleType("browser"))
    monkeypatch.setattr(updater, "CURRENT_VERSION", "1.0.0")
    monkeypatch.setattr(updater, "_exe_file_version", lambda: "")
    assert current_version() == "1.0.0"


def test_module_import_without_package_version_falls_back(monkeypatch) -> None:
    """`from browser import __version__` failing at import time -> '1.0.0'."""
    import importlib
    import types

    monkeypatch.setitem(sys.modules, "browser", types.ModuleType("browser"))
    importlib.reload(updater)
    try:
        assert updater.CURRENT_VERSION == "1.0.0"
    finally:
        monkeypatch.undo()
        importlib.reload(updater)
    import browser

    assert browser.__version__ == updater.CURRENT_VERSION


# ── UpdateChecker._atom_fallback raising ─────────────────────────────


def test_atom_fallback_exception_returns_none() -> None:
    class _BoomSrc:
        def fetch_latest_atom(self):
            raise RuntimeError("atom down")

    checker = UpdateChecker()
    checker.source = _BoomSrc()
    assert checker._atom_fallback() is None


def test_checker_429_uses_atom_fallback(signals) -> None:
    from browser_core.updater import ReleaseInfo

    err = urllib.error.HTTPError("https://x", 429, "rl", {}, BytesIO(b""))

    class _Src:
        def fetch_latest(self):
            raise err

        def fetch_latest_atom(self):
            return ReleaseInfo(version="10.0.0", url="https://x")

    UpdateChecker(_Src()).run()
    UpdateChecker.update_available.emit.assert_called_once()


# ── ReleaseDownloader rename-loop paths ─────────────────────────────


def test_downloader_cancel_during_rename(tmp_path, monkeypatch, signals) -> None:
    dest = tmp_path / "setup.exe"
    dl = _download(monkeypatch, b"x" * 10, dest=dest)
    # Download loop sees no interruption (2 checks), rename loop sees it.
    _interruptions = iter([False, False, True])
    dl.isInterruptionRequested = lambda: next(_interruptions, True)
    dl.run()
    ReleaseDownloader.cancelled.emit.assert_called_once()
    ReleaseDownloader.finished_ok.emit.assert_not_called()
    assert not dest.exists()


def test_downloader_rename_fails_all_retries(tmp_path, monkeypatch, signals) -> None:
    dest = tmp_path / "setup.exe"
    dl = _download(monkeypatch, b"x" * 10, dest=dest)
    dl.isInterruptionRequested = lambda: False
    monkeypatch.setattr(updater.time, "sleep", lambda s: None)  # skip backoff

    def _locked(self, target):
        raise OSError("file locked by AV scan")

    monkeypatch.setattr(Path, "replace", _locked)
    dl.run()
    ReleaseDownloader.failed.emit.assert_called_once()
    assert "file locked by AV scan" in ReleaseDownloader.failed.emit.call_args[0][0]
    assert not dest.exists()
    assert not (tmp_path / "setup.exe.part").exists()
