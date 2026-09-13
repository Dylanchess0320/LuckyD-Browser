"""Night-6 wave-2 tests: tools/browser_tools.py.

Playwright is NOT installed here, so live-browser paths are exercised with
a FakePage injected via monkeypatched _get_page, plus the real no-playwright
error paths. Covers: HTML text extraction, route-handler factory,
navigate/click/type/screenshot/evaluate/snapshot output formatting,
state save/load/clear, device emulation config, intercept list/mock/clear,
tracing, headless toggle, restart/close lifecycle, OpenInBrowser.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path

import pytest

import tools.browser_tools as bt
from tools.registry import registry


def _ok(result) -> bool:
    return not bool(getattr(result, "error", False))


def _tool(name):
    t = registry.get(name)
    assert t is not None, f"tool {name} not registered"
    return t


@pytest.fixture
def clean_browser(monkeypatch):
    """Reset all browser_tools module globals around each test."""
    monkeypatch.setattr(bt, "_page", None)
    monkeypatch.setattr(bt, "_browser", None)
    monkeypatch.setattr(bt, "_pw_manager", None)
    monkeypatch.setattr(bt, "_playwright", None)
    monkeypatch.setattr(bt, "_headless", True)
    monkeypatch.setattr(bt, "_device_config", None)
    monkeypatch.setattr(bt, "_storage_state_path", None)
    monkeypatch.setattr(bt, "_intercepts", [])
    yield
    monkeypatch.setattr(bt, "_page", None)
    monkeypatch.setattr(bt, "_browser", None)
    monkeypatch.setattr(bt, "_pw_manager", None)
    monkeypatch.setattr(bt, "_playwright", None)
    monkeypatch.setattr(bt, "_headless", True)
    monkeypatch.setattr(bt, "_device_config", None)
    monkeypatch.setattr(bt, "_storage_state_path", None)
    monkeypatch.setattr(bt, "_intercepts", [])


# ── fakes ──────────────────────────────────────────────────────────────


class FakeTracing:
    def __init__(self):
        self.started_with = None

    async def start(self, **kw):
        self.started_with = kw

    async def stop(self, path=None):
        Path(path).write_bytes(b"fake-trace-bytes")


class FakeContext:
    def __init__(self):
        self.tracing = FakeTracing()
        self.cookies_cleared = False

    async def storage_state(self):
        return {"cookies": [{"name": "c", "value": "v"}], "origins": []}

    async def clear_cookies(self):
        self.cookies_cleared = True


class FakePage:
    def __init__(self):
        self.context = FakeContext()
        self.routes = {}
        self.closed = False
        self.url = "https://example.com/start"
        self.clicked = None
        self.filled = None
        self.pressed = None

    async def goto(self, url, timeout=None):
        self.url = url

    async def title(self):
        return "Example Title"

    async def content(self):
        return (
            "<html><head><title>T</title><script>var x=1;</script></head>"
            "<body><h1>Hi</h1><p>body text</p></body></html>"
        )

    async def evaluate(self, expr):
        return [
            {"tag": "a", "txt": "link text", "id": "go", "cls": "", "href": "/go"},
            {"tag": "button", "txt": "Send", "id": "", "cls": "btn primary", "href": ""},
            {"tag": "input", "txt": "", "id": "", "cls": "", "href": ""},
        ]

    async def click(self, selector, timeout=None):
        self.clicked = selector

    async def fill(self, selector, text, timeout=None):
        self.filled = (selector, text)

    async def press(self, selector, key):
        self.pressed = (selector, key)

    async def screenshot(self, path=None, full_page=False):
        Path(path).write_bytes(b"PNGDATA")

    async def route(self, pattern, handler):
        self.routes[pattern] = handler

    async def close(self):
        self.closed = True


@pytest.fixture
def fake_page(monkeypatch, clean_browser):
    page = FakePage()

    async def _get():
        return page

    monkeypatch.setattr(bt, "_get_page", _get)
    return page


# ── pure helpers ───────────────────────────────────────────────────────


class TestExtractText:
    def test_strips_scripts_and_tags(self):
        html = (
            "<html><head><script>alert(1)</script><style>.x{}</style></head>"
            "<body><h1>Hello</h1><p>World</p></body></html>"
        )
        text = bt._extract_text_from_html(html)
        assert "Hello" in text and "World" in text
        assert "alert" not in text
        assert "<" not in text

    def test_collapses_whitespace(self):
        text = bt._extract_text_from_html("<p>a</p>\n\n\n<p>b</p>")
        assert text == "a b"

    def test_caps_at_8000(self):
        text = bt._extract_text_from_html("<p>" + "z" * 9000 + "</p>")
        assert len(text) == 8000


class TestFulfillHandler:
    def test_is_coroutine_function(self):
        h = bt._make_fulfill_handler(404)
        assert asyncio.iscoroutinefunction(h)

    async def test_fulfills_with_status(self):
        seen = {}

        class Route:
            async def fulfill(self, status=None):
                seen["status"] = status

        await bt._make_fulfill_handler(418)(Route())
        assert seen["status"] == 418


class TestGetPlaywright:
    async def test_missing_playwright_raises(self, clean_browser):
        assert "playwright" not in sys.modules
        with pytest.raises(RuntimeError, match="Playwright not installed"):
            await bt._get_playwright()

    async def test_navigate_errors_without_playwright(self, clean_browser):
        r = await _tool("BrowserNavigate").execute(url="https://example.com")
        assert not _ok(r)
        assert "Playwright not installed" in r.text

    async def test_click_type_snapshot_evaluate_errors_without_playwright(self, clean_browser):
        for name, kw in [
            ("BrowserClick", {"selector": "#x"}),
            ("BrowserType", {"selector": "#x", "text": "hi"}),
            ("BrowserSnapshot", {}),
            ("BrowserEvaluate", {"expression": "1+1"}),
            ("BrowserScreenshot", {}),
            ("BrowserTrace", {"action": "start"}),
        ]:
            r = await _tool(name).execute(**kw)
            assert not _ok(r), name


# ── page-driven tools with FakePage ────────────────────────────────────


class TestNavigate:
    async def test_formats_elements(self, fake_page):
        r = await _tool("BrowserNavigate").execute(url="https://example.com")
        assert _ok(r)
        assert "Page: Example Title" in r.text
        assert "URL: https://example.com" in r.text
        assert "Interactive elements (3):" in r.text
        assert "#go" in r.text  # id branch
        assert ".btn" in r.text  # class branch (first class only)
        assert "[input]" in r.text  # tag fallback branch
        assert "href: /go" in r.text
        assert fake_page.url == "https://example.com"
        assert r.metadata["elements"] == 3
        assert r.metadata["url"] == "https://example.com"

    async def test_page_error_surfaces(self, monkeypatch, clean_browser):
        async def _bad():
            raise RuntimeError("boom")

        monkeypatch.setattr(bt, "_get_page", _bad)
        r = await _tool("BrowserNavigate").execute(url="https://example.com")
        assert not _ok(r)
        assert "Browser navigation error: boom" in r.text


class TestClickType:
    async def test_click(self, fake_page):
        r = await _tool("BrowserClick").execute(selector="#go")
        assert _ok(r)
        assert fake_page.clicked == "#go"
        assert "Clicked: #go" in r.text
        assert r.metadata["selector"] == "#go"

    async def test_type_no_submit(self, fake_page):
        r = await _tool("BrowserType").execute(selector="#q", text="hello")
        assert _ok(r)
        assert fake_page.filled == ("#q", "hello")
        assert fake_page.pressed is None
        assert r.metadata == {
            "selector": "#q",
            "text_length": 5,
            "submitted": False,
        }

    async def test_type_with_submit(self, fake_page):
        r = await _tool("BrowserType").execute(selector="#q", text="hi", submit=True)
        assert _ok(r)
        assert fake_page.pressed == ("#q", "Enter")
        assert "and submitted" in r.text


class TestSnapshotScreenshotEvaluate:
    async def test_snapshot(self, fake_page):
        r = await _tool("BrowserSnapshot").execute()
        assert _ok(r)
        assert "Title: Example Title" in r.text
        assert "URL: https://example.com/start" in r.text
        assert "Hi" in r.text  # extracted body text
        assert "var x=1" not in r.text  # script stripped

    async def test_screenshot_auto_path(self, fake_page, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        r = await _tool("BrowserScreenshot").execute()
        assert _ok(r)
        path = r.metadata["path"]
        assert path.startswith("screenshot_") and path.endswith(".png")
        assert Path(path).read_bytes() == b"PNGDATA"
        assert r.metadata["size"] == 7
        assert "7 bytes" in r.text

    async def test_screenshot_explicit_path(self, fake_page, tmp_path):
        p = tmp_path / "shot.png"
        r = await _tool("BrowserScreenshot").execute(path=str(p), full_page=True)
        assert _ok(r)
        assert r.metadata["full_page"] is True
        assert p.exists()

    async def test_evaluate_json(self, fake_page):
        r = await _tool("BrowserEvaluate").execute(expression="document.title")
        assert _ok(r)
        # fake evaluate returns a list -> json pretty-printed
        parsed = json.loads(r.text)
        assert parsed[0]["tag"] == "a"
        assert r.metadata["expression"] == "document.title"


# ── browser state (cookies / localStorage) ─────────────────────────────


class TestBrowserState:
    async def test_unknown_action(self, clean_browser):
        r = await _tool("BrowserState").execute(action="explode")
        assert not _ok(r)
        assert "Unknown action" in r.text

    async def test_save_writes_file(self, fake_page, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        r = await _tool("BrowserState").execute(action="save")
        assert _ok(r)
        data = json.loads((tmp_path / ".browser_state.json").read_text())
        assert data["cookies"] == [{"name": "c", "value": "v"}]
        assert r.metadata["path"].endswith(".browser_state.json")

    async def test_load_missing_file(self, clean_browser, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        r = await _tool("BrowserState").execute(action="load")
        assert not _ok(r)
        assert "No saved state found" in r.text

    async def test_load_corrupt_file(self, fake_page, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".browser_state.json").write_text("[1, 2, 3]")
        r = await _tool("BrowserState").execute(action="load")
        assert not _ok(r)
        assert "corrupted" in r.text

    async def test_load_roundtrip(self, fake_page, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        await _tool("BrowserState").execute(action="save")
        r = await _tool("BrowserState").execute(action="load")
        assert _ok(r)
        assert r.text == "Browser state loaded."
        assert bt._storage_state_path == str(tmp_path / ".browser_state.json")

    async def test_clear(self, fake_page, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".browser_state.json").write_text("{}")
        bt._storage_state_path = "whatever"
        r = await _tool("BrowserState").execute(action="clear")
        assert _ok(r)
        assert not (tmp_path / ".browser_state.json").exists()
        assert bt._storage_state_path is None
        assert fake_page.context.cookies_cleared


# ── device emulation ───────────────────────────────────────────────────


class TestEmulate:
    async def test_unknown_device_errors(self, clean_browser, monkeypatch):
        devices = types.SimpleNamespace()
        devices.Pixel_5 = {"viewport": {"width": 393, "height": 851}}
        fake_pw = types.ModuleType("playwright.async_api")
        fake_pw.devices = devices
        monkeypatch.setitem(sys.modules, "playwright.async_api", fake_pw)
        monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
        r = await _tool("BrowserEmulate").execute(device="Nokia 3310")
        assert not _ok(r)
        assert "Device 'Nokia 3310' not found" in r.text

    async def test_unknown_device_without_playwright(self, clean_browser):
        # Without playwright installed the import itself fails first.
        r = await _tool("BrowserEmulate").execute(device="Nokia 3310")
        assert not _ok(r)
        assert "Emulate error" in r.text

    async def test_viewport_config(self, clean_browser):
        r = await _tool("BrowserEmulate").execute(device="", width=800, height=600)
        assert _ok(r)
        assert bt._device_config == {"viewport": {"width": 800, "height": 600}}
        assert "800x600" in r.text

    async def test_desktop_resets(self, clean_browser):
        bt._device_config = {"viewport": {"width": 1, "height": 1}}
        r = await _tool("BrowserEmulate").execute(device="desktop")
        assert _ok(r)
        assert bt._device_config is None

    async def test_known_device_uses_playwright_devices(self, clean_browser, monkeypatch):
        devices = types.SimpleNamespace()
        devices.iPhone_12 = {"viewport": {"width": 390, "height": 844}}
        fake_pw = types.ModuleType("playwright.async_api")
        fake_pw.devices = devices
        monkeypatch.setitem(sys.modules, "playwright.async_api", fake_pw)
        monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
        r = await _tool("BrowserEmulate").execute(device="iPhone 12")
        assert _ok(r)
        assert bt._device_config == {"viewport": {"width": 390, "height": 844}}

    async def test_fuzzy_device_match(self, clean_browser, monkeypatch):
        devices = types.SimpleNamespace()
        devices.Pixel_5 = {"viewport": {"width": 393, "height": 851}}
        fake_pw = types.ModuleType("playwright.async_api")
        fake_pw.devices = devices
        monkeypatch.setitem(sys.modules, "playwright.async_api", fake_pw)
        monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
        r = await _tool("BrowserEmulate").execute(device="pixel")
        assert _ok(r)
        assert bt._device_config == {"viewport": {"width": 393, "height": 851}}


# ── network interception ───────────────────────────────────────────────


class TestIntercept:
    async def test_list_empty(self, clean_browser):
        r = await _tool("BrowserIntercept").execute(action="list")
        assert _ok(r)
        assert "No active intercepts." in r.text

    async def test_list_shows_entries(self, clean_browser):
        bt._intercepts.append({"pattern": "**/*.png", "status": 204})
        r = await _tool("BrowserIntercept").execute(action="list")
        assert _ok(r)
        assert "**/*.png → 204" in r.text

    async def test_mock_requires_pattern(self, clean_browser):
        r = await _tool("BrowserIntercept").execute(action="mock")
        assert not _ok(r)
        assert "url_pattern is required" in r.text

    async def test_mock_registers_and_applies_to_page(self, fake_page):
        r = await _tool("BrowserIntercept").execute(
            action="mock", url_pattern="**/ads/*", status=404
        )
        assert _ok(r)
        assert "**/ads/* → HTTP 404" in r.text
        assert bt._intercepts == [{"pattern": "**/ads/*", "status": 404}]
        handler = fake_page.routes["**/ads/*"]
        assert asyncio.iscoroutinefunction(handler)

    async def test_clear(self, fake_page):
        bt._intercepts.append({"pattern": "**/x", "status": 200})
        r = await _tool("BrowserIntercept").execute(action="clear")
        assert _ok(r)
        assert bt._intercepts == []
        assert "restarted" in r.text

    async def test_unknown_action(self, clean_browser):
        r = await _tool("BrowserIntercept").execute(action="bogus")
        assert not _ok(r)


# ── tracing / headless / close / open-in-browser ───────────────────────


class TestTrace:
    async def test_start_stop(self, fake_page, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        r = await _tool("BrowserTrace").execute(action="start")
        assert _ok(r)
        assert fake_page.context.tracing.started_with == {
            "screenshots": True,
            "snapshots": True,
        }
        r = await _tool("BrowserTrace").execute(action="stop")
        assert _ok(r)
        assert (tmp_path / "browser_trace.zip").read_bytes() == b"fake-trace-bytes"
        assert r.metadata["size"] == len(b"fake-trace-bytes")


class TestHeadless:
    async def test_noop_toggle(self, clean_browser):
        r = await _tool("BrowserToggleHeadless").execute(headless=True)
        assert _ok(r)
        assert "headless" in r.text
        assert bt._headless is True

    async def test_toggle_to_headed(self, clean_browser):
        r = await _tool("BrowserToggleHeadless").execute(headless=False)
        assert _ok(r)
        assert bt._headless is False
        assert "headed (visible)" in r.text
        assert r.metadata == {"headless": False}


class TestRestartClose:
    async def test_restart_closes_everything(self, clean_browser):
        closed = []

        class P:
            async def close(self):
                closed.append("page")

        class B:
            async def close(self):
                closed.append("browser")

        class M:
            async def __aexit__(self, *a):
                closed.append("manager")

        bt._page, bt._browser, bt._pw_manager = P(), B(), M()
        await bt._restart_browser()
        assert closed == ["page", "browser", "manager"]
        assert bt._page is None and bt._browser is None and bt._pw_manager is None

    async def test_restart_with_nothing_open(self, clean_browser):
        await bt._restart_browser()  # must not raise
        assert bt._page is None

    async def test_close_resets_globals(self, clean_browser):
        page = FakePage()
        bt._page = page
        r = await _tool("BrowserClose").execute()
        assert _ok(r)
        assert page.closed
        assert bt._page is None and bt._browser is None

    async def test_close_reports_error(self, clean_browser):
        class Bad:
            async def close(self):
                raise RuntimeError("nope")

        bt._page = Bad()
        r = await _tool("BrowserClose").execute()
        assert not _ok(r)
        assert "Close error: nope" in r.text


class TestOpenInBrowser:
    async def test_opens_url(self, monkeypatch):
        opened = []
        fake_wb = types.ModuleType("webbrowser")
        fake_wb.open = lambda url: opened.append(url)
        monkeypatch.setitem(sys.modules, "webbrowser", fake_wb)
        r = await _tool("OpenInBrowser").execute(url="https://example.com")
        assert _ok(r)
        assert opened == ["https://example.com"]
        assert "Opened in your browser" in r.text
