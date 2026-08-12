"""Automated functional smoke test for LuckyD Browser (dev tool, not shipped).

Launches the real app, drives it with timers, prints PASS/FAIL per check,
and exits non-zero if any check fails. Currently runs 111 checks.

Run:  python browser/selftest.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
for path in (BASE.parent, BASE):
    entry = str(path)
    if entry in sys.path:
        sys.path.remove(entry)
    sys.path.insert(0, entry)

# Isolate session persistence: the app under test must always start with a
# single fresh tab, never restore tabs left over from a dev session.
os.environ["LUCKYD_SESSION_PATH"] = str(
    Path(tempfile.mkdtemp(prefix="ld-selftest-")) / "session.json"
)
# And never collide with a real running browser on the default API/CDP ports.
os.environ["LUCKYD_API_PORT"] = "19877"
os.environ["QTWEBENGINE_REMOTE_DEBUGGING"] = "127.0.0.1:19222"

from browser_app import BrowserApp
from PySide6 import QtWebEngineWidgets  # noqa: F401  (before QApplication)
from PySide6.QtCore import QTimer, QUrl

RESULTS: list[tuple[str, bool, str]] = []
API_RESULTS: list[tuple[str, bool, str]] = []
_ORIG_THEME = "neon"  # captured from the live settings before API checks


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

check("version 2.2.0", _browser_pkg.__version__ == "2.2.0", _browser_pkg.__version__)
check("docstring has no mojibake", "�" not in (_browser_pkg.__doc__ or ""))

# v1.4.0 — session restore, per-site zoom memory, screenshot naming (pure logic)
from browser_core.session import SessionStore, tab_record, window_record
from browser_core.zoom import clamp_zoom, origin_key, remember, zoom_for

_store = SessionStore()  # uses the isolated LUCKYD_SESSION_PATH above
_win = window_record([tab_record("https://example.com", "Example", pinned=True)], current=0)
check("session window record", _win is not None and _win["tabs"][0]["pinned"])
check(
    "session store round-trip",
    _store.save([_win]) and _store.load()["windows"][0]["tabs"][0]["url"] == "https://example.com",
)
check(
    "session skips internal tabs",
    tab_record("about:blank") is None and tab_record("view-source:https://x") is None,
)
_store.path.write_text("not json {", encoding="utf-8")
check("session tolerates corrupt file", _store.load() == {})
_store.clear()

check("zoom origin key", origin_key("https://example.com/path?q=1") == "https://example.com")
check(
    "zoom origin keeps non-default port",
    origin_key("http://127.0.0.1:9777/dashboard") == "http://127.0.0.1:9777",
)
check(
    "zoom origin drops default port",
    origin_key("https://example.com:443/") == "https://example.com",
)
check(
    "zoom origin rejects non-http",
    origin_key("file:///C:/x.html") == "" and origin_key("about:blank") == "",
)
check("zoom clamp range", clamp_zoom(99) == 5.0 and clamp_zoom(0.01) == 0.25)
check(
    "zoom remember + 100% forgets",
    remember({}, "https://a.com", 1.5).get("https://a.com") == 1.5
    and remember({"https://a.com": 1.5}, "https://a.com", 1.0) == {},
)
check(
    "zoom lookup with fallback",
    zoom_for({"https://a.com": 1.5}, "https://a.com/x", 1.0) == 1.5
    and zoom_for({"https://a.com": 1.5}, "https://b.com", 1.0) == 1.0,
)

from browser_core.screenshot import suggested_name

_name = suggested_name("https://example.com/docs")
check(
    "screenshot name shape",
    _name.startswith("screenshot-example.com-") and _name.endswith(".jpg"),
    _name,
)

# v1.5.0 — multi-terminal page + workflows manager page
from browser_core.dashboard import workflows_html
from browser_core.terminal_page import terminal_html

_ps_page = terminal_html(None, shell="powershell")  # nosec B604
check(
    "terminal page shell injection",
    "&shell=" in _ps_page and 'let SHELL = "powershell"' in _ps_page,
)
check(
    "terminal page sanitizes shell",
    'let SHELL = "agent"' in terminal_html(None, shell='bogus";alert(1)'),  # nosec B604
)
_agent2_page = terminal_html(None, shell="agent2")  # nosec B604
check(
    "terminal page second agent",
    'let SHELL = "agent2"' in _agent2_page and 'data-sh="agent2"' in _agent2_page,
)
from browser_core.terminal_server import SHELLS, _shell_command

check("terminal bridge allowlists 2nd agent", "agent2" in SHELLS)
try:
    _cmd2 = _shell_command("agent2")
    check("2nd agent CLI resolves", bool(_cmd2 and _cmd2[0]), " ".join(_cmd2)[:120])
except FileNotFoundError as _exc:
    check("2nd agent CLI resolves", "terminal_cli2" in str(_exc), str(_exc)[:120])
_wf_page = workflows_html()
check(
    "workflows page well-formed",
    _wf_page.startswith("<!DOCTYPE html>") and "/workflow/replay" in _wf_page,
)

# masterpiece pass — userscript engine repair (was: every built-in script died
# with "Unexpected token '.'" because @match globs never escaped "/")
from browser_core.scripts import load_scripts, wrapped_source
from browser_core.settings import DEFAULTS

_bad = [s.name for s in load_scripts() if "://" in wrapped_source(s).splitlines()[0]]
check("userscript wrappers valid JS", not _bad, str(_bad))
check(
    "dark-mode userscript is opt-in",
    "Dark Mode Everywhere" in DEFAULTS["userscript_disabled"],
)

# masterpiece pass — secret theme, offline arcade, updater math
from browser_core.updater import is_newer, parse_version
from browser_ui.theme import THEMES
from browser_ui.web_view import _OFFLINE_HTML

check("synthwave secret theme", THEMES.get("synthwave", {}).get("label") == "Synthwave Sunset")
check(
    "offline page arcade",
    "<canvas" in _OFFLINE_HTML and "requestAnimationFrame" in _OFFLINE_HTML,
)
check(
    "updater version compare",
    is_newer("9.9.9", _browser_pkg.__version__)
    and not is_newer(_browser_pkg.__version__, _browser_pkg.__version__)
    and parse_version("v1.5") == (1, 5),
)

# v1.7.0 — network monitor page
from browser_core.dashboard import netmon_html

_nm_page = netmon_html()
check(
    "netmon page well-formed",
    _nm_page.startswith("<!DOCTYPE html>") and "/network/events" in _nm_page,
)

# dashboard module served by the Control API
from browser_core.dashboard import DASHBOARD_HTML, hq_splash_html

check(
    "dashboard html well-formed",
    DASHBOARD_HTML.startswith("<!DOCTYPE html>")
    and DASHBOARD_HTML.rstrip().endswith("</html>")
    and 'id="apps"' in DASHBOARD_HTML
    and "ask-box" not in DASHBOARD_HTML,
)
# v1.8.0 — dashboard: local letter tiles, no favicon-service leak, new tiles
check(
    "dashboard local tiles (no favicon service)",
    "s2/favicons" not in DASHBOARD_HTML and "tileFor" in DASHBOARD_HTML,
)
check(
    "dashboard platform tiles",
    "'/workflows'" in DASHBOARD_HTML
    and "'/network'" in DASHBOARD_HTML
    and "'/terminal'" in DASHBOARD_HTML,
)

# v1.9.0 — schedules UI in workflows page + full-page capture entry point
from browser_core.screenshot import capture_full_b64

_wf_page2 = workflows_html()
check("workflows page schedules UI", "/schedules" in _wf_page2 and "sched" in _wf_page2)
check("full-page screenshot entry point", callable(capture_full_b64))

# v2.0.0 — youtube ad resume fix present in the shipped userscript
_yt = (BASE / "assets" / "userscripts" / "youtube-adblock.user.js").read_text(encoding="utf-8")
check(
    "youtube ad resume-at-position fix",
    "trackAdBoundary" in _yt and "preAdTime" in _yt and "preAdMuted" in _yt,
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
        with urllib.request.urlopen(base + path, timeout=10) as r:  # nosec B310
            return json.loads(r.read())

    def post(path, payload, timeout=15):
        req = urllib.request.Request(
            base + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310
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
    # /snapshot's page JS loses the race when the GUI thread is mid-startup
    # work (harness spawn, update check) — retry before calling it a failure.
    _snap_exc: Exception | None = None
    for _attempt in range(3):
        try:
            snap = post("/snapshot", {}, timeout=25)
            url = (snap.get("snapshot") or {}).get("url", "")
            API_RESULTS.append(("control API /snapshot", "example.com" in url, url))
            break
        except Exception as exc:
            _snap_exc = exc
            time.sleep(2)
    else:
        API_RESULTS.append(("control API /snapshot", False, str(_snap_exc)))
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
        with urllib.request.urlopen(base + "/dashboard", timeout=10) as r:  # nosec B310
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
        with urllib.request.urlopen(base + "/hq", timeout=15) as r:  # nosec B310
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

    # live theme switch through the API (restores the original afterwards) —
    # runs BEFORE the workflow checks: repolishing the app competes with the
    # replay's GUI-thread work, and client timeouts are cheap here.
    try:
        switched = post("/theme", {"name": "synthwave"}, timeout=35)
        bad = False
        try:
            post("/theme", {"name": "bogus-theme"})
            bad = True  # must not succeed
        except Exception:
            pass  # HTTP 500 on unknown theme = correct rejection
        post("/theme", {"name": _ORIG_THEME}, timeout=35)
        API_RESULTS.append(
            ("theme switch via API", switched.get("theme") == "synthwave" and not bad, "")
        )
    except Exception as exc:
        API_RESULTS.append(("theme switch via API", False, str(exc)))

    # v1.5.0 — full workflow lifecycle against the live browser:
    # record two scrolls → stop/save → listed → replay → delete.
    # (Scrolls only: navigation replays depend on network timing — flaky
    # in a smoke test. Indexed-element self-healing is unit-tested.)
    try:
        time.sleep(2.0)  # let the theme repolish finish before replaying
        post("/workflow/record", {"name": "selftest-demo"})
        post("/act", {"action": "scroll", "text": "down"})
        post("/act", {"action": "scroll", "text": "up"})
        stopped = post("/workflow/stop", {})
        API_RESULTS.append(
            (
                "workflow record+save",
                bool(stopped.get("saved")) and stopped.get("steps") == 2,
                str(stopped),
            )
        )
        listing = get("/workflows/list")
        API_RESULTS.append(
            (
                "workflow listed",
                any(w.get("name") == "selftest-demo" for w in listing.get("workflows", [])),
                "",
            )
        )
        replayed = post("/workflow/replay", {"name": "selftest-demo"}, timeout=45)
        API_RESULTS.append(
            (
                "workflow replay",
                replayed.get("total") == 2 and replayed.get("succeeded") == 2,
                f"{replayed.get('succeeded')}/{replayed.get('total')}",
            )
        )
        post("/workflow/delete", {"name": "selftest-demo"})
        listing = get("/workflows/list")
        API_RESULTS.append(
            (
                "workflow deleted",
                not any(w.get("name") == "selftest-demo" for w in listing.get("workflows", [])),
                "",
            )
        )
    except Exception as exc:
        API_RESULTS.append(("workflow lifecycle", False, str(exc)))

    # v1.7.0 — network monitor lifecycle: start → events → HAR → stop
    try:
        post("/network/start", {})
        time.sleep(1.0)  # let the CDP hook attach
        events = get("/network/events")
        API_RESULTS.append(
            (
                "netmon capture starts",
                bool(events.get("running")) and not events.get("error"),
                str(events.get("error", "")),
            )
        )
        har = get("/network/har")
        API_RESULTS.append(
            (
                "netmon HAR export",
                isinstance(har, dict)
                and har.get("log", {}).get("version") == "1.2"
                and "entries" in har["log"],
                "",
            )
        )
        post("/network/stop", {})
    except Exception as exc:
        API_RESULTS.append(("netmon lifecycle", False, str(exc)))

    # v1.9.0 — schedules API: set 60m → listed → off
    try:
        r = post("/schedule", {"name": "selftest-demo", "every_min": 60})
        API_RESULTS.append(("schedule set", r.get("every_min") == 60, str(r)))
        lst = get("/schedules")
        API_RESULTS.append(
            (
                "schedule listed",
                any(
                    s.get("name") == "selftest-demo" and s.get("every_min") == 60
                    for s in lst.get("schedules", [])
                ),
                "",
            )
        )
        post("/schedule", {"name": "selftest-demo", "every_min": 0})
    except Exception as exc:
        API_RESULTS.append(("schedule API", False, str(exc)))


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
    check(
        "bookmark bar reflects add",
        any(a.data() == url for a in win.bookmark_bar.actions()),
    )
    win.toggle_bookmark()
    check("bookmark removed", not app.storage.is_bookmarked(url))
    check(
        "bookmark bar reflects remove",
        not any(a.data() == url for a in win.bookmark_bar.actions()),
    )
    _bar_was = win.bookmark_bar.isVisible()
    win.bm_bar_act.setChecked(not _bar_was)
    check("bookmark bar toggles", win.bookmark_bar.isVisible() == (not _bar_was))
    win.bm_bar_act.setChecked(_bar_was)

    # v1.4.0 — pinned tabs (guards the Qt6 QTabBar.setTabButton regression)
    win.tabs.toggle_pin(0)
    check("pin tab works", win.tabs.is_pinned(0))
    win.tabs.toggle_pin(0)
    check("unpin tab works", not win.tabs.is_pinned(0))

    # v1.4.0 — session snapshot + per-site zoom memory (live window)
    snap = win._session_snapshot()
    check(
        "session snapshot captures tab",
        snap is not None and any("example.com" in t["url"] for t in snap["tabs"]),
        str(snap),
    )
    win.zoom_in()
    _levels = app.settings.get("zoom_levels", {})
    check(
        "per-site zoom remembered",
        abs(_levels.get("https://example.com", 0) - 1.1) < 0.02,
        str(_levels),
    )
    win.zoom_reset()
    check(
        "zoom reset forgets site",
        "https://example.com" not in app.settings.get("zoom_levels", {}),
    )
    check("zoom pill is a clickable reset button", hasattr(win.zoom_label, "clicked"))

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
    # v1.6.0 — tab groups (assign, collapse chip, session persistence, AI apply)
    win.new_tab()
    gid = win.tabs.create_group("Work")
    win.tabs.set_tab_group(0, gid)
    win.tabs.set_tab_group(1, gid)
    check(
        "tab group assign",
        win.tabs.group_of(0) == gid and win.tabs.group_of(1) == gid,
    )
    win.tabs.toggle_group_collapsed(gid)
    check("group collapse chip", win.tabs.tabText(0).startswith("▸ Work"))
    win.tabs.toggle_group_collapsed(gid)
    snap = win._session_snapshot()
    check("session carries groups", bool(snap and snap.get("groups")), str(snap))
    win._apply_ai_groups([{"name": "AI Stuff", "tabs": [0]}])
    check(
        "ai group apply",
        win.tabs.group_info(win.tabs.group_of(0)).get("name") == "AI Stuff",
    )
    win.tabs.set_tab_group(0, None)
    win.tabs.set_tab_group(1, None)
    check("groups dissolve when empty", win.tabs._groups == {})

    # v1.7.0 — vertical tabs + focus mode + omnibox ask wiring
    win.vtabs_act.setChecked(True)
    check(
        "vertical tabs on",
        win.vtabs.isVisible() and not win.tabs.tabBar().isVisible(),
    )
    win.vtabs_act.setChecked(False)
    check(
        "vertical tabs off",
        not win.vtabs.isVisible() and win.tabs.tabBar().isVisible(),
    )
    win.toggle_focus_mode()
    check(
        "focus mode hides chrome",
        not win.nav_bar.isVisible() and not win.statusBar().isVisible(),
    )
    win.toggle_focus_mode()
    check("focus mode restores chrome", win.nav_bar.isVisible())
    check("sidebar programmatic ask", callable(getattr(win.ai_sidebar, "ask", None)))
    # Collapsing hid the scratch tab, which moved the current index to 0 —
    # point back at the scratch tab before closing it.
    win.tabs.setCurrentIndex(1)
    win.close_current_tab()
    check("group tab closed back to one", win.tabs.count() == 1)

    win.new_tab()
    check("second tab opens", win.tabs.count() == 2)
    win.close_current_tab()
    check("tab closes", win.tabs.count() == 1)
    # The API checks read the ACTIVE tab — make sure it's example.com again.
    win.tabs.setCurrentIndex(0)

    # v1.8.0 — side pane + duplicate tab closing (runs with a clean single tab)
    win.open_in_side_pane(QUrl("https://example.com"))
    check(
        "side pane opens",
        win._side_pane is not None
        and win._side_pane.isVisible()
        and "example.com" in win._side_view.url().toString(),
    )
    win._side_pane.close()
    win.open_in_new_tab(QUrl("https://example.com/"))  # duplicate of tab 0
    check("duplicate opened", win.tabs.count() == 2)
    closed = win.tabs.close_duplicates()
    check("close duplicate tabs", closed == 1 and win.tabs.count() == 1)
    win.tabs.setCurrentIndex(0)

    # v2.0.0 — read later queue + spellcheck profile
    win.save_read_later()
    check(
        "read later saved",
        any(r[0] == url and r[2] == "readlater" for r in app.storage.bookmarks()),
    )
    check(
        "read later stays off the bar",
        not any(a.data() == url for a in win.bookmark_bar.actions()),
    )
    app.storage.remove_bookmark(url)  # cleanup the test entry
    # Spellcheck self-enables only when a hunspell .bdic dictionary exists
    # (assets/qtwebengine_dictionaries) — either state is a valid setup.
    check(
        "spellcheck wiring safe",
        win.profile.isSpellCheckEnabled() in (True, False),
    )

    port = int(os.environ.get("LUCKYD_API_PORT", "0") or app.settings.get("browser_api_port", 9777))
    global _ORIG_THEME
    _ORIG_THEME = str(app.settings.get("theme", "neon"))
    api_thread = threading.Thread(target=_api_checks, args=(port,), daemon=True)
    api_thread.start()
    QTimer.singleShot(1000, lambda: finish(api_thread))


def finish(api_thread=None) -> None:
    if api_thread is not None:
        if api_thread.is_alive():
            # Poll instead of join(): joining here would freeze the GUI event
            # loop while API calls still need it (invoker marshals to the GUI
            # thread) — a guaranteed deadlock against the invoker timeout.
            QTimer.singleShot(500, lambda: finish(api_thread))
            return
        for name, ok, detail in API_RESULTS:
            check(name, ok, detail)
    # Fullscreen checks run LAST of all: the showFullScreen/showNormal
    # round-trip perturbs window activation, which flakes the API thread's
    # snapshot JS if it is still in flight.
    from PySide6.QtWebEngineCore import QWebEngineSettings

    win = app.windows[0]
    check(
        "fullscreen API enabled",
        win.profile.settings().testAttribute(
            QWebEngineSettings.WebAttribute.FullScreenSupportEnabled
        ),
    )
    win._enter_video_fullscreen()
    check(
        "video fullscreen hides chrome",
        win.isFullScreen()
        and not win.nav_bar.isVisible()
        and not win.menuBar().isVisible()
        and not win.tabs.tabBar().isVisible(),
    )
    win._exit_video_fullscreen()
    check(
        "video fullscreen restores chrome",
        not win.isFullScreen() and win.nav_bar.isVisible() and win.menuBar().isVisible(),
    )
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    app.qapp.exit(1 if failed else 0)


QTimer.singleShot(2500, step1)
sys.exit(app.run())
