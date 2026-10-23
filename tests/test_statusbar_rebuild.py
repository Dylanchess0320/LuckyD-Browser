"""Status strip rebuild (2026-10-22) — Dylan, twice refined.

Original ask: "I also dont need the bottom header with the site
permissions button." Correction: "the bottom header is useful to see
what site im hovering" — and "if you can reconstruct it fancier that
would be nice."

Result: the QStatusBar is a slim TEXT-ONLY strip (browser_ui/status_bar.py):
- REMOVED widgets: lock/site-permissions button, zoom pill, chunky
  QProgressBar load widget (and the _update_security/_update_zoom_label
  methods that existed only to feed them).
- KEPT + upgraded: hovered-link URLs, rendered rich — scheme dimmed,
  domain in the theme accent (format_hover_html).
- Plain status text still lands via the showMessage API, rerouted to
  the strip's label.
- Load progress is a thin painted line on the bar's top edge, driven by
  the load-progress signal, hidden at 100%/idle.

Capabilities survive on their other paths:
- Site Permissions: Tools menu, Ctrl+K palette, page context menu.
- Zoom in/out/reset: View menu (Ctrl+= / Ctrl+- / Ctrl+0).
- Load state: tab spinner AND the strip's progress line.

Qt chrome is asserted at source level (suite convention — PySide6 is
not installed in CI); real-Qt proofs were run in a real-Qt environment
during development and are recorded in
~/workspace/luckyd-statusbar-removal-report.md.
"""

from __future__ import annotations

import ast
import html
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

_PAL = {"muted": "#111111", "accent": "#222222", "text": "#333333"}


def _src(rel: str) -> str:
    return (_BROWSER_DIR / rel).read_text(encoding="utf-8")


def _class_node(rel: str, class_name: str) -> ast.ClassDef:
    tree = ast.parse(_src(rel))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node
    raise AssertionError(f"class {class_name} not found in {rel}")


def _methods(rel: str, class_name: str) -> set[str]:
    return {n.name for n in _class_node(rel, class_name).body if isinstance(n, ast.FunctionDef)}


def _function_segment(src: str, name: str) -> str:
    match = re.search(rf"^(\s*)def {re.escape(name)}\(", src, re.M)
    assert match, f"function {name} not found"
    indent = len(match.group(1))
    end = len(src)
    for nxt in re.finditer(r"^(\s*)(?:def |class )", src[match.end() :], re.M):
        if len(nxt.group(1)) <= indent:
            end = match.end() + nxt.start()
            break
    return src[match.start() : end]


def _format_hover_html():
    """Exec format_hover_html out of status_bar.py (Qt-free by design)."""
    tree = ast.parse(_src("browser_ui/status_bar.py"))
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "format_hover_html"
    )
    fn.decorator_list = []
    ns: dict = {"html": html}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "<hover>", "exec"), ns)
    return ns["format_hover_html"]


# ── The unwanted widgets are gone ──────────────────────────────────────


def test_main_window_has_no_removed_widgets() -> None:
    mw = _src("browser_ui/main_window.py")
    for token in ("zoom_label", "lock_label", "self.progress", "QProgressBar"):
        assert token not in mw, f"removed-widget remnant in main_window.py: {token}"
    methods = _methods("browser_ui/main_window.py", "MainWindow")
    for gone in ("_update_zoom_label", "_update_security"):
        assert gone not in methods


def test_status_bar_module_has_no_buttons_or_progress_widget() -> None:
    sb = _src("browser_ui/status_bar.py")
    assert "QPushButton" not in sb
    assert "QProgressBar" not in sb
    # Colors come from the theme palette, never literals.
    assert not re.search(r"#[0-9a-fA-F]{6}\b", sb)
    assert "theme_palette" in sb


# ── The rebuilt strip ──────────────────────────────────────────────────


def test_status_bar_class_shape() -> None:
    node = _class_node("browser_ui/status_bar.py", "StatusBar")
    bases = [ast.unparse(b) for b in node.bases]
    assert bases == ["QStatusBar"]
    methods = _methods("browser_ui/status_bar.py", "StatusBar")
    for needed in (
        "showMessage",
        "clearMessage",
        "show_hover_url",
        "set_load_progress",
        "paintEvent",
    ):
        assert needed in methods
    sb = _src("browser_ui/status_bar.py")
    segment = _function_segment(sb, "paintEvent")
    assert "fillRect" in segment and "PROGRESS_LINE_PX" in segment
    assert 'pal["accent"]' in segment


