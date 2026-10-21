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
from typing import Any

from .base import ToolBase, ToolOutput
from .registry import register_tool

# ── Browser session state ──────────────────────────────────────────────

_browser = None
_page = None
_playwright = None
# The async_playwright() context manager entered in _get_page(). Must be
# __aexit__ed on restart/close or the Playwright driver (node) process leaks.
_pw_manager = None
_headless = True
_device_config: dict | None = None
_intercepts: list[dict] = []
_console_logs: list[dict[str, Any]] = []
_MAX_CONSOLE_LOGS = 200
# Path to a saved storage_state file (.browser_state.json); passed to
# new_context() so cookies AND localStorage are restored on load.
_storage_state_path: str | None = None


def _record_console_entry(entry_type: str, text: str) -> None:
    """Append a bounded console or pageerror entry."""
    _console_logs.append(
        {
            "type": str(entry_type or "log"),
            "text": str(text)[:2000],
            "ts": time.time(),
        }
    )
    if len(_console_logs) > _MAX_CONSOLE_LOGS:
        del _console_logs[:-_MAX_CONSOLE_LOGS]


def _make_fulfill_handler(status: int):
    """Build an async route handler that fulfills with the given status.

    Must be a coroutine function: Playwright awaits the handler, and a sync
    callable returning an un-awaited coroutine leaves the request hanging.
    """

    async def _handler(route):
        await route.fulfill(status=status)

    return _handler


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
    global _browser, _page, _pw_manager
    if _page is None:
        pw = await _get_playwright()
        # __aenter__ lives on the context MANAGER, not the entered Playwright
        # object — keep the manager so restart/close can __aexit__ it and stop
        # the driver process.
        manager = pw()
        p = await manager.__aenter__()
        _pw_manager = manager
        context_opts = {}
        if _device_config:
            context_opts = dict(_device_config)
        if _storage_state_path:
            context_opts["storage_state"] = _storage_state_path
        _browser = await p.chromium.launch(headless=_headless)
        ctx = await _browser.new_context(**context_opts)
        _page = await ctx.new_page()
        if hasattr(_page, "on"):
            with contextlib.suppress(Exception):
                _page.on(
                    "console",
                    lambda msg: _record_console_entry(
                        msg.type()
                        if callable(getattr(msg, "type", None))
                        else str(getattr(msg, "type", "log")),
                        msg.text()
                        if callable(getattr(msg, "text", None))
                        else str(getattr(msg, "text", msg)),
                    ),
                )
                _page.on("pageerror", lambda err: _record_console_entry("pageerror", str(err)))
        # Apply pending intercepts
        for ic in _intercepts:
            await _page.route(ic["pattern"], _make_fulfill_handler(ic.get("status", 200)))
    return _page


async def _restart_browser():
    """Close and reopen the browser (needed for mode/viewport changes)."""
    global _browser, _page, _pw_manager
    if _page:
        with contextlib.suppress(Exception):
            await _page.close()
    if _browser:
        with contextlib.suppress(Exception):
            await _browser.close()
    if _pw_manager is not None:
        # Exit the async_playwright() context manager — this stops the
        # Playwright driver process. Skipping it leaked one driver per
        # restart.
        with contextlib.suppress(Exception):
            await _pw_manager.__aexit__(None, None, None)
        _pw_manager = None
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
        global _browser, _page, _pw_manager
        try:
            if _page:
                await _page.close()
            if _browser:
                await _browser.close()
            if _pw_manager is not None:
                with contextlib.suppress(Exception):
                    await _pw_manager.__aexit__(None, None, None)
                _pw_manager = None
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
        global _page, _storage_state_path
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
                if not isinstance(data, dict):
                    return ToolOutput(
                        text="Saved state is corrupted (not a JSON object).",
                        title="State Load",
                        error=True,
                    )
                # Pass storage_state at context creation so Playwright restores
                # BOTH cookies and localStorage. (The old code only called
                # add_cookies and the "origins" loop was a no-op.)
                _storage_state_path = str(state_path)
                await _restart_browser()
                try:
                    await _get_page()
                except Exception:
                    # A corrupt state file must not poison every future page.
                    _storage_state_path = None
                    raise
                return ToolOutput(
                    text="Browser state loaded.",
                    title="State Loaded",
                )
            elif action == "clear":
                if state_path.exists():
                    state_path.unlink()
                _storage_state_path = None
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
                import playwright.async_api as _pw_api

                pw_devices = getattr(_pw_api, "devices", {})

                device_name = None
                # Try exact match first
                if device in dir(pw_devices):
                    device_name = device
                else:
                    # Fuzzy match, ignoring separators: "iPhone 12" must match
                    # the "iPhone_12" attribute (spaces never appear in attr names).
                    def _norm(s: str) -> str:
                        return "".join(ch for ch in s.lower() if ch.isalnum())

                    target = _norm(device)
                    for name in dir(pw_devices):
                        if not name.startswith("_") and target and target in _norm(name):
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
                lines = "\n".join(f"  {i['pattern']} → {i.get('status', 200)}" for i in _intercepts)
                return ToolOutput(
                    text=f"Active intercepts:\n{lines}",
                    title="Network Intercepts",
                    metadata={"intercepts": _intercepts},
                )
            elif action == "mock":
                if not url_pattern:
                    return ToolOutput(text="url_pattern is required for 'mock' action.", error=True)
                _intercepts.append({"pattern": url_pattern, "status": status})
                # Apply to current page if active
                page = await _get_page()
                await page.route(url_pattern, _make_fulfill_handler(status))
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


