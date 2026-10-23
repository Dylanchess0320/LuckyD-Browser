"""
Desktop automation tools - screen capture, mouse, keyboard, window control.
Like Claude Computer Use but for your actual desktop.
"""

from __future__ import annotations

import time
from pathlib import Path

from .base import ToolBase, ToolOutput
from .registry import register_tool


class DesktopScreenshotTool(ToolBase):
    name = "DesktopScreenshot"
    description = "Capture the full desktop screen and save to a file."
    aliases = ["ScreenShot", "CaptureScreen", "DesktopCapture"]
    parameters = {
        "path": {
            "type": "string",
            "description": "File path to save the screenshot (default: auto-generated)",
        },
        "region": {
            "type": "string",
            "description": "Region as 'x,y,w,h' or 'full' for full screen (default: full)",
        },
    }

    def execute(self, path: str = "", region: str = "full") -> ToolOutput:
        try:
            import mss

            if not path:
                path = f"desktop_{int(time.time())}.png"

            with mss.mss() as sct:
                if region == "full":
                    sct.shot(output=path)
                else:
                    parts = [int(x.strip()) for x in region.split(",")]
                    if len(parts) == 4:
                        monitor = {
                            "top": parts[1],
                            "left": parts[0],
                            "width": parts[2],
                            "height": parts[3],
                        }
                        sct.shot(output=path, region=monitor)
                    else:
                        return ToolOutput(text="Region must be 'x,y,w,h' or 'full'", error=True)

            size = Path(path).stat().st_size
            return ToolOutput(
                text=f"Screenshot saved: {path} ({size:,} bytes)",
                title="Desktop Screenshot",
                metadata={"path": path, "size": size, "region": region},
            )
        except Exception as e:
            return ToolOutput(text=f"Screenshot error: {e}", error=True)


class DesktopMouseTool(ToolBase):
    name = "DesktopMouse"
    description = "Control the mouse: move, click, double-click, right-click, drag."
    aliases = ["Mouse", "MouseControl"]
    parameters = {
        "action": {
            "type": "string",
            "description": "Action: move, click, dblclick, rightclick, mousedown, mouseup, drag",
        },
        "x": {"type": "integer", "description": "X coordinate (pixels)"},
        "y": {"type": "integer", "description": "Y coordinate (pixels)"},
        "to_x": {"type": "integer", "description": "Target X for drag action"},
        "to_y": {"type": "integer", "description": "Target Y for drag action"},
        "duration": {
            "type": "number",
            "description": "Movement duration in seconds (default: 0.2)",
        },
    }

    def execute(
        self,
        action: str,
        x: int = 0,
        y: int = 0,
        to_x: int = 0,
        to_y: int = 0,
        duration: float = 0.2,
    ) -> ToolOutput:
        try:
            import pyautogui

            pyautogui.FAILSAFE = True

            if action == "move":
                pyautogui.moveTo(x, y, duration=duration)
                return ToolOutput(text=f"Mouse moved to ({x}, {y})", title="Mouse Move")
            elif action == "click":
                pyautogui.click(x, y, duration=duration)
                return ToolOutput(text=f"Clicked at ({x}, {y})", title="Mouse Click")
            elif action == "dblclick":
                pyautogui.doubleClick(x, y, duration=duration)
                return ToolOutput(text=f"Double-clicked at ({x}, {y})", title="Mouse DoubleClick")
            elif action == "rightclick":
                pyautogui.rightClick(x, y, duration=duration)
                return ToolOutput(text=f"Right-clicked at ({x}, {y})", title="Mouse RightClick")
            elif action == "mousedown":
                pyautogui.mouseDown(x, y)
                return ToolOutput(text=f"Mouse down at ({x}, {y})", title="Mouse Down")
            elif action == "mouseup":
                pyautogui.mouseUp(x, y)
                return ToolOutput(text=f"Mouse up at ({x}, {y})", title="Mouse Up")
            elif action == "drag":
                pyautogui.moveTo(x, y, duration=duration)
                pyautogui.drag(to_x - x, to_y - y, duration=duration)
                return ToolOutput(
                    text=f"Dragged from ({x},{y}) to ({to_x},{to_y})", title="Mouse Drag"
                )
            else:
                return ToolOutput(text=f"Unknown action: {action}", error=True)
        except Exception as e:
            return ToolOutput(text=f"Mouse error: {e}", error=True)


