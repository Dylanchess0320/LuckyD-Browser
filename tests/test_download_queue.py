"""P1: download retry + persisted queue.

Interrupted downloads used to dead-end with no recovery; finished
downloads vanished on restart. Interrupts now offer a Retry button that
re-issues the URL through the current tab, and every finished download
is persisted to the browser database and restored as history.
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


class _DockStub:
    """Real QDockWidget stand-in; unknown attrs become mocks."""

    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        return MagicMock(name=f"dock.{name}")


sys.modules["PySide6.QtWidgets"].QDockWidget = _DockStub

from browser_core.storage import Storage
from browser_ui.downloads import DownloadsDock


def _states():
    return sys.modules["PySide6.QtWebEngineCore"].QWebEngineDownloadRequest.DownloadState


def _download(did=7, url="https://example.com/file.zip", name="file.zip"):
    dl = MagicMock()
    dl.id.return_value = did
    dl.suggestedFileName.return_value = name
    dl.downloadFileName.return_value = name
    dl.url.return_value.toString.return_value = url
    dl.interruptReasonString.return_value = "network failed"
    return dl


def _dock(tmp_path, **kwargs):
    settings = MagicMock()
    settings.get.return_value = str(tmp_path)
    kwargs.setdefault("storage", Storage(tmp_path / "browser.db"))
    return DownloadsDock(settings, **kwargs)


class TestPersistedQueue:
    def test_finished_download_recorded(self, tmp_path):
        dock = _dock(tmp_path)
        dock.handle_download(_download())
        dock._on_state(dock._downloads[7], MagicMock(), _states().DownloadCompleted)
        rows = dock._storage.download_history()
        assert len(rows) == 1
        assert rows[0][:3] == ("https://example.com/file.zip", "file.zip", "completed")

    def test_history_restored_on_construction(self, tmp_path):
        store = Storage(tmp_path / "browser.db")
        store.record_download("https://example.com/old.zip", "old.zip", "completed")
        item_cls = sys.modules["PySide6.QtWidgets"].QListWidgetItem
        item_cls.reset_mock()
        _dock(tmp_path, storage=store)
        texts = [c.args[0] for c in item_cls.call_args_list if c.args]
        assert any("old.zip" in t and "previous session" in t for t in texts)

    def test_clear_completed_wipes_history(self, tmp_path):
        store = Storage(tmp_path / "browser.db")
        store.record_download("https://example.com/old.zip", "old.zip", "completed")
        dock = _dock(tmp_path, storage=store)
        dock.list.count.return_value = 0  # no live rows; history rows removed below
        dock._clear_completed()
        assert store.download_history() == []


class TestRetry:
    def test_interrupt_offers_retry_and_reruns_url(self, tmp_path):
        seen = []
        dock = _dock(tmp_path, retry_handler=seen.append)
        dock.handle_download(_download())
        states = _states()
        dock._on_state(dock._downloads[7], MagicMock(), states.DownloadInterrupted)
        btn_cls = sys.modules["PySide6.QtWidgets"].QPushButton
        assert any("Retry" in str(c.args) for c in btn_cls.call_args_list)
        dock._retry_download(7)
        assert seen == ["https://example.com/file.zip"]

    def test_no_retry_without_handler(self, tmp_path):
        dock = _dock(tmp_path, retry_handler=None)
        dock.handle_download(_download())
        dock.list.setItemWidget.reset_mock()
        dock._on_state(dock._downloads[7], MagicMock(), _states().DownloadInterrupted)
        for call in dock.list.setItemWidget.call_args_list:
            assert call.args[1] is None  # only the controls-removal call

    def test_retry_without_url_is_noop(self, tmp_path):
        seen = []
        dock = _dock(tmp_path, retry_handler=seen.append)
        dock._retry_download(999)
        assert seen == []
