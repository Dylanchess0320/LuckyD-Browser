"""Application object: owns QApplication, shared services, and windows."""

from __future__ import annotations

import contextlib
import threading
from pathlib import Path

from browser_core.adblock import AdBlockInterceptor
from browser_core.harness_bridge import HarnessSupervisor
from browser_core.profile import default_profile
from browser_core.scheduler import ScheduleStore
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
    schedule_done = Signal(str, bool, str)  # (workflow, ok, detail)


class BrowserApp:
    def __init__(self, argv):
        self.qapp = QApplication(argv)
        self.qapp.setApplicationName("luckyd-browser")
        self.qapp.setApplicationDisplayName("LuckyD Browser")
        self.qapp.setOrganizationName("LuckyD")

        # Use the multi-resolution ICO for title bars, taskbar, Alt+Tab and
        # dialogs.  The old PNG is a mostly-transparent legacy mark and
        # becomes invisible against light Windows title bars.
        icon_path = ASSETS_DIR / "professional_icon.ico"
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
        self.bus.schedule_done.connect(self._on_schedule_done)

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

        # Workflow schedules: a 30s tick replays whatever is due (in a worker
        # thread — replays drive the GUI through the backend's invoker).
        self.schedule_store = ScheduleStore()
        self.control_backend = None
        self._schedule_running = False
        self._schedule_timer = QTimer(self.qapp)
        self._schedule_timer.setInterval(30_000)
        self._schedule_timer.timeout.connect(self._run_due_schedules)
        self._schedule_timer.start(30_000)

        self.windows: list[MainWindow] = []
        first_window = self.new_window()
        self._restore_previous_session(first_window)
        self._announce_whats_new(first_window)

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
            # LUCKYD_API_PORT overrides the setting (tests run side-by-side
            # with a real browser instance, which already owns 9777).
            import os

            port = int(
                os.environ.get("LUCKYD_API_PORT")
                or int(self.settings.get("browser_api_port", 9777))
            )
            token = str(self.settings.get("browser_api_token", "") or "")
            self.control_backend = QtBrowserBackend(self)
            server = BrowserControlServer(
                self.control_backend,
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
            cli2 = str(self.settings.get("terminal_cli2", "") or "")
            token = str(self.settings.get("terminal_token", "") or "")
            server = TerminalServer(port=port, cli_path=cli, cli2_path=cli2, token=token)
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

    def _announce_whats_new(self, window: MainWindow) -> None:
        """One toast on the first launch after an update."""
        try:
            from browser import WHATS_NEW, __version__

            if str(self.settings.get("last_seen_version", "")) == __version__:
                return
            self.settings.set("last_seen_version", __version__)
            QTimer.singleShot(2500, lambda: window.toast(WHATS_NEW, "ok"))
        except Exception:
            pass

    # ── Workflow schedules (autopilot) ─────────────────────────────────

    def _run_due_schedules(self) -> None:
        backend = self.control_backend
        if backend is None or self._schedule_running:
            return
        due = self.schedule_store.due()
        if not due:
            return
        self._schedule_running = True

        def _work() -> None:
            try:
                for name in due:
                    try:
                        result = backend.replay_workflow(name)
                        ok = result.get("succeeded") == result.get("total")
                        detail = f"{result.get('succeeded')}/{result.get('total')} steps"
                    except Exception as exc:
                        ok, detail = False, str(exc)[:120]
                    self.schedule_store.mark_run(name, detail)
                    self.bus.schedule_done.emit(name, ok, detail)
            finally:
                self._schedule_running = False

        threading.Thread(target=_work, name="workflow-scheduler", daemon=True).start()

    def _on_schedule_done(self, name: str, ok: bool, detail: str) -> None:
        kind = "ok" if ok else "warn"
        msg = f"⏰ Scheduled workflow “{name}” {'finished' if ok else 'had issues'} — {detail}"
        for win in list(self.windows):
            with contextlib.suppress(Exception):
                win.toast(msg, kind)

    def run(self) -> int:
        return self.qapp.exec()
