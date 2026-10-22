"""Pass-3 fixes + Features 12/13 (2026-10-22) — Dylan's 10.8.0 test feedback.

Bug 1  agent-task runs dead-ended when the coding-agent backend was down
       (no fallback) — the sidebar now falls back to the built-in agent.
Bug 2  shortcuts dialog overflowed (QMessageBox.about) — now a capped,
       scrolling, palette-built QDialog.
Bug 3  logo rendered as a square — chroma-key transparency, verified by
       decoding the shipped PNG/ICO assets with the stdlib.
Bug 4  ALL tip/hint text removed from the homepage.
Bug 5  blank strip above web content — the tab pages stack is a direct
       row of the window container now (no reserved tab-bar height).
Bug 6  dropdown-arrow glyphs removed (AI Assistant Actions, Settings combos).
Bug 7  Enter in the AI Assistant input submits (returnPressed wiring).
Bug 8  homepage tiles: one uniform theme treatment, no per-tile colors.
Bug 9  "thinking…" + provider attribution live INSIDE the transcript.
Bug 10 the + button hugs the last tab in the strip (and in the fallback).
Bug 11 Agent Mesh → Multi Agent Terminal: embed-mode panes + focus mode.
F12    homepage animation themes (aurora/particles/parallax/off).
F13    LuckyD Chat inside the browser: Hub chat + Claw tasks via the
       control server's proxy routes; tokens from Settings → Connections.

Qt chrome is asserted at source level (suite convention — PySide6 is not
installed in CI); the real-Qt geometry/behavior proofs were run in a
real-Qt environment during development and are recorded in the report.
Everything Qt-free (dashboard, mesh page, chat page, services client,
control-server routes, icon assets) is tested functionally here.
"""

from __future__ import annotations

import ast
import http.client
import io
import json
import re
import struct
import sys
import threading
import urllib.error
import urllib.request
import zlib
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))


# ── Qt stubs: real QObject/Signal when the ambient ones aren't types ──
# (Same defensive pattern as test_cov_control_server.py: subclassing a
# MagicMock silently degrades, so GuiInvoker & friends need real bases.
# Never clobber a real stub another module already installed.)
class _QtObjectStub:
    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return MagicMock(name=f"qtstub.{name}")


class _FakeSignal:
    def __init__(self, *types):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in list(self._slots):
            slot(*args)


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
_QTCORE = sys.modules["PySide6.QtCore"]
if not isinstance(getattr(_QTCORE, "QObject", None), type):
    _QTCORE.QObject = _QtObjectStub
    _QTCORE.Signal = lambda *types: _FakeSignal(*types)


def _src(rel: str) -> str:
    return (_BROWSER_DIR / rel).read_text(encoding="utf-8")


def _fake_settings(**values):
    return SimpleNamespace(get=lambda key, default=None: values.get(key, default))


# ── Bugs 1/6/7/9 + the Ask⇄Agent toggle (AI Assistant sidebar) ─────────


def test_sidebar_enter_submits_and_mode_toggle_dispatches() -> None:
    sb = _src("browser_ui/ai_sidebar.py")
    # Bug 7: Enter is wired to the submit dispatcher (pass 2 lost it).
    assert "self.input.returnPressed.connect(self._submit)" in sb
    assert "self.send_btn.clicked.connect(self._submit)" in sb
    # The toggle: two checkable mode buttons, ask by default, one _submit
    # that dispatches by the active mode.
    assert "self.ask_btn" in sb and "self.agent_btn" in sb
    assert 'self._mode = "ask"' in sb
    submit = sb[sb.index("def _submit") : sb.index("def _submit") + 500]
    assert '"agent"' in submit and '"ask"' in submit
    assert "self._start_agent()" in submit
    assert "self._send()" in submit