# ---------------------------------------------------------------------------
# 16. BrowserConsoleTool -- inspect console messages and uncaught page errors
# ---------------------------------------------------------------------------
class BrowserConsoleTool(ToolBase):
    name = "BrowserConsole"
    description = (
        "Read or clear captured console messages and uncaught page errors from "
        "the active Playwright browser session."
    )
    aliases = ["ConsoleLogs", "PageErrors"]
    parameters = {
        "action": {
            "type": "string",
            "enum": ["list", "clear"],
            "description": "Action: 'list' (default) to read logs or 'clear' to empty the log buffer.",
        },
        "level": {
            "type": "string",
            "description": (
                "Optional filter by level ('error', 'warning', 'log', 'info', 'pageerror'). "
                "Empty returns all levels."
            ),
        },
        "limit": {
            "type": "integer",
            "description": "Maximum number of recent log entries to return (default: 50).",
        },
    }

    async def execute(self, action: str = "list", level: str = "", limit: int = 50) -> ToolOutput:
        try:
            if action == "clear":
                _console_logs.clear()
                return ToolOutput(
                    text="Console log buffer cleared.",
                    title="Console Cleared",
                    metadata={"cleared": True, "count": 0},
                )
            if action != "list":
                return ToolOutput(text=f"Unknown action: {action}", error=True)

            lvl = (level or "").strip().lower()
            entries = list(_console_logs)
            if lvl:
                if lvl == "error":
                    entries = [e for e in entries if e.get("type") in ("error", "pageerror")]
                else:
                    entries = [e for e in entries if e.get("type") == lvl]
            cap = max(1, min(int(limit or 50), _MAX_CONSOLE_LOGS))
            entries = entries[-cap:]
            if not entries:
                return ToolOutput(
                    text="No console messages or page errors recorded.",
                    title="Browser Console (0)",
                    metadata={"logs": [], "count": 0},
                )
            lines = [f"  [{e.get('type', 'log')}] {e.get('text', '')}" for e in entries]
            return ToolOutput(
                text=f"Console logs ({len(entries)}):\n" + "\n".join(lines),
                title=f"Browser Console ({len(entries)})",
                metadata={"logs": entries, "count": len(entries)},
            )
        except Exception as e:
            return ToolOutput(text=f"Browser console error: {e}", error=True)


# ---------------------------------------------------------------------------
# 17. BrowserControlTool -- drive the LIVE LuckyD Browser desktop window
# ---------------------------------------------------------------------------


def _discover_control_api(port: int = 0, token: str = "") -> tuple[str, str]:
    """Resolve the live LuckyD Browser Control API base URL and bearer token.

    Checks explicit arguments first, then environment variables
    (LUCKYD_API_PORT / BROWSER_API_PORT, LUCKYD_API_TOKEN / BROWSER_API_TOKEN),
    and finally the browser's settings.json file on disk.
    """
    resolved_port = int(port or 0)
    if not resolved_port:
        for env_key in ("LUCKYD_API_PORT", "BROWSER_API_PORT"):
            raw = os.environ.get(env_key, "").strip()
            if raw.isdigit():
                resolved_port = int(raw)
                break

    resolved_token = (token or "").strip()
    if not resolved_token:
        for env_key in ("LUCKYD_API_TOKEN", "BROWSER_API_TOKEN"):
            val = os.environ.get(env_key, "").strip()
            if val:
                resolved_token = val
                break

    if not resolved_port or not resolved_token:
        candidates: list[Path] = [
            Path(__file__).resolve().parent.parent / "browser" / "data" / "settings.json",
        ]
        local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
        if local_appdata:
            candidates.append(Path(local_appdata) / "LuckyDBrowser" / "settings.json")
        candidates.append(Path.home() / "AppData" / "Local" / "LuckyDBrowser" / "settings.json")
        candidates.append(Path.home() / ".luckyd-browser" / "settings.json")

        for path in candidates:
            try:
                if not path.is_file():
                    continue
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
                if not resolved_port and int(data.get("browser_api_port", 0) or 0) > 0:
                    resolved_port = int(data["browser_api_port"])
                if not resolved_token and str(data.get("browser_api_token", "") or "").strip():
                    resolved_token = str(data["browser_api_token"]).strip()
                if resolved_port and resolved_token:
                    break
            except Exception:
                continue

    if resolved_port <= 0:
        resolved_port = 9777
    return f"http://127.0.0.1:{resolved_port}", resolved_token


