"""Night-6 wave-2 tests: tools/desktop_tools.py.

mss / pyautogui / pygetwindow / pyperclip are NOT installed here, so every
test either asserts the genuine missing-dependency error path or injects a
recording fake module via sys.modules. No real desktop is touched.
Covers: screenshot region parsing + auto-path, mouse/keyboard dispatch,
position/size/locate, window list/focus/minimize/maximize/close/geometry,
clipboard read/write.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

import tools.desktop_tools as dt  # noqa: F401  # side effect: registers tools
from tools.registry import registry


def _pkg_installed(name: str) -> bool:
    """True when the optional dependency is importable in this environment."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):  # pragma: no cover - defensive
        return False


# The "missing dependency" tests assert the genuine no-module error path, so
# they only make sense where the package really is absent. CI runners are
# bare; a dev box with desktop extras installed skips them instead of
# failing. The fakes below exercise the happy paths on every machine.
missing_mss = pytest.mark.skipif(_pkg_installed("mss"), reason="mss is installed here")
missing_pyautogui = pytest.mark.skipif(
    _pkg_installed("pyautogui"), reason="pyautogui is installed here"
)
missing_pygetwindow = pytest.mark.skipif(
    _pkg_installed("pygetwindow"), reason="pygetwindow is installed here"
)
missing_pyperclip = pytest.mark.skipif(
    _pkg_installed("pyperclip"), reason="pyperclip is installed here"
)


def _ok(result) -> bool:
    return not bool(getattr(result, "error", False))


def _tool(name):
    t = registry.get(name)
    assert t is not None, f"tool {name} not registered"
    return t


# ── screenshot ─────────────────────────────────────────────────────────


