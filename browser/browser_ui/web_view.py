"""QWebEngineView subclass: popups become tabs, custom context menu."""

from __future__ import annotations

import json

from browser_core import agent as _agent
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QMenu


class WebPage(QWebEnginePage):
    """Shared page logic — intercepts luckyd:// internal links."""

    def __init__(self, profile, parent, main_window):
        super().__init__(profile, parent)
        self._mw = main_window

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):  # noqa: N802
        if url.scheme() == "luckyd":
            self._mw.open_internal_page(url.host() or url.path())
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)

    # ── JavaScript dialogs (alert / confirm / prompt) ────────────────
    # While an agent session is active, a modal JS dialog would freeze the
    # agent's runJavaScript channel. Auto-dismiss it instead and record its
    # text into window.__ld_dialog so the next agent snapshot can READ what
    # the popup said and react. Normal browsing (no agent) is unaffected.
    def _record_dialog(self, kind: str, msg: str) -> None:
        payload = json.dumps(f"{kind}: {msg}"[:400])
        self.runJavaScript(f"window.__ld_dialog = {payload}")

    def javaScriptAlert(self, security_origin, msg):  # noqa: N802 (Qt API)
        if _agent.ACTIVE_SESSIONS:
            self._record_dialog("alert", msg)
            return
        super().javaScriptAlert(security_origin, msg)

    def javaScriptConfirm(self, security_origin, msg):  # noqa: N802
        if _agent.ACTIVE_SESSIONS:
            self._record_dialog("confirm (auto-accepted)", msg)
            return True
        return super().javaScriptConfirm(security_origin, msg)

    def javaScriptPrompt(self, security_origin, msg, default_value):  # noqa: N802
        if _agent.ACTIVE_SESSIONS:
            self._record_dialog("prompt (auto-cancelled)", msg)
            return (False, "")
        return super().javaScriptPrompt(security_origin, msg, default_value)


_RETRY_DELAYS_MS = (1200, 2500, 4000)
_MAX_ATTEMPTS = 3

_CONNECTING_HTML = (
    "<!doctype html><html><head><meta charset='utf-8'><style>"
    "body{background:#1b1d23;color:#cfd3dc;font:15px system-ui;display:flex;"
    "height:100vh;align-items:center;justify-content:center;margin:0}"
    ".wrap{text-align:center}.spin{font-size:34px}"
    ".dim{color:#7a7f8a;font-size:12px}</style></head><body><div class='wrap'>"
    "<div class='spin'>⟳</div><p>Connecting… (attempt {n})</p>"
    "<p class='dim'>Network hiccup — retrying automatically.</p>"
    "</div></body></html>"
)

_OFFLINE_HTML = (
    "<!doctype html><html><head><meta charset='utf-8'><style>"
    "body{background:#1b1d23;color:#cfd3dc;font:15px system-ui;display:flex;"
    "height:100vh;align-items:center;justify-content:center;margin:0}"
    ".wrap{text-align:center;max-width:420px}.icon{font-size:34px}"
    ".dim{color:#7a7f8a;font-size:12px}</style></head><body><div class='wrap'>"
    "<div class='icon'>⚠</div><p>Still can't reach this site.</p>"
    "<p class='dim'>Check your connection, firewall, or VPN — then press "
    "F5 to try again.</p></div></body></html>"
)