class BrowserControlTool(ToolBase):
    name = "BrowserControl"
    description = (
        "Drive the user's LIVE LuckyD Browser desktop window via the local "
        "Browser Control API (127.0.0.1:9777). Supports reading open tabs, "
        "snapshots with numbered interactive elements, clicking/typing/scrolling, "
        "reversible tab actions (pin, mute, move, duplicate, close_duplicates, "
        "undo_tab), JS evaluation, and page-grounded AI queries. Auto-discovers "
        "the API port and bearer token from settings.json or environment."
    )
    aliases = ["LuckyDBrowser", "LiveBrowser", "ControlBrowser"]
    parameters = {
        "action": {
            "type": "string",
            "enum": [
                "status",
                "tabs",
                "navigate",
                "new_tab",
                "activate_tab",
                "close_tab",
                "tab_action",
                "undo_tab",
                "snapshot",
                "act",
                "undo",
                "eval",
                "ask",
                "extract",
                "screenshot",
            ],
            "description": "Control API operation to execute against the live LuckyD Browser.",
        },
        "url": {
            "type": "string",
            "description": "Target URL (for navigate, new_tab, act=navigate, or screenshot).",
        },
        "new_tab": {
            "type": "boolean",
            "description": "When action='navigate', open the URL in a new tab if True.",
        },
        "index": {
            "type": "integer",
            "description": "Tab index (for activate_tab, close_tab, tab_action) or element index (for act).",
        },
        "to_index": {
            "type": "integer",
            "description": "Destination tab index when action='tab_action' and tab_op='move'.",
        },
        "tab_op": {
            "type": "string",
            "enum": [
                "pin",
                "mute",
                "duplicate",
                "move",
                "close_duplicates",
                "reopen_closed",
                "undo",
            ],
            "description": "Tab bar operation when action='tab_action'.",
        },
        "act_type": {
            "type": "string",
            "enum": [
                "click",
                "type",
                "press",
                "select",
                "scroll",
                "navigate",
                "back",
                "undo",
                "wait",
            ],
            "description": "Page interaction kind when action='act'.",
        },
        "text": {
            "type": "string",
            "description": "Text to type/select, key to press, or scroll direction ('up'/'down') when action='act'.",
        },
        "js": {
            "type": "string",
            "description": "JavaScript expression to run in the active tab when action='eval'.",
        },
        "question": {
            "type": "string",
            "description": "Question or extraction instruction for action='ask' or action='extract'.",
        },
        "provider": {
            "type": "string",
            "description": "Optional AI provider override for action='ask' or action='extract'.",
        },
        "port": {
            "type": "integer",
            "description": "Optional Control API port override (default: auto-discovered or 9777).",
        },
        "token": {
            "type": "string",
            "description": "Optional Control API bearer token override (default: auto-discovered).",
        },
    }

    async def execute(
        self,
        action: str = "status",
        url: str = "",
        new_tab: bool = False,
        index: int = -1,
        to_index: int = 0,
        tab_op: str = "",
        act_type: str = "",
        text: str = "",
        js: str = "",
        question: str = "",
        provider: str = "",
        port: int = 0,
        token: str = "",
    ) -> ToolOutput:
        import httpx

        base_url, bearer = _discover_control_api(port=port, token=token)
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"

        method = "GET"
        route = "/status"
        payload: dict[str, Any] = {}

        act_norm = (action or "status").strip().lower()
        if act_norm == "status":
            method, route = "GET", "/status"
        elif act_norm == "tabs":
            method, route = "GET", "/tabs"
        elif act_norm == "navigate":
            if not url.strip():
                return ToolOutput(text="url is required for action='navigate'", error=True)
            method, route = "POST", "/navigate"
            payload = {"url": url.strip(), "new_tab": bool(new_tab)}
        elif act_norm == "new_tab":
            method, route = "POST", "/tab/new"
            if url.strip():
                payload = {"url": url.strip()}
        elif act_norm == "activate_tab":
            method, route = "POST", "/tab/activate"
            payload = {"index": max(0, int(index))}
        elif act_norm == "close_tab":
            method, route = "POST", "/tab/close"
            payload = {"index": int(index)}
        elif act_norm == "tab_action":
            if not tab_op.strip():
                return ToolOutput(text="tab_op is required for action='tab_action'", error=True)
            method, route = "POST", "/tab/action"
            payload = {
                "action": tab_op.strip(),
                "index": int(index),
                "to_index": int(to_index),
            }
        elif act_norm == "undo_tab":
            method, route = "POST", "/tabs/undo"
        elif act_norm == "snapshot":
            method, route = "POST", "/snapshot"
        elif act_norm == "act":
            if not act_type.strip():
                return ToolOutput(text="act_type is required for action='act'", error=True)
            method, route = "POST", "/act"
            payload = {
                "action": act_type.strip(),
                "index": int(index),
                "text": text,
                "url": url,
            }
        elif act_norm == "undo":
            method, route = "POST", "/undo"
        elif act_norm == "eval":
            if not js.strip():
                return ToolOutput(text="js is required for action='eval'", error=True)
            method, route = "POST", "/eval"
            payload = {"js": js}
        elif act_norm == "ask":
            if not question.strip():
                return ToolOutput(text="question is required for action='ask'", error=True)
            method, route = "POST", "/ask"
            payload = {"question": question.strip()}
            if provider.strip():
                payload["provider"] = provider.strip()
        elif act_norm == "extract":
            if not question.strip():
                return ToolOutput(text="question is required for action='extract'", error=True)
            method, route = "POST", "/extract"
            payload = {"instruction": question.strip()}
            if provider.strip():
                payload["provider"] = provider.strip()
        elif act_norm == "screenshot":
            from urllib.parse import quote

            method = "GET"
            route = f"/screenshot?url={quote(url.strip())}" if url.strip() else "/screenshot"
        else:
            return ToolOutput(text=f"Unknown BrowserControl action: {action!r}", error=True)

        try:
            async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
                if method == "GET":
                    resp = await client.get(f"{base_url}{route}", headers=headers)
                else:
                    resp = await client.post(f"{base_url}{route}", headers=headers, json=payload)
            try:
                data = resp.json()
            except Exception:
                data = {"ok": False, "error": resp.text[:400]}
            if resp.status_code >= 400 or not data.get("ok", True):
                err = data.get("error") or f"HTTP {resp.status_code}"
                return ToolOutput(
                    text=f"LuckyD Browser Control API error ({route}): {err}",
                    title=f"BrowserControl {act_norm} (error)",
                    metadata=data if isinstance(data, dict) else {"error": str(err)},
                    error=True,
                )
            return ToolOutput(
                text=self._format_response(act_norm, data),
                title=f"BrowserControl: {act_norm}",
                metadata=data if isinstance(data, dict) else {"result": data},
            )
        except Exception as e:
            return ToolOutput(
                text=(
                    f"Could not reach LuckyD Browser Control API at {base_url}{route}: {e}. "
                    "Ensure LuckyD Browser is open and Browser Control API is enabled in Tools."
                ),
                error=True,
            )

    @staticmethod
    def _format_response(action: str, data: dict[str, Any]) -> str:
        if action == "tabs":
            tabs = data.get("tabs", [])
            if not tabs:
                return "No open tabs."
            lines = [
                f"  [{'*' if t.get('active') else ' '}] #{t.get('index')}: {t.get('title', '')} — {t.get('url', '')}"
                for t in tabs
            ]
            return f"Open tabs ({len(tabs)}):\n" + "\n".join(lines)
        if action == "snapshot":
            snap = data.get("snapshot", {})
            if not isinstance(snap, dict):
                return json.dumps(data, ensure_ascii=False, indent=2)[:6000]
            els = snap.get("elements", [])
            lines = [
                f"Page: {snap.get('title', '')}",
                f"URL: {snap.get('url', '')}",
                f"\nInteractive elements ({len(els)}):",
            ]
            for el in els[:60]:
                idx = el.get("i", el.get("index", "?"))
                tag = el.get("tag", "")
                label = el.get("label") or el.get("text") or ""
                lines.append(f"  [{idx}] <{tag}> {str(label)[:70]}")
            page_text = str(snap.get("text", "") or "").strip()
            if page_text:
                lines.append(f"\nVisible text:\n{page_text[:3000]}")
            return "\n".join(lines)[:6000]
        return json.dumps(data, ensure_ascii=False, indent=2)[:6000]


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
register_tool(BrowserConsoleTool())
register_tool(BrowserControlTool())


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------
__all__ = [
    "BrowserClickTool",
    "BrowserCloseTool",
    "BrowserConsoleTool",
    "BrowserControlTool",
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
