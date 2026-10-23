"""
Browser automation tools — Playwright-powered page interaction.
Navigate, click, type, screenshot, and control a headless browser.
"""

from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path
from typing import Any

from .base import ToolBase, ToolOutput
from .registry import register_tool

# ── Singleton browser session ──────────────────────────────────────────


class BrowserSession:
    """Persistent Playwright browser session shared across tools."""

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._headless = True
        self._device = None
        self._intercepts: list[dict] = []

    def _ensure_playwright(self):
        if self._playwright is None:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
        return self._playwright

    def _ensure_browser(self):
        if self._browser is None:
            pw = self._ensure_playwright()
            self._browser = pw.chromium.launch(headless=self._headless)
        return self._browser

    def _ensure_page(self):
        browser = self._ensure_browser()
        if self._context is None:
            if self._device:
                self._context = browser.new_context(**self._device)
            else:
                self._context = browser.new_context()
        if self._page is None or self._page.is_closed():
            self._page = self._context.new_page()
            # Apply any pending intercepts
            for ic in self._intercepts:
                self._page.route(ic["pattern"], ic["handler"])
        return self._page

    def navigate(self, url: str) -> dict:
        page = self._ensure_page()
        page.goto(url, wait_until="domcontentloaded")
        title = page.title()
        return {"title": title, "url": page.url}

    def click(self, selector: str) -> dict:
        page = self._ensure_page()
        page.click(selector)
        return {"clicked": selector}

    def type_text(self, selector: str, text: str, submit: bool = False) -> dict:
        page = self._ensure_page()
        page.fill(selector, text)
        if submit:
            page.press(selector, "Enter")
        return {"typed": text, "selector": selector, "submitted": submit}

    def snapshot(self) -> str:
        page = self._ensure_page()
        # Build a text representation of the page
        result = page.evaluate(
            """
            () => {
                const elements = [];
                const all = document.querySelectorAll(
                    'a, button, input, select, textarea, h1, h2, h3, h4, h5, h6, [role="button"], [role="link"], [contenteditable="true"]'
                );
                for (const el of all) {
                    const tag = el.tagName.toLowerCase();
                    const text = (el.textContent || '').trim().slice(0, 80);
                    const id = el.id ? '#' + el.id : '';
                    const cls = el.className && typeof el.className === 'string' ? '.' + el.className.split(' ')[0] : '';
                    const href = el.href || '';
                    const placeholder = el.placeholder || '';
                    elements.push({ tag, text, id, cls, href, placeholder });
                }
                return { title: document.title, url: location.href, elements };
            }
        """
        )
        lines = [f"Title: {result['title']}", f"URL: {result['url']}", ""]
        for el in result["elements"][:100]:
            label = el["text"] or el["placeholder"]
            info = f"  <{el['tag']}{el['id']}{el['cls']}> {label}"
            if el.get("href"):
                info += f" -> {el['href'][:80]}"
            lines.append(info)
        return "\n".join(lines)

    def screenshot(self, path: str = "", full_page: bool = False) -> str:
        page = self._ensure_page()
        if not path:
            path = f"browser_{int(time.time())}.png"
        page.screenshot(path=path, full_page=full_page)
        size = Path(path).stat().st_size
        return f"Screenshot saved: {path} ({size:,} bytes)"

    def evaluate(self, expression: str) -> Any:
        page = self._ensure_page()
        return page.evaluate(expression)

    def close(self):
        if self._page:
            with contextlib.suppress(Exception):
                self._page.close()
            self._page = None
        if self._context:
            with contextlib.suppress(Exception):
                self._context.close()
            self._context = None

    def close_all(self):
        self.close()
        if self._browser:
            with contextlib.suppress(Exception):
                self._browser.close()
            self._browser = None
        if self._playwright:
            with contextlib.suppress(Exception):
                self._playwright.stop()
            self._playwright = None

    def toggle_headless(self, headless: bool):
        self._headless = headless
        # Force restart
        self.close_all()

    def emulate(self, device_name: str = "", width: int = 0, height: int = 0):
        if device_name and device_name != "desktop":
            from playwright.sync_api import devices as pw_devices

            if hasattr(pw_devices, device_name):
                self._device = getattr(pw_devices, device_name)
            else:
                # Try fuzzy match
                for name in dir(pw_devices):
                    if device_name.lower() in name.lower():
                        self._device = getattr(pw_devices, name)
                        break
        elif width and height:
            self._device = {"viewport": {"width": width, "height": height}}
        else:
            self._device = None  # reset to desktop
        # Force restart
        self.close_all()

    def add_intercept(self, pattern: str, status: int = 200):
        def handler(route):
            route.fulfill(status=status, body="")

        self._intercepts.append({"pattern": pattern, "handler": handler})
        if self._page:
            self._page.route(pattern, handler)

    def clear_intercepts(self):
        self._intercepts.clear()
        if self._page:
            self._page.unroute_all()

    def list_requests(self) -> list[dict]:
        if not self._page:
            return []
        # Requests aren't tracked unless we set up listeners
        return []


