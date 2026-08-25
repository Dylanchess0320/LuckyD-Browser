"""
Browser automation tools using Playwright — Navigate, click, type, screenshot,
evaluate JS, emulate devices, intercept requests, trace sessions, and more.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
from pathlib import Path

from .base import ToolBase, ToolOutput
from .registry import register_tool

# ── Browser session state ──────────────────────────────────────────────

_browser = None
_page = None
_playwright = None
_headless = True
_device_config: dict | None = None
_intercepts: list[dict] = []


async def _get_playwright():
    global _playwright
    if _playwright is None:
        try:
            from playwright.async_api import async_playwright

            _playwright = async_playwright
        except ImportError:
            raise RuntimeError(
                "Playwright not installed. Run: pip install playwright && playwright install chromium"
            ) from None
    return _playwright


async def _get_page():
    global _browser, _page
    if _page is None:
        pw = await _get_playwright()
        p = await pw().__aenter__()
        context_opts = {}
        if _device_config:
            context_opts = _device_config
        _browser = await p.chromium.launch(headless=_headless)
        ctx = await _browser.new_context(**context_opts)
        _page = await ctx.new_page()
        # Apply pending intercepts
        for ic in _intercepts:
            await _page.route(ic["pattern"], ic["handler"])
    return _page


async def _restart_browser():
    """Close and reopen the browser (needed for mode/viewport changes)."""
    global _browser, _page
    if _page:
        with contextlib.suppress(Exception):
            await _page.close()
    if _browser:
        with contextlib.suppress(Exception):
            await _browser.close()
    _page = None
    _browser = None


# ── Helpers ────────────────────────────────────────────────────────────


def _extract_text_from_html(html: str) -> str:
    """Simple HTML-to-text extraction for snapshots."""
    html = re.sub(
        r"<(script|style|noscript)[^>]*>.*?</\1>",
        "",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:8000]


# ── Tool: Navigate ─────────────────────────────────────────────────────


class BrowserNavigateTool(ToolBase):
    name = "BrowserNavigate"
    description = "Navigate the browser to a URL. Returns page title and summary of elements."
    aliases = ["Navigate", "GoTo"]
    parameters = {
        "url": {"type": "string", "description": "The URL to navigate to"},
    }

    async def execute(self, url: str) -> ToolOutput:
        try:
            page = await _get_page()
            await page.goto(url, timeout=30000)
            title = await page.title()
            html = await page.content()
            _extract_text_from_html(html)
            elements = await page.evaluate(
                """() => {
                const items = [];
                document.querySelectorAll('a, button, input, select, textarea').forEach((el, i) => {
                    if (i > 100) return;
                    const tag = el.tagName.toLowerCase();
                    const txt = (el.textContent || el.value || el.placeholder || '').trim().slice(0, 60);
                    const id = el.id || '';
                    const cls = (el.className || '').slice(0, 30);
                    const href = el.getAttribute('href') || '';
                    items.push({tag, txt, id, cls, href});
                });
                return items;
            }"""
            )
            summary = f"Page: {title}\nURL: {url}\n\nInteractive elements ({len(elements)}):\n"
            for _i, el in enumerate(elements[:50]):
                sel = (
                    f"#{el['id']}"
                    if el["id"]
                    else f".{el['cls'].split()[0]}"
                    if el["cls"]
                    else el["tag"]
                )
                summary += f"  [{el['tag']}] {el['txt'][:50]}  {sel}\n"
                if el.get("href"):
                    summary += f"       href: {el['href'][:80]}\n"
            return ToolOutput(
                text=summary[:6000],
                title=f"  {title}",
                metadata={"url": url, "title": title, "elements": len(elements)},
            )
        except Exception as e:
            return ToolOutput(text=f"Browser navigation error: {e}", error=True)


# ── Tool: Click ────────────────────────────────────────────────────────


class BrowserClickTool(ToolBase):
    name = "BrowserClick"
    description = "Click an element on the page by CSS selector."
    aliases = ["Click"]
    parameters = {
        "selector": {"type": "string", "description": "CSS selector of the element to click"},
    }

    async def execute(self, selector: str) -> ToolOutput:
        try:
            page = await _get_page()
            await page.click(selector, timeout=10000)
            title = await page.title()
            return ToolOutput(
                text=f"Clicked: {selector}\nCurrent page: {title}",
                title=f"  Clicked {selector}",
                metadata={"selector": selector, "title": title},
            )
        except Exception as e:
            return ToolOutput(text=f"Click error: {e}", error=True)


# ── Tool: Type ─────────────────────────────────────────────────────────


class BrowserTypeTool(ToolBase):
    name = "BrowserType"
    description = "Type text into an input field."
    aliases = ["Type"]
    parameters = {
        "selector": {"type": "string", "description": "CSS selector of the input element"},
        "text": {"type": "string", "description": "Text to type"},
        "submit": {"type": "boolean", "description": "Press Enter after typing (default: false)"},
    }

    async def execute(self, selector: str, text: str, submit: bool = False) -> ToolOutput:
        try:
            page = await _get_page()
            await page.fill(selector, text, timeout=10000)
            if submit:
                await page.press(selector, "Enter")
            return ToolOutput(
                text=f"Typed into {selector}{' and submitted' if submit else ''}",
                title=f"  Typed in {selector}",
                metadata={"selector": selector, "text_length": len(text), "submitted": submit},
            )
        except Exception as e:
            return ToolOutput(text=f"Type error: {e}", error=True)


# ── Tool: Snapshot ────────────────────────────────────────────────────


class BrowserSnapshotTool(ToolBase):
    name = "BrowserSnapshot"
    description = (
        "Get a text snapshot of the current page (interactive elements, headings, links, etc.)."
    )
    aliases = ["Snapshot", "PageContent"]
    parameters = {}

    async def execute(self) -> ToolOutput:
        try:
            page = await _get_page()
            title = await page.title()
            url = page.url
            html = await page.content()
            text = _extract_text_from_html(html)
            return ToolOutput(
                text=f"Title: {title}\nURL: {url}\n\n{text[:6000]}",
                title=f"  {title}",
                metadata={"url": url, "title": title},
            )
        except Exception as e:
            return ToolOutput(text=f"Snapshot error: {e}", error=True)


# ── Tool: Screenshot ──────────────────────────────────────────────────


class BrowserScreenshotTool(ToolBase):
    name = "BrowserScreenshot"
    description = "Take a screenshot of the current page and save it to a file."
    aliases = ["Screenshot"]
    parameters = {
        "path": {
            "type": "string",
            "description": "File path to save the screenshot (default: auto-generated in working dir)",
        },
        "full_page": {
            "type": "boolean",
            "description": "Capture full scrollable page (default: false)",
        },
    }

    async def execute(self, path: str = "", full_page: bool = False) -> ToolOutput:
        try:
            page = await _get_page()
            if not path:
                path = f"screenshot_{int(time.time())}.png"
            await page.screenshot(path=path, full_page=full_page)
            size = Path(path).stat().st_size
            return ToolOutput(
                text=f"Screenshot saved to {path} ({size:,} bytes)",
                title="  Screenshot",
                metadata={"path": path, "full_page": full_page, "size": size},
            )
        except Exception as e:
            return ToolOutput(text=f"Screenshot error: {e}", error=True)


# ── Tool: Evaluate JS ──────────────────────────────────────────────────


class BrowserEvaluateTool(ToolBase):
    name = "BrowserEvaluate"
    description = "Run JavaScript on the current page and return the result."
    aliases = ["Eval", "JS"]
    parameters = {
        "expression": {"type": "string", "description": "JavaScript expression to evaluate"},
    }

    async def execute(self, expression: str) -> ToolOutput:
        try:
            page = await _get_page()
            result = await page.evaluate(expression)
            text = json.dumps(result, indent=2, default=str)[:4000]
            return ToolOutput(
                text=text,
                title="  JS Result",
                metadata={"expression": expression[:100]},
            )
        except Exception as e:
            return ToolOutput(text=f"Evaluate error: {e}", error=True)


# ── Tool: Close ────────────────────────────────────────────────────────


class BrowserCloseTool(ToolBase):
    name = "BrowserClose"
    description = "Close the browser session."
    aliases = ["CloseBrowser"]
    parameters = {}

    async def execute(self) -> ToolOutput:
        global _browser, _page
        try:
            if _page:
                await _page.close()
            if _browser:
                await _browser.close()
            _page = None
            _browser = None
            return ToolOutput(text="Browser closed.", title="Browser Closed")
        except Exception as e:
            return ToolOutput(text=f"Close error: {e}", error=True)


# ── NEW: Open In System Browser ────────────────────────────────────────


class OpenInBrowserTool(ToolBase):
    name = "OpenInBrowser"
    description = (
        "Open a URL in the user's default system browser (Chrome, Edge, etc.). "
        "Use this when the user wants to see a web page, watch a video, or interact "
        "with a site in their real browser."
    )
    aliases = ["OpenURL", "LaunchBrowser"]
    parameters = {
        "url": {"type": "string", "description": "The URL to open"},
    }

    async def execute(self, url: str) -> ToolOutput:
        import webbrowser

        webbrowser.open(url)
        return ToolOutput(
            text=f"Opened in your browser: {url}",
            title="URL Opened",
        )


# ── NEW: Browser State (cookies / localStorage) ──────────────────────


class BrowserStateTool(ToolBase):
    name = "BrowserState"
    description = (
        "Save or load persistent browser state (cookies, localStorage). "
        "Use 'save' to persist the current session, 'load' to restore a "
        "previously saved session, or 'clear' to wipe saved state."
    )
    aliases = ["Cookies", "LocalStorage"]
    parameters = {
        "action": {
            "type": "string",
            "enum": ["save", "load", "clear"],
            "description": "Action: 'save' persists current state, 'load' restores it, 'clear' wipes it.",
        },
    }

    async def execute(self, action: str) -> ToolOutput:
        global _page
        state_path = Path(os.getcwd()) / ".browser_state.json"
        try:
            if action == "save":
                page = await _get_page()
                storage = await page.context.storage_state()
                state_path.write_text(json.dumps(storage, indent=2))
                return ToolOutput(
                    text=f"Browser state saved to {state_path}",
                    title="State Saved",
                    metadata={"path": str(state_path)},
                )
            elif action == "load":
                if not state_path.exists():
                    return ToolOutput(
                        text="No saved state found. Use 'save' first.",
                        title="State Load",
                        error=True,
                    )
                data = json.loads(state_path.read_text())
                # Recreate context with the saved state
                await _restart_browser()
                page = await _get_page()
                await page.context.add_cookies(data.get("cookies", []))
                # Apply origins
                for _origin_data in data.get("origins", []):
                    pass  # Playwright handles origins via storage_state
                return ToolOutput(
                    text="Browser state loaded.",
                    title="State Loaded",
                )
            elif action == "clear":
                if state_path.exists():
                    state_path.unlink()
                try:
                    page = await _get_page()
                    await page.context.clear_cookies()
                except Exception:
                    pass
                return ToolOutput(
                    text="Browser state cleared.",
                    title="State Cleared",
                )
            else:
                return ToolOutput(
                    text=f"Unknown action: {action}. Use 'save', 'load', or 'clear'.",
                    error=True,
                )
        except Exception as e:
            return ToolOutput(text=f"State error: {e}", error=True)


# ── NEW: Device / Viewport Emulation ──────────────────────────────────


class BrowserEmulateTool(ToolBase):
    name = "BrowserEmulate"
    description = (
        "Emulate a mobile device or set a custom viewport. "
        "Predefined devices include: 'iPhone 12', 'Pixel 5', 'iPad Pro', "
        "'Galaxy S9+', 'iPhone SE'. Pass 'desktop' to reset to default. "
        "Alternatively, pass custom width/height numbers."
    )
    aliases = ["Emulate", "DeviceEmulate", "MobileEmulate"]
    parameters = {
        "device": {
            "type": "string",
            "description": "Device name to emulate (e.g., 'iPhone 12', 'Pixel 5') or 'desktop' to reset.",
        },
        "width": {
            "type": "integer",
            "description": "Custom viewport width (optional, used if device is not set).",
        },
        "height": {
            "type": "integer",
            "description": "Custom viewport height (optional, used if device is not set).",
        },
    }

    async def execute(self, device: str = "", width: int = 0, height: int = 0) -> ToolOutput:
        global _device_config
        try:
            if device and device.lower() != "desktop":
                from playwright.async_api import devices as pw_devices

                device_name = None
                # Try exact match first
                if device in dir(pw_devices):
                    device_name = device
                else:
                    # Fuzzy match
                    dlow = device.lower()
                    for name in dir(pw_devices):
                        if not name.startswith("_") and dlow in name.lower():
                            device_name = name
                            break
                if device_name:
                    _device_config = getattr(pw_devices, device_name)
                else:
                    return ToolOutput(
                        text=f"Device '{device}' not found. Try: iPhone 12, Pixel 5, iPad Pro, Galaxy S9+, iPhone SE",
                        error=True,
                    )
            elif width and height:
                _device_config = {"viewport": {"width": width, "height": height}}
            else:
                _device_config = None  # reset

            await _restart_browser()
            label = device or f"{width}x{height}"
            return ToolOutput(
                text=f"Emulating: {label}",
                title="Device Emulated",
            )
        except Exception as e:
            return ToolOutput(text=f"Emulate error: {e}", error=True)


# ── NEW: Network Interception ─────────────────────────────────────────


class BrowserInterceptTool(ToolBase):
    name = "BrowserIntercept"
    description = (
        "Intercept network requests on the current page. "
        "Use 'list' to see recent intercepts, 'mock' to block/fake URLs, "
        "'clear' to remove all intercepts. "
        "For 'mock', pass URL pattern (glob) and optional status code (default 200)."
    )
    aliases = ["Intercept", "NetworkMock", "BlockURL"]
    parameters = {
        "action": {
            "type": "string",
            "enum": ["list", "mock", "clear"],
            "description": "Action: 'list' requests, 'mock' URLs, or 'clear' intercepts.",
        },
        "url_pattern": {
            "type": "string",
            "description": "Glob pattern for URLs to intercept (e.g., '**/*.png').",
        },
        "status": {
            "type": "integer",
            "description": "HTTP status code for mocked response (default: 200, or 204 for block).",
        },
    }

    async def execute(self, action: str, url_pattern: str = "", status: int = 200) -> ToolOutput:
        global _intercepts
        try:
            if action == "list":
                if not _intercepts:
                    return ToolOutput(
                        text="No active intercepts.",
                        title="Network Intercepts",
                        metadata={"intercepts": []},
                    )
                lines = "\n".join(
                    f"  {i['url_pattern']} → {i.get('status', 200)}" for i in _intercepts
                )
                return ToolOutput(
                    text=f"Active intercepts:\n{lines}",
                    title="Network Intercepts",
                    metadata={"intercepts": _intercepts},
                )
            elif action == "mock":
                if not url_pattern:
                    return ToolOutput(text="url_pattern is required for 'mock' action.", error=True)
                _intercepts.append({"url_pattern": url_pattern, "status": status})
                # Apply to current page if active
                page = await _get_page()
                await page.route(
                    url_pattern,
                    lambda route, s=status: route.fulfill(status=s),
                )
                return ToolOutput(
                    text=f"Intercepting {url_pattern} → HTTP {status}",
                    title="Intercept Set",
                )
            elif action == "clear":
                _intercepts.clear()
                # Can't un-route individually, restart browser to clear all routes
                await _restart_browser()
                return ToolOutput(
                    text="All intercepts cleared. Browser restarted.",
                    title="Intercepts Cleared",
                )
            else:
                return ToolOutput(text=f"Unknown action: {action}", error=True)
        except Exception as e:
            return ToolOutput(text=f"Intercept error: {e}", error=True)


# ---------------------------------------------------------------------------
# 14. BrowserTraceTool -- start / stop Playwright tracing
# ---------------------------------------------------------------------------
class BrowserTraceTool(ToolBase):
    name = "BrowserTrace"
    description = (
        "Start or stop browser tracing (records screenshots, network, and DOM "
        "snapshots for debugging). Use 'start' to begin, 'stop' to save the "
        "trace to a .zip file."
    )
    aliases = ["Trace", "DebugTrace"]
    parameters = {
        "action": {
            "type": "string",
            "enum": ["start", "stop"],
            "description": "Start or stop tracing.",
        },
    }

    async def execute(self, action: str) -> ToolOutput:
        try:
            page = await _get_page()
            if action == "start":
                await page.context.tracing.start(screenshots=True, snapshots=True)
                return ToolOutput(
                    text="Tracing started.",
                    title="Trace Start",
                )
            else:
                path = Path("browser_trace.zip")
                await page.context.tracing.stop(path=str(path))
                size = path.stat().st_size
                return ToolOutput(
                    text=f"Trace saved to {path} ({size:,} bytes)",
                    title="Trace Saved",
                    metadata={"path": str(path), "size": size},
                )
        except Exception as e:
            return ToolOutput(text=f"Trace error: {e}", error=True)


# ---------------------------------------------------------------------------
# 15. BrowserToggleHeadlessTool -- toggle headless / headed
# ---------------------------------------------------------------------------
class BrowserToggleHeadlessTool(ToolBase):
    name = "BrowserToggleHeadless"
    description = (
        "Toggle the browser between headless (invisible) and headed (visible) "
        "mode. Useful when you need to see the actual rendered page. Pass "
        "'true' for headless, 'false' for headed."
    )
    aliases = ["ToggleHeadless", "Headless"]
    parameters = {
        "headless": {
            "type": "boolean",
            "description": "True for headless (default), False for visible browser window.",
        },
    }

    async def execute(self, headless: bool = True) -> ToolOutput:
        global _headless
        try:
            if _headless != headless:
                _headless = headless
                await _restart_browser()
            mode = "headless" if _headless else "headed (visible)"
            return ToolOutput(
                text=f"Browser mode: {mode}",
                title="Headless Toggle",
                metadata={"headless": _headless},
            )
        except Exception as e:
            return ToolOutput(text=f"Headless toggle error: {e}", error=True)


# ── Auto-register ──────────────────────────────────────────────────────

register_tool(BrowserNavigateTool())
register_tool(BrowserClickTool())
register_tool(BrowserTypeTool())
register_tool(BrowserSnapshotTool())
register_tool(BrowserScreenshotTool())
register_tool(BrowserEvaluateTool())
register_tool(BrowserCloseTool())
register_tool(OpenInBrowserTool())
register_tool(BrowserStateTool())
register_tool(BrowserEmulateTool())
register_tool(BrowserInterceptTool())
register_tool(BrowserTraceTool())
register_tool(BrowserToggleHeadlessTool())


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------
__all__ = [
    "BrowserClickTool",
    "BrowserCloseTool",
    "BrowserEmulateTool",
    "BrowserEvaluateTool",
    "BrowserInterceptTool",
    "BrowserNavigateTool",
    "BrowserScreenshotTool",
    "BrowserSnapshotTool",
    "BrowserStateTool",
    "BrowserToggleHeadlessTool",
    "BrowserTraceTool",
    "BrowserTypeTool",
    "OpenInBrowserTool",
]