class TestScreenshot:
    @missing_mss
    async def test_missing_mss(self):
        assert "mss" not in sys.modules
        r = await _tool("DesktopScreenshot").execute()
        assert not _ok(r)
        assert r.text.startswith("Screenshot error: No module named 'mss'")

    @pytest.fixture
    def fake_mss(self, monkeypatch):
        shots = []

        class FakeSCT:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def shot(self, output=None, region=None):
                shots.append((output, region))
                Path(output).write_bytes(b"SHOTDATA")

        mod = types.ModuleType("mss")
        mod.mss = lambda: FakeSCT()
        monkeypatch.setitem(sys.modules, "mss", mod)
        return shots

    async def test_full_screen(self, fake_mss, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        p = tmp_path / "s.png"
        r = await _tool("DesktopScreenshot").execute(path=str(p))
        assert _ok(r)
        assert fake_mss == [(str(p), None)]
        assert r.metadata == {"path": str(p), "size": 8, "region": "full"}
        assert f"({8:,} bytes)" in r.text

    async def test_region_parsed(self, fake_mss, tmp_path):
        p = tmp_path / "r.png"
        r = await _tool("DesktopScreenshot").execute(path=str(p), region="10,20,30,40")
        assert _ok(r)
        assert fake_mss[0][1] == {"top": 20, "left": 10, "width": 30, "height": 40}
        assert r.metadata["region"] == "10,20,30,40"

    async def test_bad_region(self, fake_mss, tmp_path):
        r = await _tool("DesktopScreenshot").execute(path=str(tmp_path / "x.png"), region="1,2,3")
        assert not _ok(r)
        assert "Region must be x,y,w,h or full" in r.text

    async def test_non_integer_region(self, fake_mss, tmp_path):
        r = await _tool("DesktopScreenshot").execute(path=str(tmp_path / "x.png"), region="a,b,c,d")
        assert not _ok(r)
        assert r.text.startswith("Screenshot error:")

    async def test_auto_path(self, fake_mss, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        r = await _tool("DesktopScreenshot").execute()
        assert _ok(r)
        assert r.metadata["path"].startswith("desktop_")
        assert r.metadata["path"].endswith(".png")
        assert Path(r.metadata["path"]).exists()


# ── pyautogui fake ─────────────────────────────────────────────────────


@pytest.fixture
def fake_gui(monkeypatch):
    calls = []

    class FakeBox:
        left, top, width, height = 10, 20, 100, 50

    mod = types.ModuleType("pyautogui")
    mod.FAILSAFE = False
    mod.moveTo = lambda x, y, duration=None: calls.append(("moveTo", x, y, duration))
    mod.click = lambda x, y, duration=None: calls.append(("click", x, y, duration))
    mod.doubleClick = lambda x, y, duration=None: calls.append(("doubleClick", x, y, duration))
    mod.rightClick = lambda x, y, duration=None: calls.append(("rightClick", x, y, duration))
    mod.mouseDown = lambda x, y: calls.append(("mouseDown", x, y))
    mod.mouseUp = lambda x, y: calls.append(("mouseUp", x, y))
    mod.drag = lambda dx, dy, duration=None: calls.append(("drag", dx, dy, duration))
    mod.typewrite = lambda text, interval=None: calls.append(("typewrite", text, interval))
    mod.press = lambda key: calls.append(("press", key))
    mod.hotkey = lambda *keys: calls.append(("hotkey", keys))
    mod.position = lambda: (111, 222)
    mod.size = lambda: (1920, 1080)
    mod.locateOnScreen = lambda path, confidence=None: FakeBox()
    monkeypatch.setitem(sys.modules, "pyautogui", mod)
    return types.SimpleNamespace(calls=calls, mod=mod, box=FakeBox)


class TestMouse:
    @missing_pyautogui
    async def test_missing_pyautogui(self):
        assert "pyautogui" not in sys.modules
        r = await _tool("DesktopMouse").execute(action="click")
        assert not _ok(r)
        assert r.text.startswith("Mouse error: No module named 'pyautogui'")

    @pytest.mark.parametrize(
        "action,kw,expect",
        [
            ("move", {"x": 5, "y": 6}, ("moveTo", 5, 6, 0.2)),
            ("click", {"x": 5, "y": 6}, ("click", 5, 6, 0.2)),
            ("dblclick", {"x": 5, "y": 6}, ("doubleClick", 5, 6, 0.2)),
            ("rightclick", {"x": 5, "y": 6}, ("rightClick", 5, 6, 0.2)),
            ("mousedown", {"x": 5, "y": 6}, ("mouseDown", 5, 6)),
            ("mouseup", {"x": 5, "y": 6}, ("mouseUp", 5, 6)),
        ],
    )
    async def test_dispatch(self, fake_gui, action, kw, expect):
        r = await _tool("DesktopMouse").execute(action=action, **kw)
        assert _ok(r)
        assert fake_gui.calls == [expect]
        assert fake_gui.mod.FAILSAFE is True  # tool always enables failsafe

    async def test_drag_offsets(self, fake_gui):
        r = await _tool("DesktopMouse").execute(action="drag", x=10, y=20, to_x=30, to_y=50)
        assert _ok(r)
        assert fake_gui.calls == [("moveTo", 10, 20, 0.2), ("drag", 20, 30, 0.2)]
        assert r.text == "Dragged from (10,20) to (30,50)"

    async def test_unknown_action(self, fake_gui):
        r = await _tool("DesktopMouse").execute(action="spin")
        assert not _ok(r)
        assert r.text == "Unknown action: spin"


class TestKeyboard:
    async def test_type(self, fake_gui):
        r = await _tool("DesktopKeyboard").execute(action="type", text="hello")
        assert _ok(r)
        assert fake_gui.calls == [("typewrite", "hello", 0.05)]
        assert r.text == "Typed: 'hello'"

    async def test_type_preview_truncated(self, fake_gui):
        long_text = "x" * 60
        r = await _tool("DesktopKeyboard").execute(action="type", text=long_text)
        assert _ok(r)
        assert r.text == "Typed: '" + "x" * 50 + "...'"

    async def test_press(self, fake_gui):
        r = await _tool("DesktopKeyboard").execute(action="press", text="enter")
        assert _ok(r)
        assert fake_gui.calls == [("press", "enter")]
        assert r.text == "Pressed: enter"

    async def test_hotkey_split(self, fake_gui):
        r = await _tool("DesktopKeyboard").execute(action="hotkey", text="ctrl + c")
        assert _ok(r)
        assert fake_gui.calls == [("hotkey", ("ctrl", "c"))]
        assert r.text == "Hotkey: ctrl+c"

    async def test_unknown_action(self, fake_gui):
        r = await _tool("DesktopKeyboard").execute(action="yodel")
        assert not _ok(r)
        assert r.text == "Unknown action: yodel"


class TestPosition:
    async def test_position(self, fake_gui):
        r = await _tool("DesktopPosition").execute(query="position")
        assert _ok(r)
        assert r.text == "Mouse position: (111, 222)"
        assert r.metadata == {"x": 111, "y": 222}

    async def test_size(self, fake_gui):
        r = await _tool("DesktopPosition").execute(query="size")
        assert _ok(r)
        assert r.text == "Screen size: 1920x1080"
        assert r.metadata == {"width": 1920, "height": 1080}

    async def test_locate_found(self, fake_gui):
        r = await _tool("DesktopPosition").execute(query="locate", image_path="btn.png")
        assert _ok(r)
        assert r.text == "Found: left=10, top=20, w=100, h=50, center=(60,45)"
        assert r.metadata == {"left": 10, "top": 20, "width": 100, "height": 50}

    async def test_locate_not_found(self, fake_gui, monkeypatch):
        monkeypatch.setattr(fake_gui.mod, "locateOnScreen", lambda path, confidence=None: None)
        r = await _tool("DesktopPosition").execute(query="locate", image_path="b.png")
        assert _ok(r)
        assert r.text == "Image not found on screen"

    async def test_locate_error(self, fake_gui, monkeypatch):
        def boom(path, confidence=None):
            raise RuntimeError("no cv2")

        monkeypatch.setattr(fake_gui.mod, "locateOnScreen", boom)
        r = await _tool("DesktopPosition").execute(query="locate", image_path="b.png")
        assert not _ok(r)
        assert "Locate failed: no cv2" in r.text

    async def test_unknown_query(self, fake_gui):
        r = await _tool("DesktopPosition").execute(query="smell")
        assert not _ok(r)
        assert r.text == "Unknown query: smell"

    async def test_locate_without_image(self, fake_gui):
        r = await _tool("DesktopPosition").execute(query="locate")
        assert not _ok(r)


# ── windows ────────────────────────────────────────────────────────────


@pytest.fixture
def fake_gw(monkeypatch):
    class FakeWin:
        def __init__(self, title, left=1, top=2, width=3, height=4):
            self.title = title
            self.left, self.top, self.width, self.height = left, top, width, height
            self.calls = []

        def activate(self):
            self.calls.append("activate")

        def minimize(self):
            self.calls.append("minimize")

        def maximize(self):
            self.calls.append("maximize")

        def close(self):
            self.calls.append("close")

    wins = [FakeWin("   "), FakeWin("Code Editor", 10, 20, 800, 600), FakeWin("Browser")]
    mod = types.ModuleType("pygetwindow")
    mod.getAllWindows = lambda: list(wins)
    mod.getWindowsWithTitle = lambda t: [w for w in wins if t.lower() in w.title.lower()]
    monkeypatch.setitem(sys.modules, "pygetwindow", mod)
    return wins


class TestWindow:
    @missing_pygetwindow
    async def test_missing_pygetwindow(self):
        assert "pygetwindow" not in sys.modules
        r = await _tool("DesktopWindow").execute()
        assert not _ok(r)
        assert r.text.startswith("Window error: No module named 'pygetwindow'")

    async def test_list_filters_blank_titles(self, fake_gw):
        r = await _tool("DesktopWindow").execute(action="list")
        assert _ok(r)
        assert r.text.startswith("2 windows:")
        assert "Code Editor @ (10,20) 800x600" in r.text
        assert "Browser @ (1,2) 3x4" in r.text

    @pytest.mark.parametrize(
        "action,method,past",
        [
            ("focus", "activate", "Focused"),
            ("minimize", "minimize", "Minimized"),
            ("maximize", "maximize", "Maximized"),
            ("close", "close", "Closed"),
        ],
    )
    async def test_window_ops(self, fake_gw, action, method, past):
        r = await _tool("DesktopWindow").execute(action=action, title="code")
        assert _ok(r)
        assert fake_gw[1].calls == [method]
        assert r.text == f"{past}: Code Editor"

    async def test_no_match(self, fake_gw):
        r = await _tool("DesktopWindow").execute(action="focus", title="zzz")
        assert not _ok(r)
        assert r.text == "No window matching 'zzz'"

    async def test_geometry(self, fake_gw):
        r = await _tool("DesktopWindow").execute(action="geometry", title="code")
        assert _ok(r)
        assert r.text == "Code Editor: left=10, top=20, width=800, height=600"

    async def test_unknown_action_or_missing_title(self, fake_gw):
        r = await _tool("DesktopWindow").execute(action="focus")
        assert not _ok(r)
        assert r.text == "Unknown action or missing title"
        r = await _tool("DesktopWindow").execute(action="explode", title="x")
        assert not _ok(r)


# ── clipboard ──────────────────────────────────────────────────────────


@pytest.fixture
def fake_clip(monkeypatch):
    state = {"text": "clip content"}

    mod = types.ModuleType("pyperclip")
    mod.paste = lambda: state["text"]
    mod.copy = lambda t: state.update(text=t)
    monkeypatch.setitem(sys.modules, "pyperclip", mod)
    return state


class TestClipboard:
    @missing_pyperclip
    async def test_missing_pyperclip(self):
        assert "pyperclip" not in sys.modules
        r = await _tool("DesktopClipboard").execute()
        assert not _ok(r)
        assert r.text.startswith("Clipboard error: No module named 'pyperclip'")

    async def test_read(self, fake_clip):
        r = await _tool("DesktopClipboard").execute(action="read")
        assert _ok(r)
        assert r.text == "clip content"

    async def test_read_empty(self, fake_clip):
        fake_clip["text"] = ""
        r = await _tool("DesktopClipboard").execute(action="read")
        assert _ok(r)
        assert r.text == "(clipboard empty)"

    async def test_read_truncated(self, fake_clip):
        fake_clip["text"] = "y" * 5000
        r = await _tool("DesktopClipboard").execute(action="read")
        assert _ok(r)
        assert r.text == "y" * 4000

    async def test_write(self, fake_clip):
        r = await _tool("DesktopClipboard").execute(action="write", text="new text")
        assert _ok(r)
        assert r.text == "Written to clipboard (8 chars)"
        assert fake_clip["text"] == "new text"

    async def test_unknown_action(self, fake_clip):
        r = await _tool("DesktopClipboard").execute(action="delete")
        assert not _ok(r)
        assert r.text == "Unknown action: delete"