# Global singleton
_session = BrowserSession()


# ── Tool classes ───────────────────────────────────────────────────────


class BrowserNavigateTool(ToolBase):
    name = "BrowserNavigate"
    description = "Navigate the browser to a URL. Returns page title and summary of elements."
    aliases = ["Navigate", "GoTo"]
    parameters = {
        "url": {"type": "string", "description": "The URL to navigate to"},
    }

    def execute(self, url: str) -> ToolOutput:
        try:
            result = _session.navigate(url)
            snapshot_text = _session.snapshot()[:5000]
            return ToolOutput(
                text=f"Navigated to: {result['title']}\nURL: {result['url']}\n\n{snapshot_text}",
                title=result["title"],
                metadata=result,
            )
        except Exception as e:
            return ToolOutput(text=f"Browser navigate error: {e}", error=True)


class BrowserClickTool(ToolBase):
    name = "BrowserClick"
    description = "Click an element on the page by CSS selector."
    aliases = ["Click", "PageClick"]
    parameters = {
        "selector": {"type": "string", "description": "CSS selector of the element to click"},
    }

    def execute(self, selector: str) -> ToolOutput:
        try:
            result = _session.click(selector)
            return ToolOutput(
                text=f"Clicked: {selector}",
                title="Browser Click",
                metadata=result,
            )
        except Exception as e:
            return ToolOutput(text=f"Click error: {e}", error=True)


class BrowserTypeTool(ToolBase):
    name = "BrowserType"
    description = "Type text into an input field."
    aliases = ["Type", "Fill", "Input"]
    parameters = {
        "selector": {"type": "string", "description": "CSS selector of the input element"},
        "text": {"type": "string", "description": "Text to type"},
        "submit": {"type": "boolean", "description": "Press Enter after typing (default: false)"},
    }

    def execute(self, selector: str, text: str, submit: bool = False) -> ToolOutput:
        try:
            result = _session.type_text(selector, text, submit)
            return ToolOutput(
                text=f"Typed '{text}' into {selector}" + (" (submitted)" if submit else ""),
                title="Browser Type",
                metadata=result,
            )
        except Exception as e:
            return ToolOutput(text=f"Type error: {e}", error=True)


class BrowserSnapshotTool(ToolBase):
    name = "BrowserSnapshot"
    description = (
        "Get a text snapshot of the current page (interactive elements, headings, links, etc.)."
    )
    aliases = ["Snapshot", "PageSnapshot", "PageText"]
    parameters = {}

    def execute(self) -> ToolOutput:
        try:
            text = _session.snapshot()
            return ToolOutput(
                text=text[:10000],
                title="Page Snapshot",
            )
        except Exception as e:
            return ToolOutput(text=f"Snapshot error: {e}", error=True)


