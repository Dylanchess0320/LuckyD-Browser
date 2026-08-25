"""Browser Control API — localhost HTTP control of the LIVE browser.

This is what gives the LuckyD harness (luckyd-code.exe), the terminal agent,
and any local script full power over the real browser window: read pages,
drive tabs, click/type with the same element indexing the AI agent uses,
grab screenshots, or ask the page-aware AI directly.

Security model: binds to 127.0.0.1 ONLY (same trust model as the CDP port
9222 the app already exposes). Set the ``browser_api_token`` setting to
additionally require ``Authorization: Bearer <token>`` on every request.

Architecture: a stdlib ``ThreadingHTTPServer`` in a daemon thread owns the
sockets; every Qt/WebEngine call is marshaled onto the GUI thread through
``GuiInvoker`` (queued signal + threading.Event). The HTTP routing layer
(``make_handler``) is Qt-free and unit-testable with any backend object that
implements the small backend protocol used below.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


def _export_env_to_os() -> None:
    """Push .env keys into os.environ so spawned tile services inherit them.

    The AI bridge loads .env into its own dict; child processes launched by
    the TileRegistry (e.g. Deck Studio's node pipeline, which needs
    GOOGLE_API_KEY for image generation) only see real environment variables.
    Never overrides anything already set.
    """
    try:
        from browser_core.ai_bridge import _load_env

        for key, value in _load_env().items():
            if key.endswith(("_KEY", "_MODEL", "_HOST", "_PROVIDER", "_BASE_URL")):
                os.environ.setdefault(key, value)
    except Exception:
        pass  # env export is best-effort — never block server startup


_export_env_to_os()

from browser_core.agent import (
    _CLICK_JS,
    _HIGHLIGHT_JS,
    _PRESS_JS,
    _SELECT_JS,
    _SNAPSHOT_JS,
    _TYPE_JS,
)
from browser_core.dashboard import (
    dashboard_html,
    hq_shell_html,
    hq_splash_html,
    netmon_html,
    workflows_html,
)
from browser_core.extract import build_messages, parse_json_loose
from browser_core.netmon import NetMonitor, to_har
from browser_core.scheduler import INTERVALS, ScheduleStore
from browser_core.terminal_page import STATIC_DIR, mesh_html, terminal_html
from browser_core.workflows import (
    INDEXED_ACTIONS,
    WorkflowRecorder,
    WorkflowStore,
    elements_js,
    fingerprint_js,
    resolve_index,
    step_record,
)

API_NAME = "Browser Control API"
API_VERSION = "1.4.0"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9777

# Snapshot sizing — matches agent.JsDriver defaults.
_MAX_ELS = 80
_MAX_TEXT = 6000

# Polled after every action: a change means the page reacted (navigation or
# readyState flip), the same settle heuristic JsDriver uses.
_SETTLE_JS = "(location.href + '|' + document.readyState)"

_ROUTES = (
    ("GET  /help", "this route list"),
    ("GET  /dashboard", "live new-tab dashboard (HTML hub of the platform)"),
    ("GET  /hq", "coding agent workspace — opens the exe UI (auto-starts if needed)"),
    ("GET  /mesh", "Agent Mesh — four parallel terminal sessions in one workspace"),
    ("GET  /terminal", "live LuckyD Code terminal in a tab (xterm.js + PTY)"),
    ("GET  /status", "browser state + harness/AI reachability"),
    ("GET  /tabs", "open tabs (index, url, title, active)"),
    ("POST /navigate", '{"url": "https://…", "new_tab": false}'),
    ("POST /tab/new", '{"url": "https://…" (optional)}'),
    ("POST /tab/activate", '{"index": N}'),
    ("POST /tab/close", '{"index": N} (refuses to close the last tab)'),
    ("POST /snapshot", "URL, title, numbered interactive elements, visible text"),
    (
        "POST /act",
        '{"action": "click|type|press|select|scroll|navigate|back|wait",'
        ' "index": N, "text": "…", "url": "https://…"}',
    ),
    ("GET  /screenshot?url=", "base64 JPEG of a tab (CDP; needs websockets)"),
    ("POST /eval", '{"js": "…"} — run JavaScript in the active tab'),
    ("POST /ask", '{"question": "…", "provider": "…" (optional)} — AI + page context'),
    ("GET  /workflows", "workflow manager page (record/replay saved automations)"),
    ("GET  /workflows/list", "saved workflows + recorder status (JSON)"),
    ("POST /workflow/record", '{"name": "…"} — start recording /navigate + /act traffic'),
    ("POST /workflow/stop", "stop recording and save the workflow"),
    ("POST /workflow/replay", '{"name": "…"} — replay with self-healing element match'),
    ("POST /workflow/delete", '{"name": "…"}'),
    (
        "POST /extract",
        '{"instruction": "…", "schema": {…} (optional), "provider": "…" (optional)}'
        " — structured JSON data from the page (AI)",
    ),
    ("POST /theme", '{"name": "neon|cyber|solar|arctic|synthwave"} — live theme switch'),
    ("GET  /network", "network monitor page (live request log of the active tab)"),
    ("GET  /network/events?since=N", "incremental request rows (JSON)"),
    ("GET  /network/har", "download the capture as HAR 1.2"),
    ("POST /network/start", '{"url": "…" (optional — defaults to the active tab)}'),
    ("POST /network/stop", "stop capturing"),
    ("POST /network/clear", "clear captured rows"),
    ("GET  /schedules", "workflow schedules with last-run results"),
    ("POST /schedule", '{"name": "…", "every_min": 0|15|30|60|360|1440} — auto-replay a workflow'),
)


# ── HTTP routing layer (Qt-free — unit-testable with a fake backend) ──────


def make_handler(backend, token: str = "", harness=None, settings=None):
    """Build a BaseHTTPRequestHandler subclass bound to ``backend``.

    The backend must provide: status(), tabs(), navigate(), new_tab(),
    activate_tab(), close_tab(), snapshot(), act(), eval_js(), screenshot(),
    ask(). Each returns a JSON-serializable value or raises.

    ``harness`` is an optional HarnessSupervisor: it powers the /hq gateway
    (redirect-or-autostart) and enriches /status with harness + AI details.
    ``settings`` (optional) lets the HTML surfaces render with the active
    theme so the dashboard/HQ splash match the rest of the product.
    """

    class ControlHandler(BaseHTTPRequestHandler):
        server_version = "LuckyDBrowserControl/1.2"
        protocol_version = "HTTP/1.1"

        # ── helpers ───────────────────────────────────────────────────
        def _authorized(self) -> bool:
            # token is now always non-empty by the time it reaches here (see
            # BrowserControlServer.__init__ / browser_app.py) — this endpoint
            # can navigate tabs, run arbitrary JS via /eval, and read whatever
            # page is open, including logged-in sessions. An empty token used
            # to mean "trust localhost", but binding to 127.0.0.1 doesn't
            # protect this: any page loaded in the browser itself can reach
            # 127.0.0.1:9777 with a normal cross-origin fetch().
            if not token:
                return True  # only true if a caller explicitly disabled it
            return hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {token}")

        def _host_ok(self) -> bool:
            """DNS rebinding defense: Host must be loopback if present."""
            host = self.headers.get("Host", "")
            if not host:
                return True
            # Allow 127.0.0.1[:port], localhost[:port], ::1
            h = host.split(":")[0].lower()
            return h in ("127.0.0.1", "localhost", "::1")

        def _origin_ok(self) -> bool:
            """Reject cross-origin browser fetches outright (defense in depth).

            A page loaded from any website can still fire the request even
            though 9777 is loopback-only — the browser itself is the client.
            Requests without an Origin header (scripts, curl, the harness)
            pass through to the token check above.
            """
            origin = self.headers.get("Origin")
            if not origin:
                return True
            host = self.headers.get("Host", "")
            return origin in (f"http://{host}", f"https://{host}")

        def _send(self, code: int, obj) -> None:
            body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
            try:
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (ConnectionError, OSError):
                pass  # client (tab) went away mid-response — expected

        def _send_html(self, html_text: str, code: int = 200) -> None:
            body = html_text.encode("utf-8")
            try:
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (ConnectionError, OSError):
                pass

        def _redirect(self, location: str) -> None:
            try:
                self.send_response(302)
                self.send_header("Location", location)
                self.send_header("Content-Length", "0")
                self.end_headers()
            except (ConnectionError, OSError):
                pass

        def _send_file(self, fs_path, mime: str) -> None:
            """Stream a vendored static asset (xterm.js & friends)."""
            try:
                body = fs_path.read_bytes()
            except OSError:
                return self._send(404, {"ok": False, "error": "asset not found"})
            try:
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(body)
            except (ConnectionError, OSError):
                pass

        def _send_download(self, text: str, filename: str, mime: str) -> None:
            """Send a file download (Content-Disposition attachment)."""
            body = text.encode("utf-8")
            try:
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (ConnectionError, OSError):
                pass

        def _terminal_asset(self, path: str) -> None:
            """Serve a vendored xterm asset, guarding against path traversal."""
            name = path.rsplit("/", 1)[-1]
            fs_path = (STATIC_DIR / name).resolve()
            if STATIC_DIR.resolve() not in fs_path.parents:
                return self._send(403, {"ok": False, "error": "forbidden"})
            mime = {
                ".js": "application/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8",
            }.get(fs_path.suffix.lower(), "application/octet-stream")
            return self._send_file(fs_path, mime)

        def _hq(self, query: dict) -> None:
            """Gateway to the coding agent workspace (the exe's web UI).

            Healthy  → 302 straight to it.  Down → background-start the exe
            and show an auto-refreshing splash.  Missing/broken → a clear
            error page with a manual Retry link (/?retry=1 forces a retry).
            """
            if harness is None:
                return self._send_html(
                    hq_splash_html(
                        "",
                        "error",
                        "No harness supervisor is wired " "into this Control API instance.",
                        settings=settings,
                    )
                )
            if harness.probe()["up"]:
                # Serve the workspace in the branded shell (theme header bar +
                # theme-synced iframe) instead of a bare 302 to the exe UI.
                return self._send_html(hq_shell_html(harness.url, settings=settings))
            st = harness.status()
            if query.get("retry"):
                harness.ensure_started(force=True)
                st = harness.status()
            elif not st.get("starting") and not st.get("error"):
                harness.ensure_started()
                st = harness.status()
            if st.get("starting"):
                return self._send_html(hq_splash_html(st["url"], "starting", settings=settings))
            return self._send_html(
                hq_splash_html(
                    st["url"], "error", st.get("error") or "unknown error", settings=settings
                )
            )

        def _ok(self, **fields) -> None:
            self._send(200, {"ok": True, **fields})

        def _fail(self, code: int, exc: Exception) -> None:
            self._send(code, {"ok": False, "error": str(exc) or repr(exc)})

        _MAX_BODY = 1 << 20  # 1 MB — prevents OOM via Content-Length: 2GB

        def _body(self) -> dict:
            try:
                size = int(self.headers.get("Content-Length", 0) or 0)
            except ValueError:
                size = 0
            if size <= 0:
                return {}
            if size > self._MAX_BODY:
                return {}
            try:
                raw = self.rfile.read(size) or b"{}"
                if len(raw) > self._MAX_BODY:
                    return {}
                data = json.loads(raw)
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}

        def log_message(self, *args):  # keep the app quiet
            pass

        # ── dispatch ──────────────────────────────────────────────────
        def _status(self) -> dict:
            """backend.status() + harness supervisor + AI provider details."""
            info = backend.status()
            if harness is not None:
                with contextlib.suppress(Exception):
                    st = harness.status()
                    info["harness"] = bool(st.get("up"))
                    info["harness_starting"] = bool(st.get("starting"))
                    info["harness_tools"] = st.get("tools")
                    info["harness_url"] = st.get("url")
                    if st.get("error"):
                        info["harness_error"] = str(st["error"])
            ai_info = getattr(backend, "ai_info", None)
            if callable(ai_info):
                with contextlib.suppress(Exception):
                    info.update(ai_info())
            return info

        # Routes reachable by direct browser navigation (new-tab URL, iframe
        # src, <script>/<link> tags) can never carry an Authorization header
        # — only same-origin fetch() calls from a page's own JS can. These stay
        # gated by _origin_ok() (direct navigation never sends a mismatched
        # Origin) and get the token injected into their own JS instead, the
        # same pattern web_server.py uses for its landing page.
        _NAV_PATHS = ("/", "/help", "/dashboard", "/hq", "/mesh", "/terminal")

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, {"ok": False, "error": "forbidden host"})
            if not self._origin_ok():
                return self._send(403, {"ok": False, "error": "forbidden origin"})
            parsed = urlparse(self.path)
            path, query = parsed.path, parse_qs(parsed.query)
            is_nav = path in self._NAV_PATHS or path.startswith("/static/terminal/")
            if not is_nav and not self._authorized():
                return self._send(401, {"ok": False, "error": "unauthorized"})
            try:
                if path in ("/", "/help"):
                    return self._ok(
                        name=API_NAME,
                        version=API_VERSION,
                        routes=[{"route": r, "about": a} for r, a in _ROUTES],
                    )
                if path == "/dashboard":
                    return self._send_html(dashboard_html(settings, token))
                if path == "/hq":
                    return self._hq(query)
                if path == "/mesh":
                    return self._send_html(mesh_html(token))
                if path == "/terminal":
                    shell = (query.get("shell") or ["agent"])[0]
                    # B604 false positive: `shell` is an allowlisted terminal profile name.
                    return self._send_html(terminal_html(settings, shell=shell))  # nosec B604
                if path.startswith("/static/terminal/"):
                    return self._terminal_asset(path)
                if path == "/status":
                    # Platform heartbeat: also revive any enabled+autostart
                    # tile services (rate-limited inside the registry).
                    from browser_core import tile_registry

                    tile_registry.ensure_autostart()
                    return self._ok(**self._status())
                if path == "/tabs":
                    return self._ok(tabs=backend.tabs())
                if path == "/screenshot":
                    url = (query.get("url") or [""])[0]
                    return self._ok(image_b64=backend.screenshot(url), mime="image/jpeg")
                if path == "/workflows":
                    return self._send_html(workflows_html())
                if path == "/workflows/list":
                    return self._ok(**backend.list_workflows())
                if path == "/schedules":
                    return self._ok(**backend.schedule_list())
                if path == "/network":
                    return self._send_html(netmon_html())
                if path == "/network/events":
                    try:
                        since = int((query.get("since") or ["0"])[0] or 0)
                    except ValueError:
                        since = 0
                    return self._ok(**backend.netmon_events(since))
                if path == "/network/har":
                    har = json.dumps(backend.netmon_har(), indent=1)
                    return self._send_download(har, "luckyd-capture.har", "application/json")
                return self._send(404, {"ok": False, "error": f"unknown route {path}"})
            except Exception as exc:
                return self._fail(500, exc)

        def do_POST(self):
            if not self._host_ok():
                return self._send(403, {"ok": False, "error": "forbidden host"})
            if not self._origin_ok():
                return self._send(403, {"ok": False, "error": "forbidden origin"})
            if not self._authorized():
                return self._send(401, {"ok": False, "error": "unauthorized"})
            path = urlparse(self.path).path
            body = self._body()
            try:
                if path == "/navigate":
                    url = str(body.get("url", "")).strip()
                    if not url:
                        return self._send(400, {"ok": False, "error": "url required"})
                    return self._ok(url=backend.navigate(url, bool(body.get("new_tab"))))
                if path == "/tab/new":
                    url = str(body.get("url", "")).strip() or None
                    return self._ok(index=backend.new_tab(url))
                if path == "/tab/activate":
                    return self._ok(index=backend.activate_tab(int(body.get("index", 0))))
                if path == "/tab/close":
                    return self._ok(closed=backend.close_tab(int(body.get("index", -1))))
                if path == "/snapshot":
                    return self._ok(snapshot=backend.snapshot())
                if path == "/act":
                    if not body.get("action"):
                        return self._send(400, {"ok": False, "error": "action required"})
                    return self._ok(result=backend.act(body))
                if path == "/eval":
                    js = str(body.get("js", ""))
                    if not js:
                        return self._send(400, {"ok": False, "error": "js required"})
                    return self._ok(result=backend.eval_js(js))
                if path == "/ask":
                    question = str(body.get("question", "")).strip()
                    if not question:
                        return self._send(400, {"ok": False, "error": "question required"})
                    provider = str(body.get("provider", "")).strip() or None
                    return self._ok(answer=backend.ask(question, provider))
                if path == "/workflow/record":
                    name = str(body.get("name", "")).strip()
                    if not name:
                        return self._send(400, {"ok": False, "error": "name required"})
                    return self._ok(name=backend.start_recording(name))
                if path == "/workflow/stop":
                    return self._ok(**backend.stop_recording())
                if path == "/workflow/replay":
                    name = str(body.get("name", "")).strip()
                    if not name:
                        return self._send(400, {"ok": False, "error": "name required"})
                    return self._ok(**backend.replay_workflow(name))
                if path == "/workflow/delete":
                    name = str(body.get("name", "")).strip()
                    if not name:
                        return self._send(400, {"ok": False, "error": "name required"})
                    return self._ok(deleted=backend.delete_workflow(name))
                if path == "/schedule":
                    name = str(body.get("name", "")).strip()
                    if not name:
                        return self._send(400, {"ok": False, "error": "name required"})
                    try:
                        every = int(body.get("every_min", 0) or 0)
                    except (TypeError, ValueError):
                        every = 0
                    return self._ok(**backend.schedule_set(name, every))
                if path == "/extract":
                    instruction = str(body.get("instruction", "")).strip()
                    if not instruction:
                        return self._send(400, {"ok": False, "error": "instruction required"})
                    schema = body.get("schema") if isinstance(body.get("schema"), dict) else None
                    provider = str(body.get("provider", "")).strip() or None
                    return self._ok(data=backend.extract(instruction, schema, provider))
                if path == "/theme":
                    name = str(body.get("name", "")).strip()
                    if not name:
                        return self._send(400, {"ok": False, "error": "name required"})
                    return self._ok(theme=backend.set_theme(name))
                if path == "/network/start":
                    return self._ok(**backend.netmon_start(str(body.get("url", "") or "")))
                if path == "/network/stop":
                    return self._ok(**backend.netmon_stop())
                if path == "/network/clear":
                    return self._ok(**backend.netmon_clear())
                return self._send(404, {"ok": False, "error": f"unknown route {path}"})
            except Exception as exc:
                return self._fail(500, exc)

    return ControlHandler


class _QuietHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that doesn't print tracebacks for dead clients.

    Closing/navigating a dashboard tab mid-poll aborts the socket — that's
    normal browsing, not a server bug.
    """

    def handle_error(self, request, client_address) -> None:
        import sys

        exc_type = sys.exc_info()[0]
        if exc_type is not None and issubclass(exc_type, (ConnectionError, OSError)):
            return
        super().handle_error(request, client_address)


class BrowserControlServer:
    """Threaded localhost HTTP server wrapping a backend.

    Construct it (binds the port), then ``start()`` / ``stop()`` from any
    thread. Serving runs in a daemon thread, so the Qt event loop is never
    blocked by HTTP clients.
    """

    def __init__(
        self,
        backend,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        token: str = "",
        harness=None,
        settings=None,
    ):
        self._httpd = _QuietHTTPServer(
            (host, port), make_handler(backend, token, harness, settings)
        )
        self._httpd.daemon_threads = True
        # Resolve the real bound address (matters when port=0 → OS-assigned).
        self.host, self.port = self._httpd.server_address[:2]
        self.base_url = f"http://{self.host}:{self.port}"
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="browser-control-api",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread = None


# ── Qt backend (drives the real BrowserApp on the GUI thread) ─────────────

try:  # PySide6 is a hard requirement of the app; guarded for headless tests
    from PySide6.QtCore import QObject, Signal

    _HAVE_QT = True
except ImportError:  # pragma: no cover
    _HAVE_QT = False

_ASK_SYSTEM = (
    "You answer questions about a web page the user has open. Ground every "
    "answer in the provided page excerpt; say plainly when the answer is not "
    "on the page. Be concise."
)

if _HAVE_QT:

    class GuiInvoker(QObject):
        """Runs callables on the GUI thread; HTTP workers block for the result.

        NEVER call ``run()`` from the GUI thread itself — that deadlocks.
        """

        invoke = Signal(object)

        def __init__(self):
            super().__init__()
            self.invoke.connect(self._execute)

        def _execute(self, payload) -> None:
            fn, box, done = payload
            try:
                box["result"] = fn()
            except Exception as exc:  # surfaced to the calling worker thread
                box["error"] = exc
            finally:
                done.set()

        def run(self, fn, timeout: float = 20.0):
            box, done = {}, threading.Event()
            self.invoke.emit((fn, box, done))
            if not done.wait(timeout):
                raise TimeoutError("GUI thread did not respond in time")
            if "error" in box:
                raise box["error"]
            return box.get("result")


class QtBrowserBackend:
    """Backend implementation driving the real ``BrowserApp`` windows.

    Every method is safe to call from HTTP worker threads: Qt work is
    marshaled through :class:`GuiInvoker`, and page JS additionally spins a
    short-lived ``QEventLoop`` on the GUI thread so the runJavaScript
    callback can fire (blocking the GUI thread with threading.Event there
    would deadlock).
    """

    def __init__(self, app):
        if not _HAVE_QT:
            raise RuntimeError("PySide6 is required for QtBrowserBackend")
        self._app = app
        self._invoker = GuiInvoker()
        self._ai = None  # lazy AIBridge for /ask and /extract
        self._recorder = WorkflowRecorder()
        self._wf_store = WorkflowStore()
        self._replaying = False  # replayed steps must not re-record
        self._netmon: NetMonitor | None = None  # lazy network capture
        self._schedules: ScheduleStore | None = None  # lazy schedule store

    # ── GUI-thread helpers ────────────────────────────────────────────
    def _window(self):
        target = self._app.qapp.activeWindow()
        wins = self._app.windows
        if target not in wins:
            target = wins[-1] if wins else None
        if target is None:
            raise RuntimeError("no browser window open")
        return target

    def _view(self):
        view = self._window().tabs.current_view()
        if view is None:
            raise RuntimeError("no active tab")
        return view

    def run_js(self, js: str, timeout: float = 10.0):
        """Evaluate JS in the active tab's page and return the value."""

        def _do():
            from PySide6.QtCore import QEventLoop, QTimer

            view = self._view()
            box: dict = {}
            loop = QEventLoop()
            view.page().runJavaScript(js, lambda res: (box.__setitem__("r", res), loop.quit()))
            QTimer.singleShot(int(timeout * 1000), loop.quit)
            loop.exec()
            if "r" not in box:
                raise TimeoutError("page JavaScript timed out")
            return box["r"]

        return self._invoker.run(_do, timeout + 5.0)

    def _wait_settled(self, prev, timeout: float = 4.0) -> None:
        """Return once URL|readyState changes (JsDriver's settle heuristic)."""
        deadline = time.monotonic() + timeout
        time.sleep(0.15)
        while time.monotonic() < deadline:
            try:
                state = self.run_js(_SETTLE_JS, timeout=3.0)
            except Exception:
                state = None
            if state is not None and state != prev:
                time.sleep(0.4)  # let the fresh page render a beat
                return
            time.sleep(0.25)

    # ── routes ────────────────────────────────────────────────────────
    def status(self) -> dict:
        def _do():
            win = self._window()
            view = win.tabs.current_view()
            return {
                "tabs": win.tabs.count(),
                "current_url": view.url().toString() if view else "",
            }

        info = self._invoker.run(_do)
        info.update(name=API_NAME, version=API_VERSION, cdp="127.0.0.1:9222")
        with contextlib.suppress(Exception):
            info["ads_blocked"] = int(self._app.adblock.blocked_count)
        try:  # harness reachability — network stays off the GUI thread
            import httpx

            r = httpx.get("http://127.0.0.1:8000/health", timeout=1.5)
            info["harness"] = r.status_code == 200
        except Exception:
            info["harness"] = False
        return info

    def ai_info(self) -> dict:
        """Configured AI providers + default — powers the dashboard's AI pill."""
        if self._ai is None:
            from browser_core.ai_bridge import AIBridge

            self._ai = AIBridge()
        try:
            return {
                "ai_providers": self._ai.providers(),
                "ai_default": self._ai.default_provider(),
            }
        except Exception:
            return {"ai_providers": [], "ai_default": None}

    def tabs(self) -> list[dict]:
        def _do():
            win = self._window()
            out = []
            for i in range(win.tabs.count()):
                view = win.tabs.widget(i)
                out.append(
                    {
                        "index": i,
                        "url": view.url().toString() if view else "",
                        "title": win.tabs.tabText(i),
                        "active": i == win.tabs.currentIndex(),
                    }
                )
            return out

        return self._invoker.run(_do)

    @staticmethod
    def _normalize_url(url: str) -> str:
        if not url.startswith(("http://", "https://", "file://", "about:")):
            url = "https://" + url
        return url

    def navigate(self, url: str, new_tab: bool = False) -> str:
        url = self._normalize_url(url)

        def _do():
            from PySide6.QtCore import QUrl

            win = self._window()
            if new_tab:
                view = win.open_in_new_tab(QUrl(url))
            else:
                win.load_in_current_tab(QUrl(url))
                view = win.tabs.current_view()
            return view.url().toString() if view else url

        result = self._invoker.run(_do)
        if self._recorder.active and not self._replaying:
            self._recorder.add(step_record({"action": "navigate", "url": url}))
        return result

    def new_tab(self, url: str | None = None) -> int:
        def _do():
            from PySide6.QtCore import QUrl

            win = self._window()
            win.new_tab(QUrl(self._normalize_url(url)) if url else None)
            return win.tabs.currentIndex()

        return self._invoker.run(_do)

    def activate_tab(self, index: int) -> int:
        def _do():
            win = self._window()
            if not 0 <= index < win.tabs.count():
                raise IndexError(f"tab index {index} out of range (0..{win.tabs.count() - 1})")
            win.tabs.setCurrentIndex(index)
            return index

        return self._invoker.run(_do)

    def close_tab(self, index: int) -> int:
        def _do():
            win = self._window()
            count = win.tabs.count()
            target = win.tabs.currentIndex() if index < 0 else index
            if not 0 <= target < count:
                raise IndexError(f"tab index {target} out of range (0..{count - 1})")
            if count <= 1:
                raise RuntimeError("refusing to close the last tab in the window")
            win.tabs.close_tab(target)
            return target

        return self._invoker.run(_do)

    def snapshot(self) -> dict:
        js = _SNAPSHOT_JS.replace("__MAXELS__", str(_MAX_ELS)).replace(
            "__MAXTEXT__", str(_MAX_TEXT)
        )
        raw = self.run_js(js, timeout=10.0)
        if not raw:
            raise RuntimeError("snapshot failed — no readable page in the active tab")
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise RuntimeError(f"snapshot parse failed: {exc}") from exc

    # Results that mean "the action didn't happen" — never recorded.
    _FAILED_PREFIXES = ("element not found", "refused", "unknown action", "no target")

    def act(self, action: dict) -> str:
        """Record (when active) + execute one agent-style action."""
        fingerprint = None
        kind = str(action.get("action", "")).strip().lower()
        index = int(action.get("index", -1) or -1)
        if self._recorder.active and not self._replaying and kind in INDEXED_ACTIONS and index >= 0:
            # Fingerprint BEFORE acting: a click may destroy/navigate the node.
            with contextlib.suppress(Exception):
                raw = self.run_js(fingerprint_js(index))
                fingerprint = json.loads(raw) if raw else None
        result = self._perform_act(action)
        if (
            self._recorder.active
            and not self._replaying
            and not str(result).startswith(self._FAILED_PREFIXES)
        ):
            self._recorder.add(step_record(action, fingerprint))
        return result

    def _perform_act(self, action: dict) -> str:
        """One agent-style action (click/type/press/select/scroll/navigate/…)."""
        kind = str(action.get("action", ""))
        index = int(action.get("index", -1) or -1)
        text = str(action.get("text", "") or "")
        url = str(action.get("url", "") or "")

        if kind == "wait":
            try:
                secs = float(text or 1)
            except ValueError:
                secs = 1.0
            secs = max(0.2, min(secs, 5.0))
            time.sleep(secs)
            return f"waited {secs:.1f}s"

        prev = None
        with contextlib.suppress(Exception):
            prev = self.run_js(_SETTLE_JS)  # settle detection is best-effort

        if kind == "click":
            if index < 0:
                return "click needs an element index"
            self.run_js(_HIGHLIGHT_JS.format(i=index))
            time.sleep(0.15)
            result = self.run_js(_CLICK_JS.format(i=index))
        elif kind == "type":
            if index < 0:
                return "type needs an element index"
            self.run_js(_HIGHLIGHT_JS.format(i=index))
            time.sleep(0.15)
            result = self.run_js(_TYPE_JS.format(i=index, text=json.dumps(text)))
        elif kind == "press":
            key = text if text in ("Enter", "Tab", "Escape") else "Enter"
            result = self.run_js(_PRESS_JS.format(key=json.dumps(key)))
        elif kind == "select":
            if index < 0:
                return "select needs an element index"
            self.run_js(_HIGHLIGHT_JS.format(i=index))
            time.sleep(0.15)
            result = self.run_js(_SELECT_JS.format(i=index, text=json.dumps(text)))
        elif kind == "scroll":
            dy = -700 if text.lower() == "up" else 700
            self.run_js(f"window.scrollBy(0, {dy})")
            time.sleep(0.2)
            return "scrolled"
        elif kind == "navigate":
            if not url.startswith(("http://", "https://")):
                return "refused: invalid url"
            self.run_js(f"window.location.href = {json.dumps(url)}")
            self._wait_settled(prev, timeout=8.0)
            return f"navigated to {url}"
        elif kind == "back":
            self.run_js("history.back()")
            self._wait_settled(prev, timeout=4.0)
            return "went back"
        else:
            return f"unknown action: {kind!r}"

        self._wait_settled(prev)
        return str(result)

    def eval_js(self, js: str):
        return self.run_js(js, timeout=15.0)

    def screenshot(self, url: str = "") -> str:
        from browser_core.screenshot import capture_b64

        return asyncio.run(capture_b64(url))

    def ask(self, question: str, provider: str | None = None) -> str:
        snap: dict = {}
        with contextlib.suppress(Exception):
            snap = self.snapshot()  # page context is best-effort
        if self._ai is None:
            from browser_core.ai_bridge import AIBridge

            self._ai = AIBridge()
        messages = [{"role": "system", "content": _ASK_SYSTEM}]
        if snap:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Page: {snap.get('title', '')} <{snap.get('url', '')}>\n\n"
                        f"{snap.get('text', '')[:6000]}"
                    ),
                }
            )
        messages.append({"role": "user", "content": question})
        text, _used = asyncio.run(self._ai.chat(messages, provider=provider))
        return text

    # ── workflows: record / replay with self-healing ───────────────────

    def start_recording(self, name: str) -> str:
        return self._recorder.start(name)

    def stop_recording(self) -> dict:
        name, steps = self._recorder.stop()
        if name is None:
            return {"saved": False, "name": "", "steps": 0}
        saved = self._wf_store.save(name, steps) if steps else ""
        return {"saved": bool(saved), "name": saved or name, "steps": len(steps)}

    def list_workflows(self) -> dict:
        return {"recording": self._recorder.status(), "workflows": self._wf_store.list()}

    def delete_workflow(self, name: str) -> bool:
        return self._wf_store.delete(name)

    def replay_workflow(self, name: str) -> dict:
        """Replay a saved workflow against the live tab.

        Indexed steps re-resolve their target on a fresh snapshot: the
        recorded fingerprint is scored against the current elements and the
        best match wins (self-healing), falling back to the recorded index.
        Stops at the first broken element step rather than clicking blind.
        """
        data = self._wf_store.load(name)
        if data is None:
            raise KeyError(f"workflow {name!r} not found")
        steps = data.get("steps", [])
        results: list[dict] = []
        self._replaying = True
        try:
            for i, step in enumerate(steps):
                self._wait_ready(timeout=4.0)  # never act into a mid-reload page
                kind = str(step.get("action", ""))
                action: dict = {"action": kind}
                healed = False
                if kind in INDEXED_ACTIONS:
                    candidates: list[dict] = []
                    with contextlib.suppress(Exception):
                        self.snapshot()  # retags data-ld-agent on the live page
                        raw = self.run_js(elements_js())
                        candidates = json.loads(raw) if raw else []
                    index, healed = resolve_index(
                        step.get("target"), candidates, int(step.get("index", 0) or 0)
                    )
                    action["index"] = index
                if "text" in step:
                    action["text"] = step["text"]
                if "url" in step:
                    action["url"] = step["url"]
                try:
                    detail = self.act(action)
                    ok = not str(detail).startswith(self._FAILED_PREFIXES)
                except Exception as exc:
                    detail, ok = str(exc), False
                results.append(
                    {
                        "step": i + 1,
                        "action": kind,
                        "ok": ok,
                        "healed": healed,
                        "detail": str(detail)[:200],
                    }
                )
                time.sleep(0.25)  # let the page breathe between steps
                if not ok and kind in INDEXED_ACTIONS:
                    break
        finally:
            self._replaying = False
        done = sum(1 for r in results if r["ok"])
        return {
            "workflow": data.get("name", name),
            "total": len(steps),
            "succeeded": done,
            "results": results,
        }

    def _wait_ready(self, timeout: float = 4.0) -> None:
        """Block until the active page reports readyState complete (bounded)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self.run_js("document.readyState", timeout=2.0) == "complete":
                    return
            except Exception:
                pass
            time.sleep(0.2)

    # ── structured extraction (Stagehand-style) ────────────────────────

    def extract(self, instruction: str, schema: dict | None = None, provider: str | None = None):
        """Pull structured JSON out of the active page with the AI provider."""
        snap = self.snapshot()
        if self._ai is None:
            from browser_core.ai_bridge import AIBridge

            self._ai = AIBridge()
        meta = f"{snap.get('title', '')} <{snap.get('url', '')}>"
        messages = build_messages(instruction, schema, snap.get("text", ""), meta)
        text, _used = asyncio.run(self._ai.chat(messages, provider=provider))
        data = parse_json_loose(text)
        if data is None:
            raise RuntimeError(f"model did not return parseable JSON: {text[:200]}")
        return data

    # ── theming ───────────────────────────────────────────────────────

    def set_theme(self, name: str) -> str:
        """Live-switch the app theme on every window (Settings equivalent)."""
        from browser_ui.theme import THEMES

        if name not in THEMES:
            raise ValueError(f"unknown theme {name!r}")
        self._app.settings.set("theme", name)

        def _do():
            for win in list(self._app.windows):
                with contextlib.suppress(Exception):
                    win._apply_theme()

        self._invoker.run(_do)
        return name

    # ── network monitor ──────────────────────────────────────────────

    def netmon_start(self, url_substr: str = "") -> dict:
        """Start capturing the active tab's network traffic (CDP Network domain)."""
        if not url_substr:

            def _do():
                return self._view().url().toString()

            with contextlib.suppress(Exception):
                url_substr = self._invoker.run(_do)
        if self._netmon is None:
            self._netmon = NetMonitor()
        self._netmon.start(url_substr)
        return {"started": True, "target": url_substr}

    def netmon_events(self, since: int = 0) -> dict:
        if self._netmon is None:
            return {"running": False, "target": "", "error": "", "seq": 0, "rows": []}
        return self._netmon.rows(since)

    def netmon_stop(self) -> dict:
        if self._netmon is not None:
            self._netmon.stop()
        return {"stopped": True}

    def netmon_clear(self) -> dict:
        if self._netmon is not None:
            self._netmon.clear()
        return {"cleared": True}

    def netmon_har(self) -> dict:
        if self._netmon is None:
            return to_har([])
        return to_har(self._netmon.rows(0)["rows"], self._netmon.target)

    # ── workflow schedules ────────────────────────────────────────────

    def schedule_list(self) -> dict:
        if self._schedules is None:
            self._schedules = ScheduleStore()
        return {"schedules": self._schedules.list(), "intervals": INTERVALS}

    def schedule_set(self, name: str, every_min: int) -> dict:
        if self._schedules is None:
            self._schedules = ScheduleStore()
        entry = self._schedules.set(name, every_min)
        every = int(entry.get("every_min", 0) or 0)
        return {"name": name, "every_min": every, "label": INTERVALS.get(every, "Off")}