def test_sidebar_harness_dead_end_falls_back_to_builtin_agent() -> None:
    sb = _src("browser_ui/ai_sidebar.py")
    # Bug 1: the two "backend unavailable" harness results are recognized
    # as dead ends, and both the done and failed paths can fall back to
    # the built-in tab agent — exactly once per task.
    assert "_HARNESS_DEAD_ENDS" in sb
    assert "Coding-agent server isn't responding" in sb
    assert "Coding-agent backend not found" in sb
    assert "def _fallback_to_builtin" in sb
    done = sb[sb.index("def _harness_done") : sb.index("def _harness_done") + 900]
    failed = sb[sb.index("def _harness_failed") : sb.index("def _harness_failed") + 700]
    assert "_fallback_to_builtin" in done
    assert "_fallback_to_builtin" in failed
    fallback = sb[sb.index("def _fallback_to_builtin") : sb.index("def _fallback_to_builtin") + 800]
    assert "_start_builtin_agent" in fallback
    assert "self._agent_fell_back" in fallback  # once-only guard
    # The findings-summary contract is untouched.
    assert "findings summary" in sb


def test_sidebar_status_and_attribution_live_in_transcript() -> None:
    sb = _src("browser_ui/ai_sidebar.py")
    # Bug 9: no status widget exists below the input anymore.
    assert "self.status = QLabel" not in sb
    assert "self.status.setText" not in sb
    # Status is a mutable transcript block; attribution lands on the
    # assistant block itself.
    assert "def _set_status" in sb and "def _clear_status" in sb
    assert '"role": "status"' in sb
    assert 'block["meta"] = f"Answered by {label}"' in sb


def test_sidebar_actions_button_has_no_dropdown_arrow() -> None:
    sb = _src("browser_ui/ai_sidebar.py")
    # Bug 6: plain "Actions" button opening the menu explicitly; Stop and
    # Undo are plain contextual buttons now.
    assert 'self.actions_btn = QPushButton("Actions", body)' in sb
    assert "Actions ▾" not in sb
    assert "self.actions_btn.setMenu" not in sb
    assert "self.stop_btn" in sb and "self.undo_btn" in sb
    assert "Run as agent task" not in sb  # replaced by the mode toggle


# ── Bug 2: shortcuts dialog ────────────────────────────────────────────


def _function_segment(source: str, name: str) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            lines = source.splitlines(keepends=True)
            return "".join(lines[node.lineno - 1 : node.end_lineno])
    raise AssertionError(f"function {name} not found")


def test_shortcuts_dialog_is_capped_scrolling_and_palette_built() -> None:
    mw = _src("browser_ui/main_window.py")
    seg = _function_segment(mw, "show_shortcuts")
    assert ".about(" not in seg  # the old uncapped QMessageBox is gone
    assert "QDialog" in seg and "QTextBrowser" in seg  # scrolling content
    assert "availableGeometry" in seg and "0.9" in seg  # 90% screen cap
    assert "palette(self.settings)" in seg
    # No hardcoded color literals anywhere in the builder.
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", seg)


def test_shortcut_sections_include_screenshot_and_mesh_rename() -> None:
    mw = _src("browser_ui/main_window.py")
    tree = ast.parse(mw)
    sections = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.Assign)
                    and getattr(stmt.targets[0], "id", "") == "_SHORTCUT_SECTIONS"
                ):
                    sections = ast.literal_eval(stmt.value)
    assert sections is not None
    flat = {keys: desc for _title, rows in sections for keys, desc in rows}
    # Bug 4 cross-check: the screenshot shortcut lives here now.
    assert flat.get("Ctrl+Shift+S") == "Save screenshot"
    assert "Multi Agent Terminal" in flat.get("Ctrl+Alt+M", "")
    assert len(flat) >= 40  # the full reference survived the rebuild


# ── Bugs 5 + 10: central layout ────────────────────────────────────────