class WebView(QWebEngineView):
    """A single browser tab's web view."""

    def __init__(self, main_window, profile):
        super().__init__()
        self._mw = main_window
        self.setPage(WebPage(profile, self, main_window))
        # Let pages use the async Clipboard API — the in-browser terminal's
        # copy/paste (Ctrl+Shift+C/V, right-click menu) depends on it. The
        # engine still gates per-origin; this only removes the hard block.
        self.settings().setAttribute(
            QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True
        )
        # Transient network errors (ERR_NETWORK_ACCESS_DENIED & friends at
        # startup / on flaky links) would otherwise flash Chromium's scary
        # "Your Internet access is blocked" page — retry quietly instead.
        self._load_attempts: dict[str, int] = {}
        self._connecting_for: QUrl | None = None
        self.loadFinished.connect(self._on_load_finished)
        self.loadStarted.connect(self._on_load_started)

    # ── transient-failure auto-retry ─────────────────────────────────
    def _on_load_started(self) -> None:
        if self._connecting_for is not None and self.url() != self._connecting_for:
            self._connecting_for = None  # user navigated away — cancel retry

    def _on_load_finished(self, ok: bool) -> None:
        url = self.url()
        if ok:
            # Don't count our own "Connecting…" placeholder as a success —
            # only a real page load resets the attempt counter.
            if self._connecting_for != url:
                self._load_attempts.pop(url.toString(), None)
            self._connecting_for = None
            return
        if url.scheme() not in ("http", "https"):
            return
        key = url.toString()
        if len(self._load_attempts) > 20:
            self._load_attempts.clear()
        attempts = self._load_attempts.get(key, 0) + 1
        self._load_attempts[key] = attempts
        if attempts > _MAX_ATTEMPTS:
            self.setHtml(_OFFLINE_HTML, url)  # give up gracefully
            return
        self._connecting_for = url
        self.setHtml(_CONNECTING_HTML.replace("{n}", str(attempts + 1)), url)
        QTimer.singleShot(_RETRY_DELAYS_MS[attempts - 1], lambda u=url: self._retry(u))

    def _retry(self, url: QUrl) -> None:
        if self._connecting_for == url or self.url() == url:
            self._connecting_for = None
            self.load(url)

    # ── popups ───────────────────────────────────────────────────────
    # target=_blank / window.open -> open as a new tab in this window.
    def createWindow(self, _window_type):  # noqa: N802 (Qt API name)
        return self._mw.create_popup_view()

    # ── mouse back/forward buttons ──────────────────────────────────
    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.XButton1:
            self.back()
            event.accept()
            return
        if event.button() == Qt.MouseButton.XButton2:
            self.forward()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ── context menu ─────────────────────────────────────────────────
    def contextMenuEvent(self, event):  # noqa: N802
        page = self.page()
        if page.url().path() == "/terminal":
            # The terminal tab renders its own in-page Copy/Paste menu — its
            # selection lives in xterm.js, which Qt's selectedText can't see.
            return
        request = self.lastContextMenuRequest()
        menu = QMenu(self)

        link_url = request.linkUrl() if request else QUrl()
        selected = (request.selectedText() or "").strip() if request else ""
        media_url = request.mediaUrl() if request else QUrl()

        if link_url.isValid() and not link_url.isEmpty():
            menu.addAction(
                "Open Link in New Tab",
                lambda: self._mw.open_in_new_tab(link_url),
            )
            menu.addAction(
                "Open Link in New Window",
                lambda: self._mw._app.new_window(url=link_url),
            )
            menu.addAction(
                "Copy Link Address",
                lambda: QGuiApplication.clipboard().setText(link_url.toString()),
            )
            menu.addAction(
                "Copy Link as Markdown",
                lambda: QGuiApplication.clipboard().setText(
                    f"[{page.title()}]({link_url.toString()})"
                ),
            )
            menu.addAction(
                "Save Link As…",
                lambda: page.triggerAction(QWebEnginePage.WebAction.DownloadLinkToDisk),
            )
            menu.addSeparator()

        back = menu.addAction("Back", self.back)
        back.setEnabled(self.history().canGoBack())
        fwd = menu.addAction("Forward", self.forward)
        fwd.setEnabled(self.history().canGoForward())
        menu.addAction("Reload", self.reload)
        menu.addSeparator()

        if selected:
            menu.addAction("Copy", lambda: page.triggerAction(QWebEnginePage.WebAction.Copy))
            label = selected if len(selected) <= 24 else selected[:24] + "…"
            menu.addAction(f'Search for "{label}"', lambda: self._mw.search_for(selected))
            ai_menu = menu.addMenu("AI")
            for action_label, instruction in (
                ("Explain this", "Explain this clearly:"),
                ("Summarize this", "Summarize this:"),
                ("Translate to English", "Translate this to English:"),
            ):
                ai_menu.addAction(
                    action_label,
                    lambda i=instruction, s=selected: (self._mw.ai_sidebar.ask_about(i, s)),
                )
            menu.addSeparator()

        if media_url.isValid() and not media_url.isEmpty():
            menu.addAction(
                "Open Media in New Tab",
                lambda: self._mw.open_in_new_tab(media_url),
            )
            menu.addAction(
                "Save Media As…",
                lambda: page.triggerAction(QWebEnginePage.WebAction.DownloadImageToDisk),
            )
            menu.addSeparator()

        menu.addAction("Inspect Element", self._mw.open_devtools)
        menu.exec(event.globalPos())