class DesktopKeyboardTool(ToolBase):
    name = "DesktopKeyboard"
    description = "Type text or press keys on the keyboard."
    aliases = ["Keyboard", "TypeText", "PressKey"]
    parameters = {
        "action": {"type": "string", "description": "Action: type, press, hotkey"},
        "text": {
            "type": "string",
            "description": "Text to type, key to press, or combo for hotkey (e.g. 'ctrl+c')",
        },
        "interval": {"type": "number", "description": "Seconds between keystrokes (default: 0.05)"},
    }

    def execute(self, action: str, text: str = "", interval: float = 0.05) -> ToolOutput:
        try:
            import pyautogui

            if action == "type":
                pyautogui.typewrite(text, interval=interval)
                preview = text[:50] + ("..." if len(text) > 50 else "")
                return ToolOutput(text=f"Typed: '{preview}'", title="Keyboard Type")
            elif action == "press":
                pyautogui.press(text)
                return ToolOutput(text=f"Pressed: {text}", title="Key Press")
            elif action == "hotkey":
                keys = [k.strip() for k in text.split("+")]
                pyautogui.hotkey(*keys)
                return ToolOutput(text=f"Hotkey: {'+'.join(keys)}", title="Hotkey")
            else:
                return ToolOutput(text=f"Unknown action: {action}", error=True)
        except Exception as e:
            return ToolOutput(text=f"Keyboard error: {e}", error=True)


class DesktopPositionTool(ToolBase):
    name = "DesktopPosition"
    description = "Get current mouse position, screen size, or locate an image on screen."
    aliases = ["MousePos", "ScreenSize"]
    parameters = {
        "query": {
            "type": "string",
            "description": "What to query: position, size, or locate (find an image on screen)",
        },
        "image_path": {
            "type": "string",
            "description": "Path to image to locate on screen (for query=locate)",
        },
    }

    def execute(self, query: str = "position", image_path: str = "") -> ToolOutput:
        try:
            import pyautogui

            if query == "position":
                x, y = pyautogui.position()
                return ToolOutput(
                    text=f"Mouse position: ({x}, {y})",
                    title="Mouse Position",
                    metadata={"x": x, "y": y},
                )
            elif query == "size":
                w, h = pyautogui.size()
                return ToolOutput(
                    text=f"Screen size: {w}x{h}",
                    title="Screen Size",
                    metadata={"width": w, "height": h},
                )
            elif query == "locate" and image_path:
                try:
                    loc = pyautogui.locateOnScreen(image_path, confidence=0.8)
                    if loc:
                        cx = loc.left + loc.width // 2
                        cy = loc.top + loc.height // 2
                        return ToolOutput(
                            text=f"Found: left={loc.left}, top={loc.top}, w={loc.width}, h={loc.height}, center=({cx},{cy})",
                            title="Image Found",
                            metadata={
                                "left": loc.left,
                                "top": loc.top,
                                "width": loc.width,
                                "height": loc.height,
                            },
                        )
                    return ToolOutput(text="Image not found on screen", title="Not Found")
                except Exception as e:
                    return ToolOutput(
                        text=f"Locate failed: {e}. Install: pip install opencv-python", error=True
                    )
            else:
                return ToolOutput(text=f"Unknown query: {query}", error=True)
        except Exception as e:
            return ToolOutput(text=f"Position error: {e}", error=True)


