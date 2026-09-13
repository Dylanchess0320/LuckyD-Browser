"""Night-4 browser-core audit: cdp_driver.py round 1.

No dedicated tests existed for CdpPage / CdpDriver. Everything here runs
against a scripted fake websocket — no browser, no network. Covers the
CDP message-framing loop (id matching, malformed-frame skipping, error
payloads), input-event command construction, and every CdpDriver.act
branch including validation and wait clamping.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

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

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from browser_core.cdp_driver import CdpDriver, CdpPage


class _FakeWS:
    """Scripted websocket: `frames` are popped by recv(); sends recorded."""

    def __init__(self, frames):
        self.frames = list(frames)
        self.sent = []
        self.closed = False

    async def send(self, payload):
        self.sent.append(json.loads(payload))

    async def recv(self):
        return self.frames.pop(0)

    async def close(self):
        self.closed = True


def _reply(mid, result):
    return json.dumps({"id": mid, "result": result})


@pytest.fixture()
def fast_sleep(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())


# ── CdpPage.cmd framing ──────────────────────────────────────────────


@pytest.mark.asyncio()
async def test_cmd_ignores_other_inflight_reply() -> None:
    """A reply for another message id is skipped, not consumed as ours."""
    ws = _FakeWS([_reply(2, {"v": 1}), _reply(1, {"v": 2})])
    page = CdpPage(ws)
    assert await page.cmd("M") == {"v": 2}
    assert page._mid == 1


@pytest.mark.asyncio()
async def test_cmd_skips_malformed_and_foreign_frames() -> None:
    ws = _FakeWS(["{not json", "[1,2]", _reply(999, {"x": 0}), _reply(1, {"ok": True})])
    page = CdpPage(ws)
    assert await page.cmd("M") == {"ok": True}
    assert page._mid == 1


@pytest.mark.asyncio()
async def test_cmd_error_payload_raises() -> None:
    ws = _FakeWS([json.dumps({"id": 1, "error": {"message": "nope"}})])
    page = CdpPage(ws)
    with pytest.raises(RuntimeError, match="nope"):
        await page.cmd("Runtime.evaluate")


@pytest.mark.asyncio()
async def test_cmd_non_dict_result_becomes_empty() -> None:
    ws = _FakeWS([json.dumps({"id": 1, "result": [1, 2]})])
    page = CdpPage(ws)
    assert await page.cmd("M") == {}


@pytest.mark.asyncio()
async def test_cmd_params_default_to_empty_object() -> None:
    ws = _FakeWS([_reply(1, {})])
    page = CdpPage(ws)
    await page.cmd("Page.enable")
    assert ws.sent[0]["params"] == {}
    assert ws.sent[0]["method"] == "Page.enable"


# ── evaluate / screenshot / input events ────────────────────────────


@pytest.mark.asyncio()
async def test_evaluate_unwraps_inner_value() -> None:
    ws = _FakeWS([_reply(1, {"result": {"type": "string", "value": "hi"}})])
    page = CdpPage(ws)
    assert await page.evaluate("1+1") == "hi"
    sent = ws.sent[0]
    assert sent["method"] == "Runtime.evaluate"
    assert sent["params"]["returnByValue"] is True


@pytest.mark.asyncio()
async def test_evaluate_non_dict_inner_is_none() -> None:
    ws = _FakeWS([_reply(1, {"result": 42})])
    page = CdpPage(ws)
    assert await page.evaluate("1+1") is None


@pytest.mark.asyncio()
async def test_screenshot_b64() -> None:
    ws = _FakeWS([_reply(1, {"data": "Zm9v"})])
    page = CdpPage(ws)
    assert await page.screenshot_b64() == "Zm9v"
    assert ws.sent[0]["params"] == {"format": "jpeg", "quality": 60}


@pytest.mark.asyncio()
async def test_screenshot_b64_non_string_is_empty() -> None:
    ws = _FakeWS([_reply(1, {"data": None})])
    page = CdpPage(ws)
    assert await page.screenshot_b64() == ""


@pytest.mark.asyncio()
async def test_click_at_sends_move_press_release() -> None:
    ws = _FakeWS([_reply(1, {}), _reply(2, {}), _reply(3, {})])
    page = CdpPage(ws)
    await page.click_at(10.0, 20.0)
    types = [s["params"]["type"] for s in ws.sent]
    assert types == ["mouseMoved", "mousePressed", "mouseReleased"]
    assert ws.sent[1]["params"]["button"] == "left"
    assert ws.sent[1]["params"]["clickCount"] == 1
    assert (ws.sent[0]["params"]["x"], ws.sent[0]["params"]["y"]) == (10.0, 20.0)


@pytest.mark.asyncio()
async def test_insert_text() -> None:
    ws = _FakeWS([_reply(1, {})])
    page = CdpPage(ws)
    await page.insert_text("hello")
    assert ws.sent[0]["method"] == "Input.insertText"
    assert ws.sent[0]["params"] == {"text": "hello"}


@pytest.mark.asyncio()
async def test_press_key_known_code() -> None:
    ws = _FakeWS([_reply(1, {}), _reply(2, {})])
    page = CdpPage(ws)
    await page.press_key("Tab")
    types = [s["params"]["type"] for s in ws.sent]
    assert types == ["rawKeyDown", "keyUp"]
    assert ws.sent[0]["params"]["windowsVirtualKeyCode"] == 9
    assert ws.sent[0]["params"]["text"] == ""


@pytest.mark.asyncio()
async def test_press_key_unknown_falls_back_to_enter() -> None:
    ws = _FakeWS([_reply(1, {}), _reply(2, {})])
    page = CdpPage(ws)
    await page.press_key("F12")
    assert ws.sent[0]["params"]["windowsVirtualKeyCode"] == 13
    assert ws.sent[0]["params"]["key"] == "F12"  # original key name kept


@pytest.mark.asyncio()
async def test_close_suppresses_ws_errors() -> None:
    class _BadWS(_FakeWS):
        async def close(self):
            raise RuntimeError("gone")

    page = CdpPage(_BadWS([]))
    await page.close()  # must not raise


# ── CdpPage.connect ──────────────────────────────────────────────────


@pytest.mark.asyncio()
async def test_connect_no_targets_raises(monkeypatch) -> None:
    import browser_core.cdp_driver as cdp_mod

    class _Resp:
        def json(self):
            return []

    monkeypatch.setattr(cdp_mod.httpx, "get", lambda *a, **k: _Resp())
    monkeypatch.setattr(cdp_mod, "_find_target", lambda targets, url: None)
    with pytest.raises(RuntimeError, match="no CDP page targets"):
        await CdpPage.connect("http://127.0.0.1:9222")


@pytest.mark.asyncio()
async def test_connect_missing_websockets_package(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "websockets", None)
    with pytest.raises(RuntimeError, match="pip install websockets"):
        await CdpPage.connect("http://127.0.0.1:9222")


@pytest.mark.asyncio()
async def test_connect_happy_path(monkeypatch) -> None:
    import browser_core.cdp_driver as cdp_mod

    class _Resp:
        def json(self):
            return [{"webSocketDebuggerUrl": "ws://x/devtools"}]

    monkeypatch.setattr(cdp_mod.httpx, "get", lambda *a, **k: _Resp())
    # cdp_driver binds _find_target at import: patch it in ITS namespace.
    monkeypatch.setattr(
        cdp_mod, "_find_target", lambda targets, url: targets[0] if targets else None
    )

    async def _connect(url, open_timeout=None):
        return _FakeWS(
            [_reply(1, {}), json.dumps({"id": 2, "result": {}})]  # Page.enable, Runtime.enable
        )

    monkeypatch.setitem(sys.modules, "websockets", MagicMock(connect=_connect))
    page = await CdpPage.connect("http://127.0.0.1:9222")
    methods = [s["method"] for s in page._ws.sent]
    assert methods[:2] == ["Page.enable", "Runtime.enable"]


# ── CdpDriver.snapshot ───────────────────────────────────────────────


def _driver_for(page) -> CdpDriver:
    return CdpDriver(page)


@pytest.mark.asyncio()
async def test_snapshot_parses_json_string_and_defaults(fast_sleep) -> None:
    payload = json.dumps({"url": "https://x.com"})
    page = CdpPage(_FakeWS([_reply(1, {"result": {"value": payload}})]))
    drv = _driver_for(page)
    snap = await drv.snapshot()
    assert snap["url"] == "https://x.com"
    assert snap["title"] == "" and snap["elements"] == ""
    assert snap["more_below"] is False


@pytest.mark.asyncio()
async def test_snapshot_compact_uses_small_dims(fast_sleep) -> None:
    page = CdpPage(_FakeWS([_reply(1, {"result": {"value": "{}"}})]))
    drv = _driver_for(page)
    await drv.snapshot(compact=True)
    expr = page._ws.sent[0]["params"]["expression"]
    assert "45" in expr and "800" in expr and "__MAXELS__" not in expr


@pytest.mark.asyncio()
async def test_snapshot_full_uses_large_dims(fast_sleep) -> None:
    page = CdpPage(_FakeWS([_reply(1, {"result": {"value": "{}"}})]))
    drv = _driver_for(page)
    await drv.snapshot()
    expr = page._ws.sent[0]["params"]["expression"]
    assert "80" in expr and "1200" in expr


@pytest.mark.asyncio()
async def test_snapshot_malformed_json_becomes_empty(fast_sleep) -> None:
    page = CdpPage(_FakeWS([_reply(1, {"result": {"value": "{oops"}})]))
    drv = _driver_for(page)
    snap = await drv.snapshot()
    assert snap["url"] == "" and snap["elements"] == ""


@pytest.mark.asyncio()
async def test_snapshot_non_string_value_becomes_empty(fast_sleep) -> None:
    page = CdpPage(_FakeWS([_reply(1, {"result": {"value": 123}})]))
    drv = _driver_for(page)
    assert (await drv.snapshot())["url"] == ""


@pytest.mark.asyncio()
async def test_snapshot_vision_attaches_screenshot(fast_sleep) -> None:
    page = CdpPage(_FakeWS([_reply(1, {"result": {"value": "{}"}}), _reply(2, {"data": "Zm9v"})]))
    drv = CdpDriver(page, vision=True)
    snap = await drv.snapshot()
    assert snap["shot_b64"] == "Zm9v"


@pytest.mark.asyncio()
async def test_snapshot_vision_failure_suppressed(fast_sleep) -> None:
    class _ShotBoomWS(_FakeWS):
        async def send(self, payload):
            if "captureScreenshot" in payload:
                raise RuntimeError("ws dead")
            return await super().send(payload)

    ws = _ShotBoomWS([_reply(1, {"result": {"value": "{}"}})])
    drv = CdpDriver(CdpPage(ws), vision=True)
    snap = await drv.snapshot()
    assert "shot_b64" not in snap and snap["url"] == ""


# ── CdpDriver.act branches ───────────────────────────────────────────


def _act_driver(frames, vision=False) -> tuple[CdpDriver, _FakeWS]:
    ws = _FakeWS(frames)
    return CdpDriver(CdpPage(ws), vision=vision), ws


@pytest.mark.asyncio()
async def test_act_click_trusted(fast_sleep) -> None:
    coords = json.dumps({"x": 100.0, "y": 200.0})
    drv, ws = _act_driver(
        [
            _reply(1, {}),  # highlight
            _reply(2, {"result": {"value": coords}}),  # _coords
            _reply(3, {}),  # mouseMoved
            _reply(4, {}),  # mousePressed
            _reply(5, {}),  # mouseReleased
            _reply(6, {"result": {"value": "complete"}}),  # _wait_loaded
        ]
    )
    result = await drv.act({"action": "click", "index": 2})
    assert result == "trusted-click at (100,200)"
    assert ws.sent[2]["method"] == "Input.dispatchMouseEvent"


@pytest.mark.asyncio()
async def test_act_click_element_not_found(fast_sleep) -> None:
    drv, _ = _act_driver(
        [
            _reply(1, {}),
            _reply(2, {"result": {"value": "null"}}),  # _coords → None
        ]
    )
    assert await drv.act({"action": "click", "index": 9}) == "element not found"


@pytest.mark.asyncio()
async def test_act_coords_malformed_is_not_found(fast_sleep) -> None:
    drv, _ = _act_driver(
        [
            _reply(1, {}),
            _reply(2, {"result": {"value": "{bad"}}),
        ]
    )
    assert await drv.act({"action": "click", "index": 9}) == "element not found"


@pytest.mark.asyncio()
async def test_act_coords_missing_xy_is_not_found(fast_sleep) -> None:
    drv, _ = _act_driver(
        [
            _reply(1, {}),
            _reply(2, {"result": {"value": json.dumps({"x": 1})}}),
        ]
    )
    assert await drv.act({"action": "click", "index": 9}) == "element not found"


@pytest.mark.asyncio()
async def test_act_type(fast_sleep) -> None:
    coords = json.dumps({"x": 5.0, "y": 6.0})
    drv, ws = _act_driver(
        [
            _reply(1, {}),  # highlight
            _reply(2, {"result": {"value": coords}}),  # _coords
            _reply(3, {}),  # click_at move
            _reply(4, {}),  # click_at press
            _reply(5, {}),  # click_at release
            _reply(6, {}),  # select-all evaluate
            _reply(7, {}),  # insertText
        ]
    )
    result = await drv.act({"action": "type", "index": 1, "text": "hello"})
    assert result == "typed 5 chars (trusted)"
    assert ws.sent[6]["method"] == "Input.insertText"


@pytest.mark.asyncio()
async def test_act_press_unknown_key_becomes_enter(fast_sleep) -> None:
    drv, ws = _act_driver(
        [
            _reply(1, {}),  # rawKeyDown
            _reply(2, {}),  # keyUp
            _reply(3, {"result": {"value": "complete"}}),  # _wait_loaded
        ]
    )
    result = await drv.act({"action": "press", "text": "F7"})
    assert result == "pressed Enter (trusted)"
    assert ws.sent[0]["params"]["windowsVirtualKeyCode"] == 13


@pytest.mark.asyncio()
async def test_act_press_escape(fast_sleep) -> None:
    drv, ws = _act_driver(
        [
            _reply(1, {}),
            _reply(2, {}),
            _reply(3, {"result": {"value": "complete"}}),
        ]
    )
    assert await drv.act({"action": "press", "text": "Escape"}) == "pressed Escape (trusted)"
    assert ws.sent[0]["params"]["windowsVirtualKeyCode"] == 27


@pytest.mark.asyncio()
async def test_act_select(fast_sleep) -> None:
    drv, _ = _act_driver([_reply(1, {}), _reply(2, {"result": {"value": "selected A"}})])
    result = await drv.act({"action": "select", "index": 3, "text": "Option A"})
    assert result == "selected A"


@pytest.mark.asyncio()
async def test_act_scroll_up_and_down(fast_sleep) -> None:
    drv, ws = _act_driver([_reply(1, {}), _reply(2, {})])
    assert await drv.act({"action": "scroll", "text": "up"}) == "scrolled"
    assert "scrollBy(0, -700)" in ws.sent[0]["params"]["expression"]
    assert await drv.act({"action": "scroll"}) == "scrolled"
    assert "scrollBy(0, 700)" in ws.sent[1]["params"]["expression"]


@pytest.mark.asyncio()
async def test_act_navigate_valid(fast_sleep) -> None:
    drv, ws = _act_driver(
        [
            _reply(1, {}),  # location.href
            _reply(2, {"result": {"value": "complete"}}),  # _wait_loaded
        ]
    )
    result = await drv.act({"action": "navigate", "url": "https://example.com/"})
    assert result == "navigated to https://example.com/"
    assert "https://example.com/" in ws.sent[0]["params"]["expression"]


@pytest.mark.asyncio()
async def test_act_navigate_refused(fast_sleep) -> None:
    drv, ws = _act_driver([])
    assert await drv.act({"action": "navigate", "url": "javascript:alert(1)"}) == (
        "refused: invalid url"
    )
    assert ws.sent == []


@pytest.mark.asyncio()
async def test_act_back(fast_sleep) -> None:
    drv, ws = _act_driver([_reply(1, {}), _reply(2, {"result": {"value": "complete"}})])
    assert await drv.act({"action": "back"}) == "went back"
    assert "history.back()" in ws.sent[0]["params"]["expression"]


@pytest.mark.asyncio()
async def test_act_wait_clamps(fast_sleep) -> None:
    import browser_core.cdp_driver as cdp_mod

    drv, _ = _act_driver([])
    sleeps = []
    orig = cdp_mod.asyncio.sleep

    async def _rec(secs):
        sleeps.append(secs)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(cdp_mod.asyncio, "sleep", _rec)
    try:
        assert await drv.act({"action": "wait", "text": "99"}) == "waited 5.0s"
        assert await drv.act({"action": "wait", "text": "0"}) == "waited 0.2s"
        assert await drv.act({"action": "wait", "text": "oops"}) == "waited 1.0s"
    finally:
        monkey.setattr(cdp_mod.asyncio, "sleep", orig)
    assert sleeps == [5.0, 0.2, 1.0]


@pytest.mark.asyncio()
async def test_act_unknown_is_noop(fast_sleep) -> None:
    drv, ws = _act_driver([])
    assert await drv.act({"action": "dance"}) == "no-op"
    assert ws.sent == []


@pytest.mark.asyncio()
async def test_wait_loaded_returns_on_complete(fast_sleep) -> None:
    drv, _ = _act_driver([_reply(1, {"result": {"value": "complete"}})])
    await drv._wait_loaded(timeout=5.0, initial=0.0)  # fast path, no loop iterations


@pytest.mark.asyncio()
async def test_wait_loaded_tolerates_js_errors(fast_sleep) -> None:
    drv, _ = _act_driver([])
    page = drv._page

    async def _boom(expr):
        raise RuntimeError("context torn down")

    page.evaluate = _boom
    await drv._wait_loaded(timeout=0.1, initial=0.0)  # deadline hit, no raise


@pytest.mark.asyncio()
async def test_driver_close_closes_page() -> None:
    drv, ws = _act_driver([])
    await drv.close()
    assert ws.closed