def test_central_layout_uses_pages_stack_and_hugging_plus() -> None:
    mw = _src("browser_ui/main_window.py")
    central = _function_segment(mw, "_build_central")
    # Bug 5: the QStackedWidget of pages is lifted into the container and
    # the tab-widget shell hidden (it used to reserve its bar's height).
    assert "findChild(QStackedWidget)" in central
    assert "self.tabs.hide()" in central
    strip = _function_segment(mw, "_build_tab_strip")
    bar_add = strip.index("addWidget(tab_bar)")
    # Bug 10: no stretch factor on the bar; the flexible space comes
    # AFTER the + button so it hugs the last tab.
    assert "addWidget(tab_bar, 1)" not in strip
    assert strip.index("addStretch(1)") > bar_add


def test_fallback_plus_position_math() -> None:
    mw = _src("browser_ui/main_window.py")
    tree = ast.parse(mw)
    fn_node = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_plus_x"
    )
    fn_node.decorator_list = []
    ns: dict = {}
    exec(compile(ast.Module(body=[fn_node], type_ignores=[]), "<plus_x>", "exec"), ns)
    plus_x = ns["_plus_x"]
    # Right after the last tab.
    assert plus_x(0, 300, 1280, 28) == 302
    # Capped so it never leaves the tab widget's right edge.
    assert plus_x(0, 1270, 1280, 28) == 1280 - 28 - 2
    # No tabs: starts at the bar's left edge.
    assert plus_x(4, 4, 1280, 28) == 6


# ── Bug 6 (Settings): combo arrows gone from the theme QSS ─────────────


def test_theme_qss_hides_combo_down_arrow() -> None:
    theme = _src("browser_ui/theme.py")
    assert "QComboBox::down-arrow { image: none;" in theme
    assert "QComboBox::drop-down { border: none; width: 0px; }" in theme


# ── Bugs 4 + 8 + Feature 12: the homepage ──────────────────────────────


def test_homepage_has_no_tips_and_no_per_tile_colors() -> None:
    from browser_core.dashboard import dashboard_html

    page = dashboard_html(_fake_settings())
    low = page.lower()
    assert "tip:" not in low and '"tip' not in low
    taglines = re.search(r"const TAGLINES = \[(.*?)\]", page, re.S).group(1)
    assert "tip" not in taglines.lower()
    assert "press ctrl" not in taglines.lower()
    # Bug 8: no HSL/hue-hash per-tile coloring anywhere in the render.
    assert "hsl(" not in page
    # Functional content untouched: search form + both grids + form.
    assert 'id="searchform"' in page and 'id="apps"' in page and 'id="grid"' in page


def test_homepage_animation_modes() -> None:
    from browser_core.dashboard import dashboard_html

    off = dashboard_html(_fake_settings(homepage_animation="off"))
    assert "ld-anim" not in off  # Off injects NOTHING
    assert 'id="searchform"' in off
    for mode in ("aurora", "particles", "parallax"):
        page = dashboard_html(_fake_settings(homepage_animation=mode))
        assert "ld-anim" in page
        assert f'var mode = "{mode}"' in page
        # Hard constraints baked into the snippet.
        assert "prefers-reduced-motion" in page
        assert "visibilitychange" in page
        assert "pointer-events:none" in page.replace(" ", "")
    # Default (unset) is the calm variant; garbage falls back to it.
    assert 'var mode = "aurora"' in dashboard_html(_fake_settings())
    assert 'var mode = "aurora"' in dashboard_html(_fake_settings(homepage_animation="bogus"))


def test_settings_dialog_offers_homepage_animation() -> None:
    dialogs = _src("browser_ui/dialogs.py")
    assert "ANIMATION_CHOICES" in dialogs
    assert '"Homepage animation"' in dialogs
    assert 'set("homepage_animation", self.home_anim.currentData()' in dialogs


# ── Bug 3: logo transparency, decoded with the stdlib ──────────────────


