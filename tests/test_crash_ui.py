"""P1: renderer crash recovery UI.

A dead renderer used to leave a frozen/blank tab with no recovery path.
The page now shows a dead-tab overlay whose Reload link navigates to the
crashed URL (spawning a fresh renderer) plus an error toast.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

# web_view.py imports PySide6 at top level, which is not installed
# headless — stub it like tests/test_trust.py does.
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


class _WebPageBaseStub:
    """Real QWebEnginePage stand-in (cf. test_trust._PageStub).

    Set before the web_view import (this module loads at collection time,
    ahead of any test body importing it) so WebPage is a genuine class
    instead of mock-subclass garbage.
    """

    def __init__(self, *args, **kwargs):
        self.renderProcessTerminated = MagicMock()
        self.featurePermissionRequested = MagicMock()


class _WebViewBaseStub:
    def __init__(self, *args, **kwargs):
        pass


sys.modules["PySide6.QtWebEngineCore"].QWebEnginePage = _WebPageBaseStub
sys.modules["PySide6.QtWebEngineWidgets"].QWebEngineView = _WebViewBaseStub

from browser_ui.theme import palette as _theme_palette
from browser_ui.web_view import WebPage, _crash_html


def _page(url="https://example.com/crash-me"):
    # Bypass QWebEnginePage.__init__: this module is imported both with
    # MagicMock stubs (headless) and with a real PySide6 already loaded by a
    # sibling test. The handler methods only touch the state below.
    page = WebPage.__new__(WebPage)
    page._mw = MagicMock()
    page._renderer_crashed_urls = set()
    page._crash_overlay_pending = False
    page._showing_crash_overlay = False
    page._crash_timer = None
    page.url = MagicMock(return_value=MagicMock(toString=MagicMock(return_value=url)))
    page.setHtml = MagicMock()
    page.view = MagicMock(return_value=None)
    page._log_renderer_termination = MagicMock()
    return page


class TestCrashOverlay:
    def test_overlay_has_reload_link_to_crashed_url(self):
        html = _crash_html(_theme_palette(None), "https://example.com/crash-me")
        assert "Tab crashed" in html
        assert 'href="https://example.com/crash-me"' in html
        assert "Reload tab" in html

    def test_empty_url_falls_back_to_blank(self):
        html = _crash_html(_theme_palette(None), "")
        assert 'href="about:blank"' in html


class TestCrashHandler:
    def test_termination_schedules_overlay_and_toast(self):
        page = _page()
        page._on_render_terminated(MagicMock(), 1)
        assert page._crash_overlay_pending is True
        assert "https://example.com/crash-me" in page._renderer_crashed_urls
        page._show_crash_overlay("https://example.com/crash-me")
        html = page.setHtml.call_args[0][0]
        assert "Tab crashed" in html
        assert "https://example.com/crash-me" in html
        page._mw.toasts.show.assert_called_once()
        assert page._mw.toasts.show.call_args.kwargs.get("kind") == "error"

    def test_normal_termination_is_not_a_crash(self):
        page = _page()
        page._is_normal_termination = MagicMock(return_value=True)
        page._on_render_terminated(MagicMock(), 0)
        page.setHtml.assert_not_called()
        page._mw.toasts.show.assert_not_called()

    def test_handler_never_raises(self):
        page = _page()
        page.url.side_effect = RuntimeError("torn down")
        page._mw.toasts.show.side_effect = RuntimeError("gone")
        assert page._on_render_terminated(MagicMock(), 1) is None
        page._show_crash_overlay("")
        html = page.setHtml.call_args[0][0]
        assert "about:blank" in html

    def test_signal_connected_at_construction(self):
        import inspect

        source = inspect.getsource(WebPage.__init__)
        assert "renderProcessTerminated.connect(self._on_render_terminated)" in source
