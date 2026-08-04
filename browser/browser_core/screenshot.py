"""Screenshot the visible tab via raw Chrome DevTools Protocol.

Qt WebEngine composites pages on the GPU, so QWidget.grab() returns blank
frames on GPU machines. main.py already exposes CDP on 127.0.0.1:9222 —
talking to the *page target* directly (never the browser target: Qt only
implements a subset of CDP, no Browser-context commands) returns real pixels.

This module is also the foundation for any future raw-CDP driver — note
that Playwright's connect_over_cdp can NOT attach to Qt WebEngine, but this
page-target approach works fine.
"""

from __future__ import annotations

import asyncio
import json

import httpx

CDP_HTTP = "http://127.0.0.1:9222"


async def _cmd(ws, mid: int, method: str, params: dict | None = None) -> dict:
    await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == mid:
            if "error" in msg:
                raise RuntimeError(f"{method}: {msg['error']}")
            return msg.get("result", {})


def _find_target(targets: list[dict], url: str) -> dict | None:
    pages = [t for t in targets if t.get("type") == "page"]
    for t in pages:  # exact match first
        if t.get("url") == url:
            return t
    for t in pages:  # then prefix (trailing slashes / fragments)
        if url and t.get("url", "").startswith(url):
            return t
    return pages[0] if pages else None


async def capture_b64(url: str, *, jpeg_quality: int = 60, timeout: float = 10.0) -> str:
    """Base64 JPEG of the tab showing `url` ("" matches the first page)."""
    try:
        from websockets import connect
    except ImportError as exc:
        raise RuntimeError("websockets is required: pip install websockets") from exc
    try:
        resp = httpx.get(CDP_HTTP + "/json", timeout=3.0)
        targets = resp.json()
    except Exception as exc:
        raise RuntimeError(f"CDP endpoint unreachable: {exc}") from exc
    target = _find_target(targets, url)
    if target is None:
        # Fall back to the first page target regardless of URL
        pages = [t for t in targets if t.get("type") == "page"]
        if not pages:
            raise RuntimeError("no page targets found over CDP")
        target = pages[0]

    async def _run() -> str:
        async with connect(target["webSocketDebuggerUrl"], open_timeout=timeout) as ws:
            await _cmd(ws, 1, "Page.enable")
            await asyncio.sleep(0.5)  # longer wait for compositor
            result = await _cmd(
                ws,
                2,
                "Page.captureScreenshot",
                {"format": "jpeg", "quality": jpeg_quality},
            )
            return result["data"]

    return await asyncio.wait_for(_run(), timeout=timeout)
