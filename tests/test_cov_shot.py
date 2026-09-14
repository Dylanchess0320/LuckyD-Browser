"""Coverage tests for browser_core.screenshot — CDP screenshot capture.

No real browser or CDP endpoint exists in this container, so the network seams
(httpx.get, websockets.connect) are faked with hand-written stand-ins. The CDP
message protocol itself (ids, event skipping, error surfacing) is real.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from browser_core import screenshot
from browser_core.screenshot import _cmd, _find_target, suggested_name


class FakeWS:
    """Hand-written websocket stand-in: replays scripted inbound messages."""

    def __init__(self, replies: list[str]):
        self._replies = list(replies)
        self.sent: list[dict] = []

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def recv(self) -> str:
        assert self._replies, "FakeWS ran out of scripted replies"
        return self._replies.pop(0)


class _FakeConn:
    """Async context manager returned by the fake websockets.connect."""

    def __init__(self, ws: FakeWS):
        self._ws = ws

    async def __aenter__(self) -> FakeWS:
        return self._ws

    async def __aexit__(self, *exc) -> bool:
        return False


class FakeWebsockets:
    """Hand-written stand-in for the `websockets` module."""

    def __init__(self, ws_factory):
        self._ws_factory = ws_factory
        self.connect_calls: list[dict] = []

    def connect(self, url, **kwargs):
        self.connect_calls.append({"url": url, **kwargs})
        return _FakeConn(self._ws_factory())


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


PAGE_TARGET = {
    "type": "page",
    "url": "https://example.com/article",
    "webSocketDebuggerUrl": "ws://fake/devtools/page/1",
}
OTHER_PAGE = {
    "type": "page",
    "url": "https://other.dev/",
    "webSocketDebuggerUrl": "ws://fake/devtools/page/2",
}
WORKER_TARGET = {"type": "service_worker", "url": "https://example.com/sw.js"}


def _msg(mid, **fields):
    payload = {"id": mid}
    payload.update(fields)
    return json.dumps(payload)


@pytest.fixture()
def fake_network(monkeypatch):
    """Install fake websockets module + fake httpx.get; returns the fakes."""
    state = {}

    def fake_get(url, **kwargs):
        state["get_url"] = url
        return FakeResp(state["targets"])

    state["targets"] = [PAGE_TARGET, WORKER_TARGET, OTHER_PAGE]
    state["ws"] = FakeWebsockets(lambda: state["fake_ws"])
    monkeypatch.setitem(sys.modules, "websockets", state["ws"])
    monkeypatch.setattr(httpx, "get", fake_get)
    return state


def _ws_for(*replies: str) -> FakeWS:
    return FakeWS(list(replies))


class TestSuggestedName:
    _NOW = datetime(2026, 8, 7, 15, 30, 12)

    def test_basic(self):
        name = suggested_name("https://example.com/docs", now=self._NOW)
        assert name == "screenshot-example.com-20260807-153012.jpg"

    def test_empty_url_uses_page(self):
        name = suggested_name("", now=self._NOW)
        assert name == "screenshot-page-20260807-153012.jpg"

    def test_unsafe_host_chars_dashed(self):
        name = suggested_name("https://exa mple.com!/x", now=self._NOW)
        assert name == "screenshot-exa-mple.com-20260807-153012.jpg"

    def test_host_reduced_to_nothing_uses_page(self):
        name = suggested_name("http://-.-/", now=self._NOW)
        assert name.startswith("screenshot-page-")

    def test_ext_with_dot(self):
        name = suggested_name("https://example.com/", ext=".png", now=self._NOW)
        assert name.endswith(".png")

    def test_empty_ext_defaults_to_jpg(self):
        name = suggested_name("https://example.com/", ext="", now=self._NOW)
        assert name.endswith(".jpg")

    def test_none_ext_defaults_to_jpg(self):
        name = suggested_name("https://example.com/", ext=None, now=self._NOW)
        assert name.endswith(".jpg")

    def test_default_now_is_current_time(self):
        name = suggested_name("https://example.com/")
        assert name.startswith("screenshot-example.com-")
        assert name.endswith(".jpg")


class TestFindTarget:
    def test_exact_match_wins(self):
        targets = [OTHER_PAGE, PAGE_TARGET]
        assert _find_target(targets, "https://example.com/article") is PAGE_TARGET

    def test_prefix_match_when_no_exact(self):
        # target has trailing fragment; search URL is a prefix of it.
        targets = [dict(PAGE_TARGET, url="https://example.com/article#frag")]
        found = _find_target(targets, "https://example.com/article")
        assert found["url"] == "https://example.com/article#frag"

    def test_first_page_fallback(self):
        assert _find_target([OTHER_PAGE, PAGE_TARGET], "https://missing.dev/") is OTHER_PAGE

    def test_non_page_targets_ignored(self):
        assert _find_target([WORKER_TARGET], "https://example.com/") is None

    def test_empty_list_returns_none(self):
        assert _find_target([], "https://example.com/") is None

    def test_empty_url_matches_first_page(self):
        assert _find_target([OTHER_PAGE], "") is OTHER_PAGE


class TestCmd:
    async def test_success_returns_result(self):
        ws = _ws_for(_msg(7, result={"data": "QUJD"}))
        result = await _cmd(ws, 7, "Page.captureScreenshot", {"format": "jpeg"})
        assert result == {"data": "QUJD"}
        assert ws.sent == [
            {"id": 7, "method": "Page.captureScreenshot", "params": {"format": "jpeg"}}
        ]

    async def test_params_default_to_empty_dict(self):
        ws = _ws_for(_msg(1, result={}))
        await _cmd(ws, 1, "Page.enable")
        assert ws.sent[0]["params"] == {}

    async def test_skips_unrelated_events(self):
        ws = _ws_for(
            json.dumps({"method": "Page.domContentEventFired", "params": {}}),
            _msg(2, result={"ok": True}),
        )
        result = await _cmd(ws, 2, "Page.enable")
        assert result == {"ok": True}

    async def test_missing_result_defaults_to_empty(self):
        ws = _ws_for(_msg(3))
        assert await _cmd(ws, 3, "Page.enable") == {}

    async def test_error_response_raises(self):
        ws = _ws_for(_msg(4, error={"code": -32000, "message": "boom"}))
        with pytest.raises(RuntimeError, match=r"Page\.enable"):
            await _cmd(ws, 4, "Page.enable")


class TestCaptureB64:
    async def test_success_returns_screenshot_data(self, fake_network):
        fake_network["fake_ws"] = _ws_for(
            _msg(1, result={}),
            _msg(2, result={"data": "BASE64JPEG"}),
        )
        data = await screenshot.capture_b64("https://example.com/article")
        assert data == "BASE64JPEG"

    async def test_hits_cdp_json_endpoint_and_page_target(self, fake_network):
        fake_network["fake_ws"] = _ws_for(
            _msg(1, result={}),
            _msg(2, result={"data": "x"}),
        )
        await screenshot.capture_b64("https://example.com/article")
        assert fake_network["get_url"] == "http://127.0.0.1:9222/json"
        ws_mod = fake_network["ws"]
        assert ws_mod.connect_calls[0]["url"] == "ws://fake/devtools/page/1"
        assert ws_mod.connect_calls[0]["open_timeout"] == 10.0

    async def test_sends_quality_and_format(self, fake_network):
        ws = _ws_for(_msg(1, result={}), _msg(2, result={"data": "x"}))
        fake_network["fake_ws"] = ws
        await screenshot.capture_b64("https://example.com/article", jpeg_quality=80)
        shot = ws.sent[1]
        assert shot["method"] == "Page.captureScreenshot"
        assert shot["params"] == {"format": "jpeg", "quality": 80}

    async def test_skips_events_before_result(self, fake_network):
        fake_network["fake_ws"] = _ws_for(
            json.dumps({"method": "Network.requestWillBeSent", "params": {}}),
            _msg(1, result={}),
            _msg(2, result={"data": "EVT"}),
        )
        assert await screenshot.capture_b64("https://example.com/article") == "EVT"

    async def test_empty_url_uses_first_page(self, fake_network):
        fake_network["fake_ws"] = _ws_for(_msg(1, result={}), _msg(2, result={"data": "FIRST"}))
        assert await screenshot.capture_b64("") == "FIRST"
        assert fake_network["ws"].connect_calls[0]["url"] == "ws://fake/devtools/page/1"

    async def test_websockets_missing_raises(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "websockets", None)
        with pytest.raises(RuntimeError, match="websockets is required"):
            await screenshot.capture_b64("https://example.com/")

    async def test_cdp_unreachable_raises(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "websockets", FakeWebsockets(lambda: None))

        def boom(url, **kwargs):
            raise ConnectionError("refused")

        monkeypatch.setattr(httpx, "get", boom)
        with pytest.raises(RuntimeError, match="CDP endpoint unreachable"):
            await screenshot.capture_b64("https://example.com/")

    async def test_no_page_targets_raises(self, fake_network):
        fake_network["targets"] = [WORKER_TARGET]
        fake_network["fake_ws"] = _ws_for()
        with pytest.raises(RuntimeError, match="no page targets found over CDP"):
            await screenshot.capture_b64("https://example.com/")

    async def test_defensive_fallback_when_finder_returns_none(self, fake_network, monkeypatch):
        # _find_target normally never returns None while pages exist; the
        # `if target is None` fallback below it is defensive. Force that path.
        fake_network["fake_ws"] = _ws_for(_msg(1, result={}), _msg(2, result={"data": "FB"}))
        monkeypatch.setattr(screenshot, "_find_target", lambda targets, url: None)
        assert await screenshot.capture_b64("https://example.com/") == "FB"
        assert fake_network["ws"].connect_calls[0]["url"] == "ws://fake/devtools/page/1"

    async def test_cdp_error_inside_run_propagates(self, fake_network):
        fake_network["fake_ws"] = _ws_for(_msg(1, error={"message": "nope"}))
        with pytest.raises(RuntimeError, match=r"Page\.enable"):
            await screenshot.capture_b64("https://example.com/article")


class TestCaptureFullB64:
    def _metrics_ws(self, metrics: dict, data: str = "FULL"):
        return _ws_for(
            _msg(1, result={}),
            _msg(2, result=metrics),
            _msg(3, result={"data": data}),
        )

    async def test_success_uses_layout_metrics_clip(self, fake_network):
        fake_network["fake_ws"] = self._metrics_ws(
            {"cssContentSize": {"width": 2000, "height": 5000}}
        )
        data = await screenshot.capture_full_b64("https://example.com/article")
        assert data == "FULL"

    async def test_clip_and_capture_beyond_viewport(self, fake_network):
        ws = self._metrics_ws({"cssContentSize": {"width": 2000, "height": 5000}})
        fake_network["fake_ws"] = ws
        await screenshot.capture_full_b64("https://example.com/article")
        shot = ws.sent[2]
        assert shot["method"] == "Page.captureScreenshot"
        assert shot["params"]["captureBeyondViewport"] is True
        assert shot["params"]["clip"] == {
            "x": 0,
            "y": 0,
            "width": 2000,
            "height": 5000,
            "scale": 1,
        }

    async def test_huge_page_clamped_to_chromium_limit(self, fake_network):
        ws = self._metrics_ws({"cssContentSize": {"width": 99999, "height": 99999}})
        fake_network["fake_ws"] = ws
        await screenshot.capture_full_b64("https://example.com/article")
        clip = ws.sent[2]["params"]["clip"]
        assert clip["width"] == 16384
        assert clip["height"] == 16384

    async def test_falls_back_to_content_size(self, fake_network):
        ws = self._metrics_ws({"contentSize": {"width": 1400, "height": 900}})
        fake_network["fake_ws"] = ws
        await screenshot.capture_full_b64("https://example.com/article")
        clip = ws.sent[2]["params"]["clip"]
        assert (clip["width"], clip["height"]) == (1400, 900)

    async def test_missing_metrics_use_defaults(self, fake_network):
        ws = self._metrics_ws({})
        fake_network["fake_ws"] = ws
        await screenshot.capture_full_b64("https://example.com/article")
        clip = ws.sent[2]["params"]["clip"]
        assert (clip["width"], clip["height"]) == (1280, 800)

    async def test_falsy_metric_values_use_defaults(self, fake_network):
        ws = self._metrics_ws({"cssContentSize": {"width": 0, "height": 0}})
        fake_network["fake_ws"] = ws
        await screenshot.capture_full_b64("https://example.com/article")
        clip = ws.sent[2]["params"]["clip"]
        assert (clip["width"], clip["height"]) == (1280, 800)

    async def test_cdp_unreachable_raises(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "websockets", FakeWebsockets(lambda: None))

        def boom(url, **kwargs):
            raise ConnectionError("refused")

        monkeypatch.setattr(httpx, "get", boom)
        with pytest.raises(RuntimeError, match="CDP endpoint unreachable"):
            await screenshot.capture_full_b64("https://example.com/")

    async def test_no_page_targets_raises(self, fake_network):
        fake_network["targets"] = [WORKER_TARGET]
        fake_network["fake_ws"] = _ws_for()
        with pytest.raises(RuntimeError, match="no page targets found over CDP"):
            await screenshot.capture_full_b64("https://example.com/")

    async def test_websockets_missing_raises(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "websockets", None)
        with pytest.raises(RuntimeError, match="websockets is required"):
            await screenshot.capture_full_b64("https://example.com/")

    async def test_defensive_fallback_when_finder_returns_none(self, fake_network, monkeypatch):
        fake_network["fake_ws"] = self._metrics_ws(
            {"cssContentSize": {"width": 100, "height": 100}}, data="FB2"
        )
        monkeypatch.setattr(screenshot, "_find_target", lambda targets, url: None)
        assert await screenshot.capture_full_b64("https://example.com/") == "FB2"
        assert fake_network["ws"].connect_calls[0]["url"] == "ws://fake/devtools/page/1"
