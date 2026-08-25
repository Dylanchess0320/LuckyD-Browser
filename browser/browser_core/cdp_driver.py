"""Raw-CDP driver for the agent — trusted input events + screenshots.

Same interface as agent.JsDriver (snapshot / act), but clicks, keys and text
go through Chrome DevTools Protocol Input.* — REAL trusted events that
React/Vue/picky frameworks accept, unlike synthetic JS .click().

Connection notes (learned the hard way):
- Playwright's connect_over_cdp CANNOT attach to Qt WebEngine ("Browser
  context management is not supported"), so we talk to the PAGE target
  directly over a websocket — no browser-context commands anywhere.
- Qt WebEngine only accepts CDP on 127.0.0.1:9222 (set in main.py).
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import httpx

# Same JS payloads the JS driver uses (agent.py owns the protocol).
from browser_core.agent import (
    _HIGHLIGHT_JS,
    _SELECT_JS,
    _SNAPSHOT_JS,
)
from browser_core.screenshot import CDP_HTTP, _find_target

_KEY_CODES = {"Enter": (13, "\r"), "Tab": (9, ""), "Escape": (27, "")}

# Center coordinates of an indexed element, in viewport space.
_COORDS_JS = (
    '(() => {{ const e = document.querySelector("[data-ld-agent={i}]");'
    " if (!e) return null;"
    " e.scrollIntoView({{block:'center'}});"
    " const r = e.getBoundingClientRect();"
    " return JSON.stringify({{x: r.left + r.width / 2,"
    " y: r.top + r.height / 2}}); }})()"
)


class CdpPage:
    """One page target over a raw CDP websocket."""

    def __init__(self, ws):
        self._ws = ws
        self._mid = 0

    @classmethod
    async def connect(cls, url: str, timeout: float = 10.0) -> CdpPage:
        try:
            from websockets import connect
        except ImportError as exc:
            raise RuntimeError("pip install websockets") from exc
        resp = await asyncio.to_thread(httpx.get, CDP_HTTP + "/json", timeout=3.0)
        target = _find_target(resp.json(), url)
        if target is None:
            raise RuntimeError("no CDP page targets")
        ws = await asyncio.wait_for(
            connect(target["webSocketDebuggerUrl"], open_timeout=timeout),
            timeout=timeout,
        )
        page = cls(ws)
        await page.cmd("Page.enable")
        # Runtime.evaluate works without Runtime.enable on Qt WebEngine
        with contextlib.suppress(RuntimeError):
            await page.cmd("Runtime.enable")
        return page

    async def cmd(self, method: str, params: dict | None = None) -> dict:
        self._mid += 1
        await self._ws.send(json.dumps({"id": self._mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(await self._ws.recv())
            if msg.get("id") == self._mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def evaluate(self, expression: str):
        result = await self.cmd(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True},
        )
        return result.get("result", {}).get("value")

    async def screenshot_b64(self, quality: int = 60) -> str:
        result = await self.cmd("Page.captureScreenshot", {"format": "jpeg", "quality": quality})
        return result["data"]

    async def click_at(self, x: float, y: float) -> None:
        await self.cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        for event_type in ("mousePressed", "mouseReleased"):
            await self.cmd(
                "Input.dispatchMouseEvent",
                {"type": event_type, "x": x, "y": y, "button": "left", "clickCount": 1},
            )

    async def insert_text(self, text: str) -> None:
        await self.cmd("Input.insertText", {"text": text})

    async def press_key(self, key: str) -> None:
        vk, text = _KEY_CODES.get(key, _KEY_CODES["Enter"])
        for event_type in ("rawKeyDown", "keyUp"):
            await self.cmd(
                "Input.dispatchKeyEvent",
                {
                    "type": event_type,
                    "windowsVirtualKeyCode": vk,
                    "key": key,
                    "code": key,
                    "text": text,
                },
            )

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self._ws.close()


class CdpDriver:
    """Drop-in JsDriver replacement: snapshot via Runtime.evaluate, actions
    via trusted Input events (JS fallback for select/scroll/navigation)."""

    def __init__(self, page: CdpPage, vision: bool = False):
        self._page = page
        self._vision = vision

    @classmethod
    async def connect(cls, url: str, vision: bool = False, timeout: float = 8.0) -> CdpDriver:
        page = await CdpPage.connect(url, timeout=timeout)
        return cls(page, vision)

    async def snapshot(self, compact: bool = False) -> dict:
        max_els, max_text = (45, 800) if compact else (80, 1200)
        script = _SNAPSHOT_JS.replace("__MAXELS__", str(max_els)).replace(
            "__MAXTEXT__", str(max_text)
        )
        data = await self._page.evaluate(script)
        snap: dict = {}
        if isinstance(data, str):
            try:
                snap = json.loads(data)
            except json.JSONDecodeError:
                snap = {}
        for key, default in (
            ("url", ""),
            ("title", ""),
            ("elements", ""),
            ("text", ""),
            ("dialog", ""),
            ("more_below", False),
        ):
            snap.setdefault(key, default)
        if self._vision:
            # vision is best-effort; the DOM snapshot still works
            with contextlib.suppress(Exception):
                snap["shot_b64"] = await self._page.screenshot_b64()
        return snap

    async def act(self, action: dict) -> str:
        kind = action.get("action")
        if kind == "click":
            index = int(action.get("index", -1))
            await self._page.evaluate(_HIGHLIGHT_JS.format(i=index))
            await asyncio.sleep(0.15)
            coords = await self._coords(index)
            if coords is None:
                return "element not found"
            await self._page.click_at(coords["x"], coords["y"])
            await self._wait_loaded(timeout=2.5, initial=0.3)
            return f"trusted-click at ({coords['x']:.0f},{coords['y']:.0f})"
        if kind == "type":
            index = int(action.get("index", -1))
            text = str(action.get("text", ""))
            await self._page.evaluate(_HIGHLIGHT_JS.format(i=index))
            await asyncio.sleep(0.15)
            coords = await self._coords(index)
            if coords is None:
                return "element not found"
            await self._page.click_at(coords["x"], coords["y"])  # focus
            await self._page.evaluate(
                "const a = document.activeElement; if (a && a.select) a.select();"
            )
            await self._page.insert_text(text)  # replaces selected content
            await asyncio.sleep(0.2)
            return f"typed {len(text)} chars (trusted)"
        if kind == "press":
            key = str(action.get("text", "Enter"))
            if key not in _KEY_CODES:
                key = "Enter"
            await self._page.press_key(key)
            await self._wait_loaded(timeout=2.5, initial=0.35)
            return f"pressed {key} (trusted)"
        if kind == "select":
            index = int(action.get("index", -1))
            text = json.dumps(str(action.get("text", "")))
            await self._page.evaluate(_HIGHLIGHT_JS.format(i=index))
            await asyncio.sleep(0.15)
            result = await self._page.evaluate(_SELECT_JS.format(i=index, text=text))
            await asyncio.sleep(0.2)
            return str(result)
        if kind == "scroll":
            dy = -700 if str(action.get("text", "")).lower() == "up" else 700
            await self._page.evaluate(f"window.scrollBy(0, {dy})")
            await asyncio.sleep(0.2)
            return "scrolled"
        if kind == "navigate":
            url = str(action.get("url", ""))
            if url.startswith(("http://", "https://")):
                await self._page.evaluate(f"window.location.href = {json.dumps(url)}")
                await self._wait_loaded(timeout=8.0, initial=0.6)
                return f"navigated to {url}"
            return "refused: invalid url"
        if kind == "back":
            await self._page.evaluate("history.back()")
            await self._wait_loaded(timeout=4.0, initial=0.4)
            return "went back"
        if kind == "wait":
            try:
                secs = float(str(action.get("text", "1")) or 1)
            except ValueError:
                secs = 1.0
            secs = max(0.2, min(secs, 5.0))
            await asyncio.sleep(secs)
            return f"waited {secs:.1f}s"
        return "no-op"

    async def _coords(self, index: int) -> dict | None:
        raw = await self._page.evaluate(_COORDS_JS.format(i=index))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None

    async def _wait_loaded(self, timeout: float, initial: float) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        await asyncio.sleep(initial)
        while loop.time() < deadline:
            try:
                state = await asyncio.wait_for(self._page.evaluate("document.readyState"), 3)
            except Exception:
                state = None
            if state == "complete":
                return
            await asyncio.sleep(0.25)

    async def close(self) -> None:
        await self._page.close()