class BrowserScreenshotTool(ToolBase):
    name = "BrowserScreenshot"
    description = "Take a screenshot of the current page and save it to a file."
    aliases = ["Screenshot", "PageScreenshot", "CapturePage"]
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

    def execute(self, path: str = "", full_page: bool = False) -> ToolOutput:
        try:
            result = _session.screenshot(path, full_page)
            return ToolOutput(text=result, title="Browser Screenshot")
        except Exception as e:
            return ToolOutput(text=f"Screenshot error: {e}", error=True)


class BrowserEvaluateTool(ToolBase):
    name = "BrowserEvaluate"
    description = "Run JavaScript on the current page and return the result."
    aliases = ["Evaluate", "Eval", "JS"]
    parameters = {
        "expression": {"type": "string", "description": "JavaScript expression to evaluate"},
    }

    def execute(self, expression: str) -> ToolOutput:
        try:
            result = _session.evaluate(expression)
            text = str(result) if result is not None else "undefined"
            return ToolOutput(
                text=text[:8000],
                title="JavaScript Result",
                metadata={"result": str(result)[:2000] if result is not None else None},
            )
        except Exception as e:
            return ToolOutput(text=f"JS error: {e}", error=True)


class BrowserCloseTool(ToolBase):
    name = "BrowserClose"
    description = "Close the browser session."
    aliases = ["CloseBrowser", "BrowserClose"]
    parameters = {}

    def execute(self) -> ToolOutput:
        try:
            _session.close_all()
            return ToolOutput(text="Browser closed.", title="Browser Closed")
        except Exception as e:
            return ToolOutput(text=f"Close error: {e}", error=True)


class OpenInBrowserTool(ToolBase):
    name = "OpenInBrowser"
    description = "Open a URL in the user's default system browser (Chrome, Edge, etc.)."
    aliases = ["OpenURL", "LaunchBrowser"]
    parameters = {
        "url": {"type": "string", "description": "The URL to open"},
    }

    def execute(self, url: str) -> ToolOutput:
        import webbrowser

        webbrowser.open(url)
        return ToolOutput(text=f"Opened in your browser: {url}", title="URL Opened")


class BrowserStateTool(ToolBase):
    name = "BrowserState"
    description = "Save or clear persistent browser state (cookies, localStorage)."
    aliases = ["Cookies", "LocalStorage"]
    parameters = {
        "action": {
            "type": "string",
            "description": "Action: 'save' persists current state, 'clear' wipes stored state",
        },
    }

    def execute(self, action: str) -> ToolOutput:
        try:
            page = _session._ensure_page()
            if action == "save":
                state_path = Path(os.getcwd()) / ".browser_state.json"
                storage = page.context.storage_state()
                import json

                state_path.write_text(json.dumps(storage, indent=2))
                return ToolOutput(
                    text=f"Browser state saved to {state_path}",
                    title="State Saved",
                    metadata={"path": str(state_path)},
                )
            elif action == "clear":
                state_path = Path(os.getcwd()) / ".browser_state.json"
                if state_path.exists():
                    state_path.unlink()
                page.context.clear_cookies()
                return ToolOutput(text="Browser state cleared.", title="State Cleared")
            else:
                return ToolOutput(
                    text=f"Unknown action: {action}. Use 'save' or 'clear'.", error=True
                )
        except Exception as e:
            return ToolOutput(text=f"State error: {e}", error=True)


class BrowserEmulateTool(ToolBase):
    name = "BrowserEmulate"
    description = "Emulate a mobile device or set a custom viewport."
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

    def execute(self, device: str = "", width: int = 0, height: int = 0) -> ToolOutput:
        try:
            _session.emulate(device, width, height)
            return ToolOutput(
                text=f"Emulating: {device or f'{width}x{height}'}",
                title="Device Emulated",
            )
        except Exception as e:
            return ToolOutput(text=f"Emulate error: {e}", error=True)


