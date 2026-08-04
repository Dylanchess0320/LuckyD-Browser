"""Automated functional smoke test for LuckyD Browser (dev tool, not shipped).

Launches the real app, drives it with timers, prints PASS/FAIL per check,
and exits non-zero if any check fails. Currently runs 30 checks.

Run:  python browser/selftest.py
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
for path in (BASE.parent, BASE):
    entry = str(path)
    if entry in sys.path:
        sys.path.remove(entry)
    sys.path.insert(0, entry)

from browser_app import BrowserApp
from PySide6 import QtWebEngineWidgets  # noqa: F401  (before QApplication)
from PySide6.QtCore import QTimer, QUrl

RESULTS: list[tuple[str, bool, str]] = []
API_RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}: {name} {detail}")


# offline AI / FMHY / agent checks (no GUI needed, runs before app)
from browser_core.agent import parse_action
from browser_core.ai_bridge import AIBridge
from browser_core.fmhy import parse_markdown

bridge = AIBridge()
providers = bridge.providers()
check("bridge detects deepseekkey", "deepseek" in providers, str(providers))

from browser_core import control_server
from browser_core.harness_bridge import HarnessBridge, _find_exe

check(
    "control API module ready",
    bool(control_server.API_VERSION) and hasattr(control_server, "QtBrowserBackend"),
    control_server.API_VERSION,
)
_hb = HarnessBridge()
check("harness bridge targets exe port", _hb.base.endswith(":8000"), _hb.base)
check("harness exe discoverable", _find_exe() is not None)

# vision capability heuristics + multimodal request bodies
probe = AIBridge.__new__(AIBridge)
probe._configs = {
    "g": ("gemini-2.5-pro", "", "", "gemini"),
    "d": ("deepseek-v4-flash", "", "", "openai"),
    "l": ("gemma3:4b", "", "", "openai"),
    "t": ("llama3.2", "", "", "openai"),
    "k": ("cline-pass/kimi-k3", "", "", "openai"),
}
check("vision detect gemini", probe.supports_vision("g"))
check("vision detect local gemma3", probe.supports_vision("l"))
check("vision reject deepseek", not probe.supports_vision("d"))
check(
    "vision reject text-only models",
    not probe.supports_vision("t") and not probe.supports_vision("k"),
)
_mm = [
    {
        "role": "user",
        "content": [
            {"type": "text", "text": "hi"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}},
        ],
    }
]
_g = AIBridge._body_gemini(_mm)
check(
    "gemini body keeps image",
    _g["contents"][0]["parts"][-1].get("inlineData", {}).get("data") == "QUJD",
)
_a = AIBridge._body_anthropic(_mm)
check(
    "anthropic body keeps image",
    _a["messages"][0]["content"][-1].get("type") == "image",
)

a = parse_action('{"action":"click","index":3}')
check(
    "agent parse clicked",
    a and a[0].get("action") == "click" and a[0]["index"] == 3,
)

entries = parse_markdown("* **[Foo](https://foo.com) - a thing\n", "AI")
check("fmhy markdown parse", entries[0]["name"] == "Foo" and entries[0]["url"] == "https://foo.com")

# markdown-lite chat renderer (sidebar bubbles)
from browser_ui.ai_sidebar import _md_lite

_html = _md_lite("**bold** and `code`\n\n- one\n- two\n\n```py\nprint(1)\n```")
check("md bold", "<b>bold</b>" in _html)
check("md inline code", "<code" in _html and "code</code>" in _html)
check("md bullets", "<li>one</li>" in _html and "<li>two</li>" in _html)
check("md fenced block", "<pre" in _html and "print(1)" in _html)
check("md escapes html", "<script>" not in _md_lite("<script>alert(1)</script>"))

# package metadata (mojibake regression guard)
import browser as _browser_pkg

check("version 1.3.0", _browser_pkg.__version__ == "1.3.0", _browser_pkg.__version__)
check("docstring has no mojibake", "�" not in (_browser_pkg.__doc__ or ""))

# dashboard module served by the Control API
from browser_core.dashboard import DASHBOARD_HTML, hq_splash_html

check(
    "dashboard html well-formed",
    DASHBOARD_HTML.startswith("<!DOCTYPE html>")
    and DASHBOARD_HTML.rstrip().endswith("</html>")
    and 'id="apps"' in DASHBOARD_HTML
    and "ask-box" not in DASHBOARD_HTML,
)
check(
    "hq splash states",
    "Starting your coding agent" in hq_splash_html("u", "starting")
    and "unavailable" in hq_splash_html("u", "error", "x"),
)


def _api_checks(port: int) -> None:
    """Hit the live Browser Control API from a worker thread (GUI stays free)."""
    base = f"http://127.0.0.1:{port}"

    def get(path):
        with urllib.request.urlopen(base + path, timeout=10) as r:
            return json.loads(r.read())

    def post(path, payload):
        req = urllib.request.Request(
            base + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())

    try:
        s = get("/status")
        API_RESULTS.append(
            (
                "control API /status",
                s.get("ok") is True and s.get("tabs", 0) >= 1,
                str(s.get("current_url")),
            )
        )
    except Exception as exc:
        API_RESULTS.append(("control API /status", False, str(exc)))
    try:
        e = post("/eval", {"js": "1+1"})
        API_RESULTS.append(("control API /eval", e.get("result") == 2, repr(e.get("result"))))
    except Exception as exc:
        API_RESULTS.append(("control API /eval", False, str(exc)))
    try:
        snap = post("/snapshot", {})
        url = (snap.get("snapshot") or {}).get("url", "")
        API_RESULTS.append(("control API /snapshot", "example.com" in url, url))
    except Exception as exc:
        API_RESULTS.append(("control API /snapshot", False, str(exc)))
    try:
        tabs = get("/tabs")
        API_RESULTS.append(
            (
                "control API /tabs",
                any(t.get("active") for t in tabs.get("tabs", [])),
                "",
            )
        )
    except Exception as exc:
        API_RESULTS.append(("control API /tabs", False, str(exc)))
    try:
        with urllib.request.urlopen(base + "/dashboard", timeout=10) as r:
            dash = r.read().decode("utf-8", errors="replace")
        API_RESULTS.append(
            (
                "control API /dashboard",
                "Coding Agent" in dash and "ask-box" not in dash,
                "",
            )
        )
    except Exception as exc:
        API_RESULTS.append(("control API /dashboard", False, str(exc)))
    try:
        # urllib follows the 302 to the exe UI when the harness is up;
        # otherwise the auto-start splash is served — both say "Coding Agent".
        with urllib.request.urlopen(base + "/hq", timeout=15) as r:
            hq = r.read().decode("utf-8", errors="replace")
        API_RESULTS.append(("control API /hq gateway", "Coding Agent" in hq, ""))
    except Exception as exc:
        API_RESULTS.append(("control API /hq gateway", False, str(exc)))
    try:
        st = get("/status")
        API_RESULTS.append(
            (
                "control API /status enrichment",
                "ai_providers" in st and "harness_url" in st,
                f"providers={st.get('ai_providers')}",
            )
        )
    except Exception as exc:
        API_RESULTS.append(("control API /status enrichment", False, str(exc)))


app = BrowserApp(sys.argv)
win = app.windows[0]


def step1() -> None:
    check("window visible", win.isVisible())
    check("first tab created", win.tabs.count() == 1)
    _first_url = win.tabs.current_view().url()
    check(
        "new-tab page loaded (dashboard or file fallback)",
        _first_url.isLocalFile() or _first_url.toString().endswith("/dashboard"),
        _first_url.toString(),
    )
    win.load_in_current_tab(QUrl("https://example.com"))
    QTimer.singleShot(6000, step2)


def step2() -> None:
    view = win.tabs.current_view()
    url = view.url().toString()
    check(
        "navigated to example.com",
        "example.com" in url and not view.page().isLoading(),
        url,
    )
    check("page title set", bool(view.title()), repr(view.title()))

    history = app.storage.recent(10)
    check("history recorded", any("example.com" in row[0] for row in history))

    win.toggle_bookmark()
    check("bookmark added", app.storage.is_bookmarked(url))
    win.toggle_bookmark()
    check("bookmark removed", not app.storage.is_bookmarked(url))

    check(
        "omnibox URL detection",
        win.omnibox.to_url("github.com").toString() == "https://github.com",
    )
    check(
        "omnibox search detection",
        "google.com/search" in win.omnibox.to_url("hello world").toString(),
    )

    check(
        "harness mode default on",
        win.ai_sidebar.harness_box.isChecked(),
    )
    check(
        "harness supervisor wired",
        hasattr(app, "harness") and app.harness.status().get("url", "").endswith(":8000"),
        app.harness.status().get("url", "") if hasattr(app, "harness") else "missing",
    )
    check(
        "sidebar harness status widget",
        win.ai_sidebar.harness_status.text() != "",
    )
    _dp = win.ai_sidebar.bridge.default_provider()
    _expect = win.ai_sidebar.bridge.supports_vision(_dp)
    check(
        "vision auto-sync matches default provider",
        win.ai_sidebar.vision_box.isChecked() == _expect,
        f"{_dp} -> vision={_expect}",
    )
    win.new_tab()
    check("second tab opens", win.tabs.count() == 2)
    win.close_current_tab()
    check("tab closes", win.tabs.count() == 1)

    port = int(app.settings.get("browser_api_port", 9777))
    api_thread = threading.Thread(target=_api_checks, args=(port,), daemon=True)
    api_thread.start()
    QTimer.singleShot(1000, lambda: finish(api_thread))


def finish(api_thread=None) -> None:
    if api_thread is not None:
        api_thread.join(timeout=20)
        for name, ok, detail in API_RESULTS:
            check(name, ok, detail)
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    app.qapp.exit(1 if failed else 0)


QTimer.singleShot(2500, step1)
sys.exit(app.run())
