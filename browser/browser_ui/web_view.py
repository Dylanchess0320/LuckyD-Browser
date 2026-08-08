"""QWebEngineView subclass: popups become tabs, custom context menu."""

from __future__ import annotations

import contextlib
import json
from urllib.parse import quote

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

# The offline page doubles as a tiny arcade: a canvas endless-runner while
# you wait for the network (Chrome-dino homage, LuckyD neon skin).
_OFFLINE_HTML = """<!doctype html><html><head><meta charset='utf-8'><style>
body{background:#1b1d23;color:#cfd3dc;font:15px system-ui;margin:0;
  display:flex;min-height:100vh;align-items:center;justify-content:center}
.wrap{text-align:center;max-width:560px;padding:20px}
.dim{color:#7a7f8a;font-size:12px}
canvas{background:#12141a;border:1px solid #2a2f3a;border-radius:12px;
  margin-top:18px;cursor:pointer;display:block}
.hint{color:#5b6470;font-size:11px;margin-top:8px}
b.score{color:#5b9dff}
</style></head><body><div class='wrap'>
<div style='font-size:34px'>⚠</div>
<p>Still can't reach this site.</p>
<p class='dim'>Check your connection, firewall, or VPN — then press F5 to try again.</p>
<canvas id='g' width='520' height='150'></canvas>
<div class='hint'>meanwhile… <b>SPACE / click</b> to hop · best: <b class=score id=best>0</b></div>
<script>
const c=document.getElementById('g'),x=c.getContext('2d');
const W=c.width,H=c.height,G=H-24;
let dino={x:46,y:G,w:22,h:26,vy:0,air:false},obs=[],t=0,score=0,best=0,speed=4.2,dead=false;
function hop(){if(dead){reset();return;}if(!dino.air){dino.vy=-9.4;dino.air=true;}}
addEventListener('keydown',e=>{if(e.code==='Space'){e.preventDefault();hop();}});
c.addEventListener('pointerdown',hop);
function reset(){obs=[];t=0;score=0;speed=4.2;dead=false;dino.y=G;dino.vy=0;}
function spawn(){const h=14+Math.random()*22;obs.push({x:W+10,w:10+Math.random()*10,h:h,y:G+26-h});}
function tick(){
 t++;speed+=0.0012;
 // physics
 dino.y+=dino.vy;dino.vy+=0.5;
 if(dino.y>=G){dino.y=G;dino.vy=0;dino.air=false;}
 // obstacles
 if(t%Math.max(38,70-Math.floor(speed*3))===0||t===1)spawn();
 for(const o of obs)o.x-=speed;
 obs=obs.filter(o=>o.x>-30);
 // collision
 for(const o of obs){
  if(dino.x<o.x+o.w&&dino.x+dino.w>o.x&&dino.y+26>o.y){dead=true;best=Math.max(best,Math.floor(score));document.getElementById('best').textContent=best;}
 }
 if(!dead)score+=speed/60;
 // draw
 x.clearRect(0,0,W,H);
 x.strokeStyle='#2a2f3a';x.beginPath();x.moveTo(0,G+27);x.lineTo(W,G+27);x.stroke();
 x.fillStyle=dead?'#ff5b6e':'#5b9dff';
 x.fillRect(dino.x,dino.y,dino.w,26);
 x.fillStyle='#0b0f16';x.fillRect(dino.x+14,dino.y+6,4,4); // eye
 x.fillStyle='#f9a24f';
 for(const o of obs)x.fillRect(o.x,o.y,o.w,o.h);
 x.fillStyle='#7a7f8a';x.font='11px system-ui';
 x.fillText((dead?'💀 press SPACE to retry · ':'')+Math.floor(score),8,16);
 requestAnimationFrame(tick);
}
tick();
</script></div></body></html>"""


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
    def wheelEvent(self, event):  # noqa: N802
        # Ctrl + wheel = zoom (standard browser muscle memory).
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.angleDelta().y() > 0:
                self._mw.zoom_in()
            else:
                self._mw.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)

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

        # Spell-check suggestions sit on top, like every pro browser.
        if request is not None:
            with contextlib.suppress(Exception):
                if request.misspelledWord():
                    for suggestion in request.spellCheckerSuggestions()[:4]:
                        menu.addAction(
                            f"✓ {suggestion}",
                            lambda s=suggestion: page.replaceMisspelledWord(s),
                        )
                    menu.addSeparator()

        if link_url.isValid() and not link_url.isEmpty():
            menu.addAction(
                "Open Link in New Tab",
                lambda: self._mw.open_in_new_tab(link_url),
            )
            menu.addAction(
                "Open Link in Side Pane",
                lambda: self._mw.open_in_side_pane(link_url),
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
            menu.addAction(
                "Copy Link to Highlighted Text",
                lambda: QGuiApplication.clipboard().setText(
                    page.url().toString() + "#:~:text=" + quote(selected[:300], safe="")
                ),
            )
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

        page_url = page.url().toString()
        if page_url.startswith(("http://", "https://")):
            menu.addAction(
                "Translate Page…",
                lambda: self._mw.open_in_new_tab(
                    QUrl(
                        "https://translate.google.com/translate?sl=auto&tl=en&u="
                        + quote(page_url, safe="")
                    )
                ),
            )
            menu.addSeparator()

        menu.addAction("Inspect Element", self._mw.open_devtools)
        menu.exec(event.globalPos())