def test_main_window_builds_and_routes_the_strip() -> None:
    mw = _src("browser_ui/main_window.py")
    build = _function_segment(mw, "_build_statusbar")
    assert "StatusBar(self.settings, self)" in build
    assert "self.setStatusBar(self.status_bar)" in build
    hover = _function_segment(mw, "on_link_hovered")
    assert "show_hover_url" in hover
    progress = _function_segment(mw, "on_load_progress")
    assert "set_load_progress" in progress
    finished = _function_segment(mw, "on_load_finished")
    assert "set_load_progress(None)" in finished


def test_tab_widget_wires_hover_and_progress() -> None:
    tw = _src("browser_ui/tab_widget.py")
    assert "page.linkHovered.connect(self._mw.on_link_hovered)" in tw
    assert "self._mw.on_load_progress" in tw
    assert "self._mw.on_load_started" in tw


def test_theme_styles_the_strip_but_not_the_pill() -> None:
    theme = _src("browser_ui/theme.py")
    assert "QStatusBar" in theme
    assert "status_text" in theme
    assert "zoom_pill" not in theme


# ── Hover formatting (functional) ──────────────────────────────────────


def test_hover_html_emphasizes_domain() -> None:
    fmt = _format_hover_html()
    out = fmt("https://sub.example.com/a/b?q=1", _PAL)
    assert f'<span style="color:{_PAL["accent"]};font-weight:600">sub.example.com</span>' in out
    assert f'<span style="color:{_PAL["muted"]}">https://</span>' in out
    assert out.endswith("/a/b?q=1")


def test_hover_html_escapes_and_passes_plain_text() -> None:
    fmt = _format_hover_html()
    out = fmt("https://example.com/<script>", _PAL)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert fmt("just some text", _PAL) == "just some text"
    assert fmt("", _PAL) == ""


# ── Site Permissions survives on all three other paths ─────────────────


def test_site_permissions_reachable_from_tools_menu() -> None:
    mw = _src("browser_ui/main_window.py")
    assert "def open_site_permissions" in mw
    assert '_add(tools_menu, "Site Permissions…", self.open_site_permissions)' in mw


def test_site_permissions_reachable_from_palette() -> None:
    palette = _src("browser_ui/palette.py")
    assert '("Site Permissions…", self._mw.open_site_permissions)' in palette


def test_site_permissions_reachable_from_context_menu() -> None:
    web_view = _src("browser_ui/web_view.py")
    assert 'menu.addAction("Site Permissions…", self._mw.open_site_permissions)' in web_view


# ── Zoom survives without the pill ─────────────────────────────────────


def test_zoom_menu_entries_and_shortcuts_survive() -> None:
    mw = _src("browser_ui/main_window.py")
    assert '_add(view_menu, "Zoom In", self.zoom_in, "Ctrl+=")' in mw
    assert '_add(view_menu, "Zoom Out", self.zoom_out, "Ctrl+-")' in mw
    assert '_add(view_menu, "Actual Size", self.zoom_reset, "Ctrl+0")' in mw


def test_zoom_methods_still_apply_and_remember() -> None:
    mw = _src("browser_ui/main_window.py")
    for name, snippet in (
        ("zoom_in", "clamp_zoom(view.zoomFactor() + 0.1)"),
        ("zoom_out", "clamp_zoom(view.zoomFactor() - 0.1)"),
        ("zoom_reset", "view.setZoomFactor(1.0)"),
    ):
        segment = _function_segment(mw, name)
        assert snippet in segment
        assert "self._remember_zoom(view)" in segment


def test_load_finished_still_restores_zoom_and_records_history() -> None:
    mw = _src("browser_ui/main_window.py")
    segment = _function_segment(mw, "on_load_finished")
    assert "self._apply_zoom(view)" in segment
    assert "self.storage.add_visit" in segment
    assert "self._update_nav_state(view)" in segment