def _png_alpha_stats(path: Path) -> dict:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, meta = 8, b"", {}
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        ctype = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + length]
        if ctype == b"IHDR":
            w, h, depth, color = struct.unpack(">IIBB", chunk[:10])
            meta = {"w": w, "h": h, "depth": depth, "color": color}
        elif ctype == b"IDAT":
            idat += chunk
        pos += 12 + length
    assert meta["depth"] == 8 and meta["color"] == 6, meta  # 8-bit RGBA
    w, h = meta["w"], meta["h"]
    raw = zlib.decompress(idat)
    stride = w * 4
    px = bytearray(w * h * 4)
    prev = bytearray(stride)
    for y in range(h):
        row = raw[y * (stride + 1) + 1 : (y + 1) * (stride + 1)]
        filt = raw[y * (stride + 1)]
        out = memoryview(px)[y * stride : (y + 1) * stride]
        for x in range(stride):
            a = out[x - 4] if x >= 4 else 0
            b = prev[x]
            c = prev[x - 4] if x >= 4 else 0
            if filt == 0:
                v = row[x]
            elif filt == 1:
                v = (row[x] + a) & 0xFF
            elif filt == 2:
                v = (row[x] + b) & 0xFF
            elif filt == 3:
                v = (row[x] + (a + b) // 2) & 0xFF
            elif filt == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                v = (row[x] + pr) & 0xFF
            else:  # pragma: no cover
                raise AssertionError(f"PNG filter {filt}")
            out[x] = v
        prev = bytearray(out)
    alphas = px[3::4]
    transparent = sum(1 for a in alphas if a == 0) / len(alphas)
    corners = [alphas[0], alphas[w - 1], alphas[(h - 1) * w], alphas[h * w - 1]]
    mid_edges = [
        alphas[w // 2],
        alphas[(h - 1) * w + w // 2],
        alphas[(h // 2) * w],
        alphas[(h // 2) * w + w - 1],
    ]
    opaque = [(i % w, i // w) for i, a in enumerate(alphas) if a > 0]
    xs = [p[0] for p in opaque]
    ys = [p[1] for p in opaque]
    return {
        "w": w,
        "h": h,
        "transparent": transparent,
        "corners": corners,
        "mid_edges": mid_edges,
        "bbox": (min(xs), min(ys), max(xs), max(ys)),
    }


@pytest.mark.parametrize("name", ["luckyd-browser-icon-v2.png", "icon.png"])
def test_logo_png_floats_on_true_transparency(name: str) -> None:
    stats = _png_alpha_stats(_BROWSER_DIR / "assets" / name)
    assert stats["corners"] == [0, 0, 0, 0]
    assert stats["mid_edges"] == [0, 0, 0, 0]
    assert stats["transparent"] >= 0.5  # no opaque plate behind the mark
    x0, y0, x1, y1 = stats["bbox"]
    # The opaque mark is strictly inside the canvas — not a full-bleed square.
    assert x0 > 0 and y0 > 0 and x1 < stats["w"] - 1 and y1 < stats["h"] - 1


@pytest.mark.parametrize("name", ["professional_icon.ico", "luckyd-browser-icon-v2.ico"])
def test_logo_ico_set_has_full_size_ladder(name: str) -> None:
    data = (_BROWSER_DIR / "assets" / name).read_bytes()
    reserved, typ, count = struct.unpack("<HHH", data[:6])
    assert reserved == 0 and typ == 1 and count >= 6
    sizes = set()
    for i in range(count):
        entry = data[6 + i * 16 : 6 + (i + 1) * 16]
        sizes.add(entry[0] or 256)
    assert 256 in sizes and 16 in sizes


# ── Bug 11: Multi Agent Terminal ───────────────────────────────────────


def test_mesh_page_is_embed_mode_with_focus_and_new_name() -> None:
    from browser_core.terminal_page import mesh_html, terminal_html

    mesh = mesh_html()
    assert "Multi Agent Terminal" in mesh
    assert "Agent Mesh" not in mesh
    assert mesh.count("<iframe") == 4
    assert mesh.count("embed=1") == 4  # panes load chrome-free terminals
    assert mesh.count('class="expand"') == 4  # per-pane focus buttons
    assert "main.focused" in mesh  # focus grid: one big + live rail
    term = terminal_html(None, shell="agent")
    assert "body.embed #bar" in term and "body.embed #meshdock" in term
    assert "MULTI AGENT TERMINAL" in term  # dock heading renamed
    dash = _src("browser_core/dashboard.py")
    assert "'Multi Agent Terminal', '/mesh'" in dash  # route stays /mesh
    assert "'LuckyD Chat', '/chat'" in dash


# ── Feature 13: LuckyD Chat ────────────────────────────────────────────


def test_chat_page_structure_and_quiet_states() -> None:
    from browser_core.chat_page import chat_html

    page = chat_html(_fake_settings())
    assert "LuckyD Chat" in page
    assert "Hub chat" in page and "Tasks" in page
    for route in ("/chat/state", "/chat/send", "/chat/tasks", "/chat/task", "/chat/task/cancel"):
        assert route in page, route
    assert "<select" not in page  # no dropdown controls at all
    assert "tip:" not in page.lower()
    # The one-line graceful states are baked in.
    assert "add your Hub token in Settings → Connections" in page
    assert "add your Claw token in Settings → Connections" in page
    assert "isn't reachable right now" in page


class _FakeResp:
    def __init__(self, status, obj):
        self.status = status
        self._body = json.dumps(obj).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _opener(routes, calls):
    def open_fn(req, timeout=None):
        calls.append(req)
        for sub, status, obj in routes:
            if sub in req.full_url:
                if isinstance(obj, Exception):
                    raise obj
                return _FakeResp(status, obj)
        raise AssertionError(f"unexpected URL {req.full_url}")

    return open_fn


def test_services_client_states_and_payloads() -> None:
    from browser_core import luckyd_services as ls

    st = _fake_settings(hub_token="HT", claw_token="CT")
    calls: list = []
    op = _opener(
        [
            ("18790/healthz", 200, {"ok": True, "ollama": True}),
            ("18791/healthz", 200, {"ok": True, "enabled": True, "projects": ["browser"]}),
            ("18790/chat", 200, {"ok": True, "text": "hi", "via": "luckyd", "model": "m"}),
            ("18791/tasks", 200, {"ok": True, "tasks": [{"id": "j1", "state": "done"}]}),
            ("18791/task", 200, {"ok": True, "job_id": "j9"}),
        ],
        calls,
    )
    assert ls.hub_state(st, op)["state"] == "ok"
    assert ls.claw_state(st, op)["projects"] == ["browser"]
    chat = ls.hub_chat(st, [{"role": "user", "content": "x"}], op)
    assert chat["state"] == "ok" and chat["text"] == "hi" and chat["via"] == "luckyd"
    assert ls.claw_tasks(st, op)["tasks"][0]["id"] == "j1"
    assert ls.claw_submit(st, "browser", "do it", "look", op)["job_id"] == "j9"
    submit_req = next(r for r in calls if r.method == "POST" and r.full_url.endswith("/task"))
    body = json.loads(submit_req.data)
    assert body["token"] == "CT" and body["profile"] == "look"
    assert body["source"] == "browser-chat"
    # GETs carry the token in the query, never a header.
    assert "token=HT" in calls[0].full_url

    # No token: short-circuit, no HTTP at all.
    calls.clear()
    assert ls.hub_state(_fake_settings(), op)["state"] == "no_token"
    assert ls.claw_tasks(_fake_settings(), op)["state"] == "no_token"
    assert calls == []
    # Unreachable and bad-token collapse to their one-line states.
    down = _opener([("healthz", 200, urllib.error.URLError("refused"))], [])
    assert ls.hub_state(st, down)["state"] == "unreachable"

    def open403(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 403, "Forbidden", {}, io.BytesIO(b'{"ok": false}')
        )

    assert ls.claw_state(st, open403)["state"] == "bad_token"


@pytest.fixture()
def ctl(monkeypatch):
    """A live control server (loopback) with stubbed Hub/Claw upstreams."""
    import browser_core.control_server as cs

    settings = _fake_settings(hub_token="HTOKEN-SECRET-1", claw_token="CTOKEN-SECRET-2")
    calls: list = []
    routes = [
        ("18790/healthz", 200, {"ok": True, "ollama": True}),
        ("18791/healthz", 200, {"ok": True, "enabled": True, "projects": ["browser", "hub"]}),
        ("18790/chat", 200, {"ok": True, "text": "hello", "via": "luckyd", "model": "m"}),
        ("18791/tasks", 200, {"ok": True, "tasks": [{"id": "j1", "state": "running"}]}),
        ("18791/task/j1/cancel", 200, {"ok": True, "task": {"id": "j1", "state": "cancelled"}}),
        ("18791/task", 200, {"ok": True, "job_id": "j2"}),
    ]
    monkeypatch.setattr(urllib.request, "urlopen", _opener(routes, calls))
    handler = cs.make_handler(SimpleNamespace(), token="", settings=settings)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"127.0.0.1:{httpd.server_address[1]}", calls
    httpd.shutdown()
    httpd.server_close()


def _req(base, method, path, body=None):
    conn = http.client.HTTPConnection(base, timeout=10)
    payload = json.dumps(body) if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8", "replace")
    conn.close()
    return resp.status, raw


def test_chat_routes_end_to_end(ctl) -> None:
    base, calls = ctl
    status, page = _req(base, "GET", "/chat")
    assert status == 200 and "LuckyD Chat" in page

    status, raw = _req(base, "GET", "/chat/state")
    state = json.loads(raw)
    assert status == 200
    assert state["hub"]["state"] == "ok" and state["claw"]["state"] == "ok"
    assert state["projects"] == ["browser", "hub"]

    status, raw = _req(
        base, "POST", "/chat/send", {"messages": [{"role": "user", "content": "hi"}]}
    )
    sent = json.loads(raw)
    assert sent["state"] == "ok" and sent["text"] == "hello"

    status, raw = _req(
        base, "POST", "/chat/task", {"project": "browser", "prompt": "x", "profile": "look"}
    )
    assert json.loads(raw)["job_id"] == "j2"

    status, raw = _req(base, "GET", "/chat/tasks")
    assert json.loads(raw)["tasks"][0]["id"] == "j1"

    status, raw = _req(base, "POST", "/chat/task/cancel", {"id": "j1"})
    assert json.loads(raw)["task"]["state"] == "cancelled"

    # Tokens reached the upstreams (query for GET, body for POST), and
    # never appear in any response the page receives.
    assert any("token=CTOKEN-SECRET-2" in r.full_url for r in calls if r.method == "GET")
    assert "HTOKEN-SECRET-1" not in page and "CTOKEN-SECRET-2" not in page


def test_chat_routes_without_tokens_stay_quiet(monkeypatch) -> None:
    import browser_core.control_server as cs

    calls: list = []
    monkeypatch.setattr(urllib.request, "urlopen", _opener([], calls))
    handler = cs.make_handler(SimpleNamespace(), token="", settings=_fake_settings())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"127.0.0.1:{httpd.server_address[1]}"
        _status, raw = _req(base, "GET", "/chat/state")
        state = json.loads(raw)
        assert state["hub"]["state"] == "no_token"
        assert state["claw"]["state"] == "no_token"
        assert calls == []  # nothing was even attempted
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_chat_settings_fields_and_entry_points() -> None:
    dialogs = _src("browser_ui/dialogs.py")
    for field in ("hub_token", "claw_token", "hub_url", "claw_url"):
        assert f"self.{field}" in dialogs
        assert f'set("{field}", self.{field}.text().strip())' in dialogs
    assert '"Connections"' in dialogs
    assert "EchoMode.Password" in dialogs
    mw = _src("browser_ui/main_window.py")
    assert '"LuckyD Chat", self.open_luckyd_chat' in mw
    fn = _function_segment(mw, "open_luckyd_chat")
    assert '"/chat"' in fn
