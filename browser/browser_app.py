"""Application object: owns QApplication, shared services, and windows."""

from __future__ import annotations

import threading
from pathlib import Path

from browser_core.adblock import AdBlockInterceptor
from browser_core.harness_bridge import HarnessSupervisor
from browser_core.profile import default_profile
from browser_core.scripts import ScriptEngine
from browser_core.session import SessionStore
from browser_core.settings import SettingsStore
from browser_core.storage import Storage
from browser_ui.main_window import MainWindow
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

ASSETS_DIR = Path(__file__).resolve().parent / "assets"


class _AppBus(QObject):
    """Thread-safe signal bus — worker threads emit, the GUI thread receives."""

    harness_booted = Signal(bool, str)  # (ok, error_message)


class BrowserApp:
    def __init__(self, argv):
        self.qapp = QApplication(argv)
        self.qapp.setApplicationName("luckyd-browser")
        self.qapp.setApplicationDisplayName("LuckyD Browser")
        self.qapp.setOrganizationName("LuckyD")

        icon_path = ASSETS_DIR / "icon.png"
        if icon_path.exists():
            self.qapp.setWindowIcon(QIcon(str(icon_path)))

        self.settings = SettingsStore()
        self.storage = Storage()
        self.session_store = SessionStore()
        # Session autosave: windows call schedule_session_save() on every tab
        # change; one debounced timer snapshots all windows at once.
        self._session_timer = QTimer(self.qapp)
        self._session_timer.setSingleShot(True)
        self._session_timer.setInterval(1200)
        self._session_timer.timeout.connect(self.save_session)
        self.adblock = AdBlockInterceptor(enabled=bool(self.settings.get("adblock_enabled", True)))

        self.profile = default_profile()
        self.profile.setUrlRequestInterceptor(self.adblock)
        self.profile.downloadRequested.connect(self._dispatch_download)
        self.scripts = ScriptEngine(self.profile, self.settings)

        # One harness backend for the whole app: the sidebar's harness mode,
        # the /hq gateway and the dashboard all share this supervisor.
        self.harness = HarnessSupervisor()

        self.bus = _AppBus()
        self.bus.harness_booted.connect(self._on_harness_boot)

        # Browser Control API — localhost HTTP control of the live browser
        # (the luckyd-code.exe harness and local scripts drive tabs with it).
        # Started BEFORE the first window so new tabs can use the live
        # dashboard (http://127.0.0.1:9777/dashboard) from the very first tab.
        self.control_server = None
        if bool(self.settings.get("browser_api_enabled", True)):
            self.start_control_server()

        # In-browser terminal — WebSocket↔PTY bridge for the LuckyD Code CLI.
        # Started with the Control API; the /terminal tab connects to it.
        self.terminal_server = None
        self.start_terminal_server()
        self.qapp.aboutToQuit.connect(self.stop_control_server)
        self.qapp.aboutToQuit.connect(self.stop_terminal_server)

        self.windows: list[MainWindow] = []
        first_window = self.new_window()
        self._restore_previous_session(first_window)

        # Auto-start the coding-agent backend (luckyd-code.exe) in the
        # background — the one-window platform: browser + assistant + agent.
        if bool(self.settings.get("harness_autostart", True)):
            self.boot_harness_async()

    def new_window(self, incognito: bool = False, url=None) -> MainWindow:
        win = MainWindow(self, incognito=incognito)
        self.windows.append(win)
        win.destroyed.connect(lambda: self._forget(win))
        if url is not None:
            win.open_in_new_tab(url)
        win.show()
        return win

    def _forget(self, win) -> None:
        if win in self.windows:
            self.windows.remove(win)

    def _dispatch_download(self, download) -> None:
        """Route downloads from the shared profile to the active window's dock."""
        target = self.qapp.activeWindow()
        if not isinstance(target, MainWindow):
            target = self.windows[-1] if self.windows else None
        if target is not None:
            target.downloads_handle(download)

    def apply_adblock(self, enabled: bool) -> None:
        self.settings.set("adblock_enabled", enabled)
        self.adblock.set_enabled(enabled)

    # ── Browser Control API ───────────────────────────────────────────

    def start_control_server(self) -> bool:
        """Start the localhost Browser Control API (idempotent)."""
        if self.control_server is not None and self.control_server.running:
            return True
        from browser_core.control_server import (
            BrowserControlServer,
            QtBrowserBackend,
        )

        try:
            port = int(self.settings.get("browser_api_port", 9777))
            token = str(self.settings.get("browser_api_token", "") or "")
            server = BrowserControlServer(
                QtBrowserBackend(self),
                port=port,
                token=token,
                harness=self.harness,
                settings=self.settings,
            )
            server.start()
        except Exception as exc:  # port busy, etc. — browsing must go on
            print(f"[browser] Control API not started: {exc}")
            self.control_server = None
            return False
        self.control_server = server
        print(f"[browser] Control API listening on {server.base_url}")
        return True

    def stop_control_server(self) -> None:
        if self.control_server is not None:
            self.control_server.stop()
            self.control_server = None

    # ── In-browser terminal (WebSocket↔PTY bridge) ──────────────────────

    def start_terminal_server(self) -> bool:
        """Start the terminal WS→PTY bridge (idempotent; never fatal)."""
        if self.terminal_server is not None and self.terminal_server.running:
            return True
        try:
            from browser_core.terminal_server import TerminalServer

            port = int(self.settings.get("terminal_port", 9881))
            cli = str(self.settings.get("terminal_cli", "") or "")
            server = TerminalServer(port=port, cli_path=cli)
            if not server.start():
                print("[browser] terminal bridge not started (pywinpty/websockets?)")
                self.terminal_server = None
                return False
        except Exception as exc:  # port busy, etc. — browsing must go on
            print(f"[browser] terminal bridge not started: {exc}")
            self.terminal_server = None
            return False
        self.terminal_server = server
        print(f"[browser] terminal bridge on ws://{server.host}:{server.port}")
        return True

    def stop_terminal_server(self) -> None:
        if self.terminal_server is not None:
            self.terminal_server.stop()
            self.terminal_server = None

    def set_browser_api_enabled(self, enabled: bool) -> None:
        """Tools-menu toggle: persist + start/stop the Control API."""
        self.settings.set("browser_api_enabled", bool(enabled))
        if enabled:
            self.start_control_server()
        else:
            self.stop_control_server()

    # ── Harness (coding-agent backend) ────────────────────────────────

    def boot_harness_async(self) -> None:
        """Start luckyd-code.exe in a daemon thread; toast via the app bus."""

        def _work() -> None:
            ok, err = self.harness.start_blocking(timeout=30.0)
            self.bus.harness_booted.emit(ok, err)

        threading.Thread(target=_work, name="harness-boot", daemon=True).start()

    def _on_harness_boot(self, ok: bool, err: str) -> None:
        """GUI-thread delivery of the boot result: toast + sidebar refresh."""
        if ok:
            tools = self.harness.last.get("tools")
            msg = f"Coding agent ready — {tools or '98'} tools online"
            print(f"[browser] Harness up at {self.harness.url} ({tools} tools)")
            kind = "ok"
        else:
            msg = f"Coding agent backend didn't start{': ' + err if err else ''}"
            print(f"[browser] Harness boot failed: {err}")
            kind = "warn"
        for win in list(self.windows):
            try:
                win.toasts.show(msg, kind=kind)
                win.ai_sidebar.refresh_harness_status()
            except Exception:
                pass

    # ── Session restore ("continue where you left off") ─────────────────

    def schedule_session_save(self) -> None:
        """Debounced autosave — called by windows on every tab/URL change."""
        self._session_timer.start()

    def save_session(self) -> None:
        """Snapshot every normal window and persist them (atomic write)."""
        try:
            windows = []
            for win in list(self.windows):
                try:
                    snapshot = win._session_snapshot()
                except Exception:
                    snapshot = None
                if snapshot:
                    windows.append(snapshot)
            if windows:
                self.session_store.save(windows)
            else:
                # All windows closed (or only incognito left) — next launch
                # starts fresh instead of resurrecting a stale session.
                self.session_store.clear()
        except Exception:
            pass  # session saving must never take the app down

    def _restore_previous_session(self, first_window: MainWindow) -> None:
        """Reopen last session's tabs when startup_mode is 'restore'."""
        try:
            if str(self.settings.get("startup_mode", "restore")) != "restore":
                return
            windows = self.session_store.load().get("windows") or []
            if not windows:
                return
            first_window.restore_session(windows[0])
            for extra in windows[1:]:
                self.new_window().restore_session(extra)
        except Exception as exc:  # a bad session file must never block startup
            print(f"[browser] session restore skipped: {exc}")

    def run(self) -> int:
        return self.qapp.exec()
