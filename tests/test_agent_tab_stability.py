"""Agent tab stability (2026-10-25) — tab-switch glitch in AI Assistant Agent mode.

Dylan's report: with the AI Assistant in Agent mode, switching browser tabs
while the agent was doing work glitched the screen out.

Root cause: the built-in tab agent resolved its target tab LIVE on every
step (JsBridge got `tabs.current_view()` per request; the CDP driver's
get_url did the same at attach time), so a mid-run tab switch retargeted
the run at the newly visible tab — the agent scrolled the page and flashed
orange highlight badges in whatever tab the user switched to. Meanwhile
the memory saver could freeze/discard the agent's backgrounded work tab,
and every tab switch re-toggled page visibility on ALL pages.

Fixes asserted here:
  * resolve_agent_view — while a run is active the target is PINNED to the
    tab the run started in (never the live current tab); a closed pinned
    tab yields no target (quiet end) instead of hijacking another tab.
  * The sidebar pins at run start (`_agent_view`), routes the JS bridge
    through `_bridge_target_view`, pins the CDP get_url, keeps Undo on the
    pinned tab, and marks the tab `_ld_agent_busy` so the memory saver
    never freezes/discards it mid-run (released on every run-end path).
  * lifecycle.should_protect gains `agent_busy`; tab_widget passes it and
    skips redundant page-visibility toggles per switch.
  * JsDriver._js drops its pending waiter on timeout (no leak).

Qt chrome is asserted at source level (suite convention — PySide6 may not
be installed in CI); the pure policy and the JsBridge/JsDriver code paths
are tested functionally against the real functions.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))


# ── Qt stubs (same defensive pattern as test_pass3_fixes.py) ───────────
class _QtObjectStub:
    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return MagicMock(name=f"qtstub.{name}")


class _FakeSignal:
    def __init__(self, *types):
        self._slots = []

    def connect(self, slot, *args):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in list(self._slots):
            slot(*args)


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
_QTCORE = sys.modules["PySide6.QtCore"]
if not isinstance(getattr(_QTCORE, "QObject", None), type):
    _QTCORE.QObject = _QtObjectStub
    _QTCORE.Signal = lambda *types: _FakeSignal(*types)


def _fresh_agent_module():
    """browser_core.agent with JsBridge guaranteed to be a real class.

    If the module was first imported under plain-MagicMock Qt stubs, its
    Qt-subclassed names degrade silently — reload rebinds them under the
    real stubs installed above."""
    if not isinstance(getattr(_QTCORE, "QObject", None), type):
        _QTCORE.QObject = _QtObjectStub
        _QTCORE.Signal = lambda *types: _FakeSignal(*types)
    import browser_core.agent as agent_mod

    return importlib.reload(agent_mod)


def _src(rel: str) -> str:
    return (_BROWSER_DIR / rel).read_text(encoding="utf-8")


# ── resolve_agent_view: the pin policy (pure) ─────────────────────────


def test_run_pinned_to_start_tab_never_follows_the_live_current_tab() -> None:
    agent_mod = _fresh_agent_module()
    pinned, visible_after_switch = object(), object()
    assert (
        agent_mod.resolve_agent_view(running=True, pinned=pinned, current=visible_after_switch)
        is pinned
    )


def test_run_with_closed_pinned_tab_gets_no_target_at_all() -> None:
    agent_mod = _fresh_agent_module()
    visible = object()
    # pinned=None means the pinned tab closed mid-run: no target (quiet
    # end) — the run must never fall through to another tab.
    assert agent_mod.resolve_agent_view(running=True, pinned=None, current=visible) is None


def test_idle_bridge_keeps_driving_the_visible_tab() -> None:
    agent_mod = _fresh_agent_module()
    pinned, visible = object(), object()
    assert agent_mod.resolve_agent_view(running=False, pinned=pinned, current=visible) is visible


# ── JsBridge executes on the view its getter pins (behavioral) ────────


def test_jsbridge_executes_on_the_view_its_getter_returns() -> None:
    agent_mod = _fresh_agent_module()
    assert isinstance(agent_mod.JsBridge, type)
    on_request = agent_mod.JsBridge.__dict__["_on_request"]  # real function

    executed: list[str] = []
    responses: list[tuple] = []

    class _Page:
        def runJavaScript(self, script, callback):  # noqa: N802 (Qt API)
            executed.append(script)
            callback("RESULT")

    pinned_view = SimpleNamespace(page=lambda: _Page())
    # The getter is the sidebar's pin: it returns the run's tab no matter
    # what the user is looking at now.
    stub_self = SimpleNamespace(
        _get_view=lambda: pinned_view,
        response=SimpleNamespace(emit=lambda token, result: responses.append((token, result))),
    )
    token = object()
    on_request(stub_self, token, "1+1")
    assert executed == ["1+1"]
    assert responses == [(token, "RESULT")]


def test_jsbridge_none_target_answers_without_executing() -> None:
    agent_mod = _fresh_agent_module()
    on_request = agent_mod.JsBridge.__dict__["_on_request"]
    responses: list[tuple] = []
    stub_self = SimpleNamespace(
        _get_view=lambda: None,
        response=SimpleNamespace(emit=lambda token, result: responses.append((token, result))),
    )
    token = object()
    on_request(stub_self, token, "1+1")
    assert responses == [(token, None)]


# ── JsDriver: a timed-out request must not leak its waiter ────────────


class _NeverAnswerBridge:
    """request/response bridge that records requests but never answers."""

    def __init__(self):
        self.requests: list[tuple] = []
        self.request = _FakeSignal()
        self.response = _FakeSignal()
        self.request.connect(lambda token, script: self.requests.append((token, script)))


async def test_jsdriver_timeout_drops_the_pending_waiter() -> None:
    agent_mod = _fresh_agent_module()
    bridge = _NeverAnswerBridge()
    driver = agent_mod.JsDriver(bridge)
    with pytest.raises(asyncio.TimeoutError):
        await driver._js("1+1", timeout=0.05)
    assert driver._pending == {}
    # A late response for the dead request is harmlessly ignored.
    token, _script = bridge.requests[0]
    driver._on_response(token, "late")
    assert driver._pending == {}


# ── Memory saver policy: agent-worked tabs are hands-off ──────────────


def test_should_protect_keeps_agent_worked_tab_fully_alive() -> None:
    from browser_core.lifecycle import should_protect

    kwargs = dict(
        url="https://example.com/article",
        is_current=False,
        pinned=False,
        audible=False,
    )
    assert should_protect(**kwargs) is False  # baseline: sleepable
    assert should_protect(**kwargs, agent_busy=True) is True  # agent working
    # Defaults unchanged for every existing caller.
    assert should_protect(**{**kwargs, "is_current": True}) is True
    assert (
        should_protect(url="https://example.com", is_current=False, pinned=True, audible=False)
        is True
    )


# ── Sidebar wiring (source-level — suite convention for Qt chrome) ────


def test_sidebar_pins_agent_run_to_its_start_tab() -> None:
    sb = _src("browser_ui/ai_sidebar.py")
    # The JS bridge resolves its tab through the pinned resolver — never a
    # live current_view() lambda (the old retargeting bug).
    assert "JsBridge(self._bridge_target_view, self)" in sb
    assert "JsBridge(lambda" not in sb
    assert "self._agent_view = None" in sb
    bridge_fn = sb[sb.index("def _bridge_target_view") : sb.index("def _release_agent_tab")]
    assert "self._agent_view" in bridge_fn
    assert "resolve_agent_view(" in bridge_fn
    assert "self._agent_session.stop()" in bridge_fn  # closed tab -> quiet end


def test_sidebar_pins_run_start_and_cdp_url() -> None:
    sb = _src("browser_ui/ai_sidebar.py")
    start = sb[sb.index("def _start_agent") : sb.index("def _start_builtin_agent")]
    assert "self._agent_view = self._mw.tabs.current_view()" in start
    assert "_ld_agent_busy = True" in start  # memory saver: hands off
    builtin = sb[sb.index("def _start_builtin_agent") : sb.index("def _on_agent_step")]
    assert "get_url=_pinned_url" in builtin  # CDP attach by PINNED tab URL
    assert "lambda u=url" not in builtin  # the old live-current get_url is gone
    assert "self._live_view(self._mw.tabs, self._agent_view)" in builtin


def test_sidebar_releases_the_tab_on_every_run_end_path() -> None:
    sb = _src("browser_ui/ai_sidebar.py")
    assert "view._ld_agent_busy = False" in sb  # _release_agent_tab body
    done = sb[sb.index("def _agent_done") : sb.index("def _undo_agent_step")]
    assert "_release_agent_tab()" in done
    # Harness end paths release AFTER the fallback early-return (a fallback
    # keeps the hold: the built-in run continues on the same tab).
    hd = sb[sb.index("def _harness_done") : sb.index("def _harness_failed")]
    assert "_release_agent_tab()" in hd
    assert hd.index("_fallback_to_builtin") < hd.index("_release_agent_tab()")
    hf = sb[sb.index("def _harness_failed") : sb.index("class _HarnessWorker")]
    assert "_release_agent_tab()" in hf
    assert hf.index("_fallback_to_builtin") < hf.index("_release_agent_tab()")


def test_sidebar_undo_targets_the_pinned_tab() -> None:
    sb = _src("browser_ui/ai_sidebar.py")
    undo = sb[sb.index("def _undo_agent_step") : sb.index("def _ensure_harness_bridge")]
    assert "self._live_view(self._mw.tabs, self._agent_view)" in undo


# ── Tab widget: memory-saver call-through + no visibility churn ───────


def test_memory_saver_and_visibility_churn_wiring() -> None:
    tw = _src("browser_ui/tab_widget.py")
    saver = tw[tw.index("def _tick_memory_saver") : tw.index("def _apply_sleep")]
    assert 'agent_busy=bool(getattr(view, "_ld_agent_busy", False))' in saver
    assert 'getattr(view, "_ld_agent_busy", False)' in tw  # never sleep the work tab
    # Visibility toggles only on change (the per-switch churn is gone).
    assert "def _set_page_visible" in tw
    mark = tw[tw.index("def _mark_idle_clocks") : tw.index("def _wake_view")]
    assert "self._set_page_visible(view, view is current)" in mark
    assert "page().setVisible(view is current)" not in mark
    wake = tw[tw.index("def _wake_view") : tw.index("def _tick_memory_saver")]
    assert "self._set_page_visible(view, True)" in wake
