"""B1: JS dialogs fail closed during agent sessions.

While an agent session drives the page, modal JS dialogs auto-dismiss
so the runJavaScript channel never freezes — and their text is recorded
for the agent snapshot. Decision dialogs must fail CLOSED: an
auto-accepted confirm() would let a hostile page click through
destructive choices on the agent's behalf.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

# web_view.py imports PySide6 at top level, which is not installed
# headless — stub it like tests/test_98_google_captcha.py does.
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


class _PageStub:
    """Real base class standing in for QWebEnginePage; records super calls."""

    calls: list = []

    def javaScriptAlert(self, *args):  # noqa: N802 (Qt API name)
        type(self).calls.append(("alert", args))

    def javaScriptConfirm(self, *args):  # noqa: N802 (Qt API name)
        type(self).calls.append(("confirm", args))
        return "SUPER"

    def javaScriptPrompt(self, *args):  # noqa: N802 (Qt API name)
        type(self).calls.append(("prompt", args))
        return "SUPER"


sys.modules["PySide6.QtWebEngineCore"].QWebEnginePage = _PageStub

from browser_core import agent as agent_mod

from browser.browser_ui.web_view import WebPage


def _bare_page(monkeypatch):
    page = WebPage.__new__(WebPage)
    recorded = []
    monkeypatch.setattr(page, "_record_dialog", lambda kind, msg: recorded.append((kind, msg)))
    _PageStub.calls.clear()
    return page, recorded


class TestAgentSessionDialogs:
    def test_confirm_declined_during_agent_session(self, monkeypatch):
        monkeypatch.setattr(agent_mod, "ACTIVE_SESSIONS", 1)
        page, recorded = _bare_page(monkeypatch)
        assert page.javaScriptConfirm("origin", "Delete everything?") is False
        assert recorded and recorded[0][0].startswith("confirm (auto-declined)")
        assert _PageStub.calls == []  # super dialog never shown

    def test_prompt_cancelled_during_agent_session(self, monkeypatch):
        monkeypatch.setattr(agent_mod, "ACTIVE_SESSIONS", 2)
        page, recorded = _bare_page(monkeypatch)
        assert page.javaScriptPrompt("origin", "Name?", "x") == (False, "")
        assert recorded and recorded[0][0].startswith("prompt (auto-cancelled)")

    def test_alert_recorded_without_decision(self, monkeypatch):
        monkeypatch.setattr(agent_mod, "ACTIVE_SESSIONS", 1)
        page, recorded = _bare_page(monkeypatch)
        assert page.javaScriptAlert("origin", "hi") is None
        assert recorded and recorded[0][0] == "alert"

    def test_dialogs_delegate_without_session(self, monkeypatch):
        monkeypatch.setattr(agent_mod, "ACTIVE_SESSIONS", 0)
        page, recorded = _bare_page(monkeypatch)
        assert page.javaScriptConfirm("origin", "ok?") == "SUPER"
        assert page.javaScriptPrompt("origin", "n?", "d") == "SUPER"
        page.javaScriptAlert("origin", "hi")
        assert recorded == []
        assert [c[0] for c in _PageStub.calls] == ["confirm", "prompt", "alert"]