class DesktopWindowTool(ToolBase):
    name = "DesktopWindow"
    description = "List, focus, or manipulate desktop windows."
    aliases = ["Window", "Windows"]
    parameters = {
        "action": {
            "type": "string",
            "description": "Action: list, focus, minimize, maximize, close, geometry",
        },
        "title": {"type": "string", "description": "Window title to match (partial match)"},
    }

    def execute(self, action: str = "list", title: str = "") -> ToolOutput:
        try:
            import pygetwindow as gw

            if action == "list":
                windows = gw.getAllWindows()
                visible = [
                    (w.title, w.left, w.top, w.width, w.height) for w in windows if w.title.strip()
                ]
                lines = [
                    f"  {t[:60]} @ ({left},{tp}) {wd}x{h}" for t, left, tp, wd, h in visible[:30]
                ]
                return ToolOutput(
                    text=f"{len(visible)} windows:\n" + "\n".join(lines),
                    title="Windows",
                )
            elif action == "focus" and title:
                wins = gw.getWindowsWithTitle(title)
                if wins:
                    wins[0].activate()
                    return ToolOutput(text=f"Focused: {wins[0].title}", title="Window Focused")
                return ToolOutput(text=f"No window matching '{title}'", error=True)
            elif action == "minimize" and title:
                wins = gw.getWindowsWithTitle(title)
                if wins:
                    wins[0].minimize()
                    return ToolOutput(text=f"Minimized: {wins[0].title}", title="Window Minimized")
                return ToolOutput(text=f"No window matching '{title}'", error=True)
            elif action == "maximize" and title:
                wins = gw.getWindowsWithTitle(title)
                if wins:
                    wins[0].maximize()
                    return ToolOutput(text=f"Maximized: {wins[0].title}", title="Window Maximized")
                return ToolOutput(text=f"No window matching '{title}'", error=True)
            elif action == "close" and title:
                wins = gw.getWindowsWithTitle(title)
                if wins:
                    wins[0].close()
                    return ToolOutput(text=f"Closed: {wins[0].title}", title="Window Closed")
                return ToolOutput(text=f"No window matching '{title}'", error=True)
            elif action == "geometry" and title:
                wins = gw.getWindowsWithTitle(title)
                if wins:
                    w = wins[0]
                    return ToolOutput(
                        text=f"{w.title}: left={w.left}, top={w.top}, width={w.width}, height={w.height}",
                        title="Window Geometry",
                    )
                return ToolOutput(text=f"No window matching '{title}'", error=True)
            else:
                return ToolOutput(text="Unknown action or missing title", error=True)
        except Exception as e:
            return ToolOutput(text=f"Window error: {e}", error=True)


class DesktopClipboardTool(ToolBase):
    name = "DesktopClipboard"
    description = "Read or write the system clipboard."
    aliases = ["Clipboard", "Copy", "Paste"]
    parameters = {
        "action": {
            "type": "string",
            "description": "Action: read (get clipboard), write (set clipboard)",
        },
        "text": {"type": "string", "description": "Text to write to clipboard (for action=write)"},
    }

    def execute(self, action: str = "read", text: str = "") -> ToolOutput:
        try:
            import pyperclip

            if action == "read":
                content = pyperclip.paste()
                return ToolOutput(
                    text=content[:4000] if content else "(clipboard empty)",
                    title="Clipboard",
                )
            elif action == "write":
                pyperclip.copy(text)
                return ToolOutput(
                    text=f"Written to clipboard ({len(text)} chars)", title="Clipboard Written"
                )
            else:
                return ToolOutput(text=f"Unknown action: {action}", error=True)
        except Exception as e:
            return ToolOutput(text=f"Clipboard error: {e}", error=True)


# Auto-register all desktop tools
register_tool(DesktopScreenshotTool())
register_tool(DesktopMouseTool())
register_tool(DesktopKeyboardTool())
register_tool(DesktopPositionTool())
register_tool(DesktopWindowTool())
register_tool(DesktopClipboardTool())
