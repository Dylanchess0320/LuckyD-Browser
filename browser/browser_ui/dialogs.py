"""History, Bookmarks, and Settings dialogs."""

from __future__ import annotations

import html
import time
from pathlib import Path

from browser_core.settings import SEARCH_ENGINES
from browser_ui.theme import THEMES
from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)


class HistoryDialog(QDialog):
    def __init__(self, storage, open_url, parent=None):
        super().__init__(parent)
        self.setWindowTitle("History")
        self.resize(680, 500)
        self._storage = storage
        self._open_url = open_url

        layout = QVBoxLayout(self)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search history…")
        self.search.textChanged.connect(self._reload)
        self.list = QListWidget(self)
        self.list.itemDoubleClicked.connect(self._open)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        clear = QPushButton("Clear All History", self)
        clear.clicked.connect(self._clear)
        buttons.addButton(clear, QDialogButtonBox.ButtonRole.DestructiveRole)
        buttons.rejected.connect(self.reject)

        layout.addWidget(self.search)
        layout.addWidget(self.list)
        layout.addWidget(buttons)
        self._reload()

    def _reload(self) -> None:
        query = self.search.text().strip()
        rows = self._storage.search_history(query) if query else self._storage.recent(500)
        self.list.clear()
        for url, title, ts in rows:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
            item = QListWidgetItem(f"{when}   {title or url}\n{url}")
            item.setData(Qt.ItemDataRole.UserRole, url)
            self.list.addItem(item)

    def _open(self, item) -> None:
        self._open_url(QUrl(item.data(Qt.ItemDataRole.UserRole)))
        self.accept()

    def _clear(self) -> None:
        answer = QMessageBox.question(self, "Clear History", "Delete all browsing history?")
        if answer == QMessageBox.StandardButton.Yes:
            self._storage.clear_history()
            self._reload()

    def _context_menu(self, pos) -> None:
        """Right-click on a history item to delete it."""
        item = self.list.itemAt(pos)
        if item is None:
            return
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        url = item.data(Qt.ItemDataRole.UserRole)
        menu.addAction("Open in New Tab", lambda: self._open_url(QUrl(url)))
        menu.addSeparator()
        menu.addAction("Delete Entry", lambda: self._delete_item(item))
        menu.exec(self.list.mapToGlobal(pos))

    def _delete_item(self, item) -> None:
        """Delete a single history entry."""
        url = item.data(Qt.ItemDataRole.UserRole)
        if url:
            self._storage.delete_history_entry(url)
            self._reload()


class BookmarksDialog(QDialog):
    def __init__(self, storage, open_url, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Bookmarks")
        self.resize(680, 500)
        self._storage = storage
        self._open_url = open_url

        layout = QVBoxLayout(self)
        self.list = QListWidget(self)
        self.list.itemDoubleClicked.connect(self._open)

        row = QHBoxLayout()
        import_btn = QPushButton("Import from Chrome/Edge HTML…", self)
        import_btn.clicked.connect(self._import)
        export_btn = QPushButton("Export to HTML…", self)
        export_btn.clicked.connect(self._export)
        delete_btn = QPushButton("Delete Selected", self)
        delete_btn.clicked.connect(self._delete_selected)
        row.addWidget(import_btn)
        row.addWidget(export_btn)
        row.addWidget(delete_btn)
        row.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        buttons.rejected.connect(self.reject)

        layout.addLayout(row)
        layout.addWidget(self.list)
        layout.addWidget(buttons)
        self._reload()

    def _reload(self) -> None:
        self.list.clear()
        for url, title, folder, _created in self._storage.bookmarks():
            prefix = f"[{folder}] " if folder else ""
            item = QListWidgetItem(f"⭐ {prefix}{title or url}\n{url}")
            item.setData(Qt.ItemDataRole.UserRole, url)
            self.list.addItem(item)

    def _open(self, item) -> None:
        self._open_url(QUrl(item.data(Qt.ItemDataRole.UserRole)))
        self.accept()

    def _delete_selected(self) -> None:
        for item in self.list.selectedItems():
            self._storage.remove_bookmark(item.data(Qt.ItemDataRole.UserRole))
        self._reload()

    def _export(self) -> None:
        """Export bookmarks to Netscape bookmark format HTML."""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Bookmarks",
            "bookmarks.html",
            "Bookmark HTML files (*.html *.htm);;All files (*.*)",
        )
        if not path:
            return
        bookmarks = self._storage.bookmarks()
        lines = [
            "<!DOCTYPE NETSCAPE-Bookmark-file-1>",
            "<META HTTP-EQUIV='Content-Type' CONTENT='text/html; charset=UTF-8'>",
            "<TITLE>Bookmarks</TITLE>",
            "<H1>Bookmarks</H1>",
            "<DL><p>",
        ]
        for url, title, folder, _created in bookmarks:
            label = html.escape(title or url)
            folder_attr = f' FOLDER="{html.escape(folder)}"' if folder else ""
            lines.append(f'    <DT><A HREF="{html.escape(url)}"{folder_attr}>{label}</A>')
        lines.append("</DL><p>")
        try:
            Path(path).write_text("\n".join(lines), encoding="utf-8")
            QMessageBox.information(
                self, "Export Bookmarks", f"Exported {len(bookmarks)} bookmarks."
            )
        except Exception as exc:
            QMessageBox.warning(self, "Export Error", f"Failed to export: {exc}")

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Bookmarks",
            "",
            "Bookmark HTML files (*.html *.htm);;All files (*.*)",
        )
        if not path:
            return
        count = self._storage.import_from_html(path)
        QMessageBox.information(self, "Import Bookmarks", f"Imported {count} bookmarks.")
        self._reload()


