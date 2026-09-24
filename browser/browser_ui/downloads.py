"""Downloads dock: accepts download requests and tracks their progress."""

from __future__ import annotations

import contextlib
import time
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWebEngineCore import QWebEngineDownloadRequest
from PySide6.QtWidgets import (
    QDockWidget,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


def _fmt_size(num: int | float) -> str:
    size = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


_FINISH_ICONS = {"completed": "✅", "cancelled": "🚫", "interrupted": "⚠"}


class DownloadsDock(QDockWidget):
    def __init__(self, settings, parent=None, storage=None, retry_handler=None):
        super().__init__("Downloads", parent)
        self.setObjectName("downloads_dock")  # required for saveState()
        self._settings = settings
        #: Persisted download history (None in bare/legacy constructions).
        self._storage = storage
        #: Callable(url) re-issuing a download; wired to the current tab.
        self._retry_handler = retry_handler
        self.setAllowedAreas(
            Qt.DockWidgetArea.BottomDockWidgetArea
            | Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )

        body = QWidget(self)
        layout = QVBoxLayout(body)
        self.list = QListWidget(body)

        btn_row = QHBoxLayout()
        open_btn = QPushButton("Open Downloads Folder", body)
        open_btn.clicked.connect(self._open_folder)
        clear_btn = QPushButton("Clear Completed", body)
        clear_btn.clicked.connect(self._clear_completed)
        btn_row.addWidget(open_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(clear_btn)

        layout.addWidget(self.list)
        layout.addLayout(btn_row)
        self.setWidget(body)

        self.list.itemDoubleClicked.connect(self._open_item)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self._paths: dict[int, Path] = {}
        self._downloads: dict[int, QWebEngineDownloadRequest] = {}
        self._cancel_btns: dict[int, QWidget] = {}
        self._marks: dict[int, tuple[int, float]] = {}  # did → (bytes, monotonic) for rate
        self._urls: dict[int, str] = {}  # did → source URL (for retry)
        self._load_history()

    # ── entry point (called by profile.downloadRequested) ────────────

    def handle_download(self, download: QWebEngineDownloadRequest) -> None:
        target_dir = self._settings.get("download_dir") or str(Path.home() / "Downloads")
        download.setDownloadDirectory(target_dir)
        download.setDownloadFileName(download.suggestedFileName())

        did = download.id()
        self._downloads[did] = download
        self._marks[did] = (0, time.monotonic())
        try:
            self._urls[did] = download.url().toString()
        except Exception:
            self._urls[did] = ""

        item = QListWidgetItem(f"⬇ {download.suggestedFileName()} — starting…")
        item.setData(Qt.ItemDataRole.UserRole, did)
        self.list.insertItem(0, item)

        # Per-item controls: pause/resume + cancel.
        controls = QWidget(self.list)
        row = QHBoxLayout(controls)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        pause_btn = QPushButton("⏸", controls)
        pause_btn.setFixedSize(22, 22)
        pause_btn.setToolTip("Pause / resume")
        pause_btn.setObjectName("dl_pause")  # styled in theme.py
        pause_btn.clicked.connect(lambda _=False, d=download, b=pause_btn: self._toggle_pause(d, b))
        row.addWidget(pause_btn)
        cancel_btn = QPushButton("✕", controls)
        cancel_btn.setFixedSize(22, 22)
        cancel_btn.setToolTip("Cancel download")
        cancel_btn.setObjectName("dl_cancel")  # styled in theme.py
        cancel_btn.clicked.connect(lambda _=False, d=download: d.cancel())
        row.addWidget(cancel_btn)
        self._cancel_btns[did] = controls
        self.list.setItemWidget(item, controls)

        self._paths[did] = Path(target_dir) / download.downloadFileName()

        download.receivedBytesChanged.connect(lambda d=download, i=item: self._on_progress(d, i))
        download.stateChanged.connect(lambda state, d=download, i=item: self._on_state(d, i, state))
        download.accept()
        self.show()
        self.raise_()

    def _toggle_pause(self, download, button) -> None:
        """Pause/resume a live download (Qt WebEngine supports both)."""
        if download.isPaused():
            download.resume()
            button.setText("⏸")
        else:
            download.pause()
            button.setText("▶")

    # ── progress / state ─────────────────────────────────────────────

    def _on_progress(self, download, item) -> None:
        received = download.receivedBytes()
        total = download.totalBytes()
        did = download.id()
        now = time.monotonic()
        prev = self._marks.get(did)
        self._marks[did] = (received, now)
        rate = (received - prev[0]) / max(now - prev[1], 0.05) if prev else 0.0
        speed = f" · {_fmt_size(rate)}/s" if rate > 1 else ""
        if total > 0:
            pct = int(received * 100 / total)
            eta = ""
            if rate > 1 and received < total:
                left = int((total - received) / rate)
                eta = f" · {left // 60}m{left % 60:02d}s" if left >= 60 else f" · {left}s"
            item.setText(
                f"⬇ {download.downloadFileName()} — {pct}% "
                f"({_fmt_size(received)} / {_fmt_size(total)}){speed}{eta}"
            )
        else:
            item.setText(f"⬇ {download.downloadFileName()} — {_fmt_size(received)}{speed}")

    def _on_state(self, download, item, state) -> None:
        states = QWebEngineDownloadRequest.DownloadState
        did = download.id()
        # Remove controls when done
        if state in (
            states.DownloadCompleted,
            states.DownloadCancelled,
            states.DownloadInterrupted,
        ):
            self._cancel_btns.pop(did, None)
            self._marks.pop(did, None)
            self.list.setItemWidget(item, None)

        finished: str | None = None
        if state == states.DownloadCompleted:
            item.setText(f"✅ {download.downloadFileName()} — done (double-click to open)")
            finished = "completed"
        elif state == states.DownloadCancelled:
            item.setText(f"🚫 {download.downloadFileName()} — cancelled")
            finished = "cancelled"
        elif state == states.DownloadInterrupted:
            item.setText(
                f"⚠ {download.downloadFileName()} — interrupted: {download.interruptReasonString()}"
            )
            finished = "interrupted"
            # Qt offers no restart API — offer a retry that re-issues the
            # download through the current tab instead of a dead end.
            if self._retry_handler is not None:
                retry_btn = QPushButton("↻ Retry", self.list)
                retry_btn.setToolTip("Download again from the source URL")
                retry_btn.clicked.connect(lambda _=False, d=did: self._retry_download(d))
                self.list.setItemWidget(item, retry_btn)
        if finished is not None and self._storage is not None:
            # History persistence must never break the UI.
            with contextlib.suppress(Exception):
                self._storage.record_download(
                    self._urls.get(did, ""), download.downloadFileName(), finished
                )

    def _retry_download(self, did: int) -> None:
        """Re-issue an interrupted download through the retry handler."""
        url = self._urls.get(did, "")
        if url and self._retry_handler is not None:
            with contextlib.suppress(Exception):
                self._retry_handler(url)

    def _load_history(self) -> None:
        """Show persisted downloads from previous sessions (negative dids)."""
        if self._storage is None:
            return
        try:
            rows = self._storage.download_history()
        except Exception:
            return
        for i, (url, filename, state, _ts) in enumerate(rows):
            icon = _FINISH_ICONS.get(state, "•")
            item = QListWidgetItem(f"{icon} {filename} — {state} (previous session)")
            did = -(i + 1)
            item.setData(Qt.ItemDataRole.UserRole, did)
            self._urls[did] = url or ""
            self.list.addItem(item)

    def _clear_completed(self) -> None:
        """Remove completed/cancelled/interrupted downloads from the list."""
        states = QWebEngineDownloadRequest.DownloadState
        finished = {states.DownloadCompleted, states.DownloadCancelled, states.DownloadInterrupted}
        for i in range(self.list.count() - 1, -1, -1):
            item = self.list.item(i)
            did = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(did, int) and did < 0:
                self._urls.pop(did, None)  # persisted history row
                self.list.takeItem(i)
                continue
            dl = self._downloads.get(did)
            if dl is not None and dl.state() in finished:
                self._downloads.pop(did, None)
                self._paths.pop(did, None)
                self._marks.pop(did, None)
                self._urls.pop(did, None)
                self.list.takeItem(i)
        if self._storage is not None:
            with contextlib.suppress(Exception):
                self._storage.clear_download_history()

    def _context_menu(self, pos) -> None:
        """Right-click menu on download items."""
        item = self.list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        menu.addAction("Open File", lambda: self._open_item(item))
        menu.addSeparator()
        menu.addAction("Remove from List", lambda: self._remove_item(item))
        menu.exec(self.list.mapToGlobal(pos))

    def _remove_item(self, item) -> None:
        did = item.data(Qt.ItemDataRole.UserRole)
        self._downloads.pop(did, None)
        self._paths.pop(did, None)
        self._cancel_btns.pop(did, None)
        self._marks.pop(did, None)
        self._urls.pop(did, None)
        self.list.takeItem(self.list.row(item))

    # ── helpers ──────────────────────────────────────────────────────

    def _target_dir(self) -> str:
        return self._settings.get("download_dir") or str(Path.home() / "Downloads")

    def _open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(self._target_dir()))

    def _open_item(self, item) -> None:
        path = self._paths.get(item.data(Qt.ItemDataRole.UserRole))
        if path is not None and path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:
            self._open_folder()