class BrowserInterceptTool(ToolBase):
    name = "BrowserIntercept"
    description = "Intercept network requests on the current page."
    aliases = ["Intercept", "NetworkMock", "BlockURL"]
    parameters = {
        "action": {
            "type": "string",
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

    def execute(self, action: str, url_pattern: str = "", status: int = 200) -> ToolOutput:
        try:
            if action == "list":
                reqs = _session.list_requests()
                if not reqs:
                    return ToolOutput(text="No requests tracked yet.", title="Intercepts")
                lines = [f"  {r.get('url', '')}" for r in reqs]
                return ToolOutput(text="\n".join(lines), title=f"{len(reqs)} requests")
            elif action == "mock":
                if not url_pattern:
                    return ToolOutput(text="url_pattern required for mock action", error=True)
                _session.add_intercept(url_pattern, status)
                return ToolOutput(
                    text=f"Mocking {url_pattern} → HTTP {status}",
                    title="Intercept Added",
                )
            elif action == "clear":
                _session.clear_intercepts()
                return ToolOutput(text="All intercepts cleared.", title="Intercepts Cleared")
            else:
                return ToolOutput(text=f"Unknown action: {action}", error=True)
        except Exception as e:
            return ToolOutput(text=f"Intercept error: {e}", error=True)


class BrowserTraceTool(ToolBase):
    name = "BrowserTrace"
    description = "Start or stop browser tracing (records screenshots, network, and DOM snapshots for debugging). Use 'start' to begin, 'stop' to save the trace to a .zip file."
    aliases = ["Trace", "RecordTrace", "DebugTrace"]
    parameters = {
        "action": {"type": "string", "description": "Start or stop tracing."},
    }

    def execute(self, action: str) -> ToolOutput:
        try:
            page = _session._ensure_page()
            if action == "start":
                page.context.tracing.start(screenshots=True, snapshots=True)
                return ToolOutput(text="Tracing started.", title="Trace Started")
            elif action == "stop":
                trace_path = f"trace_{int(time.time())}.zip"
                page.context.tracing.stop(path=trace_path)
                size = Path(trace_path).stat().st_size
                return ToolOutput(
                    text=f"Trace saved: {trace_path} ({size:,} bytes)",
                    title="Trace Saved",
                    metadata={"path": trace_path, "size": size},
                )
            else:
                return ToolOutput(
                    text=f"Unknown action: {action}. Use 'start' or 'stop'.", error=True
                )
        except Exception as e:
            return ToolOutput(text=f"Trace error: {e}", error=True)


class BrowserToggleHeadlessTool(ToolBase):
    name = "BrowserToggleHeadless"
    description = "Toggle the browser between headless (invisible) and headed (visible) mode."
    aliases = ["ToggleHeadless", "Headless", "ShowBrowser", "HideBrowser"]
    parameters = {
        "headless": {
            "type": "boolean",
            "description": "True for headless (default), False for visible browser window.",
        },
    }

    def execute(self, headless: bool) -> ToolOutput:
        try:
            _session.toggle_headless(headless)
            mode = "headless" if headless else "headed (visible)"
            return ToolOutput(text=f"Browser mode: {mode}", title="Headless Toggled")
        except Exception as e:
            return ToolOutput(text=f"Toggle headless error: {e}", error=True)


# ── Register all tools ─────────────────────────────────────────────────


def register_all(reg=None):
    mapping = [
        BrowserNavigateTool,
        BrowserClickTool,
        BrowserTypeTool,
        BrowserSnapshotTool,
        BrowserScreenshotTool,
        BrowserEvaluateTool,
        BrowserCloseTool,
        OpenInBrowserTool,
        BrowserStateTool,
        BrowserEmulateTool,
        BrowserInterceptTool,
        BrowserTraceTool,
        BrowserToggleHeadlessTool,
    ]
    for cls in mapping:
        register_tool(cls, registry=reg)


# Auto-register on import
register_all()