class SettingsDialog(QDialog):
    def __init__(self, settings, profile, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(480)
        self._settings = settings
        self._profile = profile

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.homepage = QLineEdit(settings.get("homepage", "newtab"))
        self.homepage.setPlaceholderText("newtab  or  https://example.com")
        form.addRow("Homepage", self.homepage)

        self.startup_box = QComboBox(self)
        self.startup_box.addItem("Continue where you left off", "restore")
        self.startup_box.addItem("Open the New Tab page", "newtab")
        self.startup_box.setToolTip(
            '"Continue where you left off" reopens the tabs (and windows) you had\n'
            "open when you last closed the browser. Incognito is never restored."
        )
        mode = str(settings.get("startup_mode", "restore"))
        self.startup_box.setCurrentIndex(1 if mode == "newtab" else 0)
        form.addRow("On startup", self.startup_box)

        self.engine = QComboBox(self)
        self.engine.addItems(list(SEARCH_ENGINES.keys()))
        self.engine.setCurrentText(settings.get("search_engine", "Google"))
        form.addRow("Search engine", self.engine)

        self.adblock = QCheckBox("Block ads and trackers", self)
        self.adblock.setChecked(bool(settings.get("adblock_enabled", True)))
        form.addRow(self.adblock)

        self.autostart = QCheckBox(
            "Start the coding-agent backend (luckyd-code.exe) on launch", self
        )
        self.autostart.setChecked(bool(settings.get("harness_autostart", True)))
        form.addRow(self.autostart)

        self.dash = QCheckBox("Live dashboard on new tabs (needs the Browser Control API)", self)
        self.dash.setChecked(bool(settings.get("dashboard_newtab", True)))
        form.addRow(self.dash)

        self.assistant_startup = QCheckBox(
            "Remember my setup (reopen the AI assistant on startup)", self
        )
        self.assistant_startup.setToolTip(
            "Persist your workspace: the assistant panel reopens on launch, and\n"
            "your chosen AI provider/model and harness mode are already applied.\n"
            "Turn off for a clean browser with the assistant hidden until you need it."
        )
        self.assistant_startup.setChecked(bool(settings.get("assistant_visible_startup", True)))
        form.addRow(self.assistant_startup)

        self.bm_bar = QCheckBox("Show the bookmarks bar (Ctrl+Shift+B toggles it)", self)
        self.bm_bar.setChecked(bool(settings.get("bookmark_bar_visible", True)))
        form.addRow(self.bm_bar)

        dir_row = QHBoxLayout()
        self.dl_dir = QLineEdit(settings.get("download_dir", ""))
        self.dl_dir.setPlaceholderText("Default: system Downloads folder")
        browse = QPushButton("Browse…", self)
        browse.clicked.connect(self._browse_dir)
        dir_row.addWidget(self.dl_dir)
        dir_row.addWidget(browse)
        form.addRow("Download folder", dir_row)

        # Zoom: global default + per-site memory
        self.zoom_box = QComboBox(self)
        self._zoom_values = [0.5, 0.67, 0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0]
        for value in self._zoom_values:
            self.zoom_box.addItem(f"{round(value * 100)}%", value)
        try:
            current_zoom = float(settings.get("zoom_factor", 1.0) or 1.0)
        except (TypeError, ValueError):
            current_zoom = 1.0
        nearest = min(
            range(len(self._zoom_values)), key=lambda i: abs(self._zoom_values[i] - current_zoom)
        )
        self.zoom_box.setCurrentIndex(nearest)
        form.addRow("Default zoom", self.zoom_box)

        self.zoom_memory = QCheckBox("Remember zoom level per website", self)
        self.zoom_memory.setToolTip(
            "Zoom a page with Ctrl +/-/0 or Ctrl+Scroll and LuckyD reopens that\n"
            "site at your level next time. Resetting to 100% forgets the site."
        )
        self.zoom_memory.setChecked(bool(settings.get("zoom_remember", True)))
        form.addRow(self.zoom_memory)

        # Theme selection
        self.theme_box = QComboBox(self)
        self.theme_box.addItems([THEMES[k]["label"] for k in THEMES])
        current_theme = settings.get("theme", "neon")
        if current_theme in THEMES:
            self.theme_box.setCurrentText(THEMES[current_theme]["label"])
        form.addRow("Theme", self.theme_box)

        # Updates
        self.auto_update = QCheckBox("Automatically check for updates", self)
        self.auto_update.setToolTip("On startup, silently check GitHub for a newer release.")
        self.auto_update.setChecked(bool(settings.get("update_auto_check", True)))
        form.addRow(self.auto_update)

        layout.addLayout(form)

        privacy = QHBoxLayout()
        cache_btn = QPushButton("Clear Cache", self)
        cache_btn.clicked.connect(self._clear_cache)
        cookies_btn = QPushButton("Clear Cookies", self)
        cookies_btn.clicked.connect(self._clear_cookies)
        privacy.addWidget(cache_btn)
        privacy.addWidget(cookies_btn)
        privacy.addStretch(1)
        layout.addLayout(privacy)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose Download Folder")
        if path:
            self.dl_dir.setText(path)

    def _clear_cache(self) -> None:
        self._profile.clearHttpCache()
        QMessageBox.information(self, "Cache", "HTTP cache cleared.")

    def _clear_cookies(self) -> None:
        self._profile.cookieStore().deleteAllCookies()
        QMessageBox.information(self, "Cookies", "All cookies deleted.")

    def accept(self) -> None:
        self._settings.set("homepage", self.homepage.text().strip() or "newtab")
        self._settings.set("startup_mode", self.startup_box.currentData() or "restore")
        self._settings.set("search_engine", self.engine.currentText())
        self._settings.set("adblock_enabled", self.adblock.isChecked())
        self._settings.set("harness_autostart", self.autostart.isChecked())
        self._settings.set("dashboard_newtab", self.dash.isChecked())
        self._settings.set("assistant_visible_startup", self.assistant_startup.isChecked())
        self._settings.set("bookmark_bar_visible", self.bm_bar.isChecked())
        self._settings.set("download_dir", self.dl_dir.text().strip())
        self._settings.set("zoom_factor", float(self.zoom_box.currentData() or 1.0))
        self._settings.set("zoom_remember", self.zoom_memory.isChecked())
        self._settings.set("update_auto_check", self.auto_update.isChecked())
        # Theme: find the key for the selected label
        selected_label = self.theme_box.currentText()
        for k, v in THEMES.items():
            if v["label"] == selected_label:
                self._settings.set("theme", k)
                break
        super().accept()


class ScriptsDialog(QDialog):
    """Userscript manager: enable/disable, rescan, open the scripts folder."""

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Extensions (userscripts)")
        self.resize(560, 420)
        self._engine = engine

        layout = QVBoxLayout(self)
        hint = QLabel(
            "Drop .user.js files into the user scripts folder, then Rescan. "
            "Built-ins ship with the browser.",
            self,
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        layout.addWidget(hint)
        self.list = QListWidget(self)
        self.list.itemChanged.connect(self._toggled)
        layout.addWidget(self.list)

        row = QHBoxLayout()
        open_btn = QPushButton("Open user scripts folder", self)
        open_btn.clicked.connect(self._open_folder)
        rescan_btn = QPushButton("Rescan", self)
        rescan_btn.clicked.connect(self._reload)
        close_btn = QPushButton("Close", self)
        close_btn.clicked.connect(self.accept)
        row.addWidget(open_btn)
        row.addWidget(rescan_btn)
        row.addStretch(1)
        row.addWidget(close_btn)
        layout.addLayout(row)
        self._reload()

    def _reload(self) -> None:
        self._engine.rescan()
        self.list.blockSignals(True)
        self.list.clear()
        for script in self._engine.scripts():
            tag = "built-in" if script.builtin else "user"
            item = QListWidgetItem(f"{script.name}  ({tag})")
            item.setToolTip(
                f"{script.path}\nmatches: {', '.join(script.matches)}\nrun at: {script.run_at}"
            )
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if self._engine.is_enabled(script)
                else Qt.CheckState.Unchecked
            )
            item.setData(Qt.ItemDataRole.UserRole, script)
            self.list.addItem(item)
        self.list.blockSignals(False)

    def _toggled(self, item) -> None:
        script = item.data(Qt.ItemDataRole.UserRole)
        if script is not None:
            self._engine.set_enabled(script, item.checkState() == Qt.CheckState.Checked)

    def _open_folder(self) -> None:
        from PySide6.QtGui import QDesktopServices

        self._engine.rescan()  # ensures the folder exists
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._engine.user_dir())))
