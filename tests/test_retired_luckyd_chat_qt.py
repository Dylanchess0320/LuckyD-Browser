"""Real-Qt offscreen proof for the LuckyD Chat retirement (pass-3
standard: mocked Qt cannot see menus or tabs, so the visibility claims
are proven against the real widgets).

Covers, in BOTH switch states:
- MainWindow: the Tools menu carries "LuckyD Chat" only when
  show_retired_tools is on.
- SettingsDialog: the "Connections" tab exists only when the switch is
  on — and with it hidden, accepting the dialog leaves stored Hub/Claw
  tokens and addresses untouched (preserved, never deleted).

Needs real PySide6 + QtWebEngine: in the CI replica venv (no PySide6)
the whole module skips; run it in the Qt repro venv:

    QT_QPA_PLATFORM=offscreen <qt-venv>/bin/python -m pytest \
        tests/test_retired_luckyd_chat_qt.py -v
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")

import pytest

PySide6 = pytest.importorskip("PySide6")
# Several suite modules install a MagicMock as sys.modules["PySide6"]
# at import time; if that mock won the race, it is not a real Qt and
# these widget proofs cannot run — skip instead of failing. (A real
# PySide6 reports a string __version__; the mock's is a MagicMock.)
if not isinstance(getattr(PySide6, "__version__", None), str):
    pytest.skip("mocked PySide6 in this suite run", allow_module_level=True)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.insert(0, str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from PySide6.QtWidgets import QApplication, QMenu, QTabWidget

TOKENS = {
    "hub_url": "http://100.96.167.54:18790",
    "hub_token": "HUB-TOKEN-KEEP-ME",
    "claw_url": "http://100.96.167.54:18791",
    "claw_token": "CLAW-TOKEN-KEEP-ME",
}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(["retire-chat-qt"])


def _store(tmp_path: Path, **overrides):
    from browser_core.settings import SettingsStore

    path = tmp_path / "settings.json"
    path.write_text(json.dumps({**TOKENS, **overrides}), encoding="utf-8")
    return SettingsStore(path), path


def _tab_titles(dialog) -> list[str]:
    tabs = dialog.findChild(QTabWidget)
    assert tabs is not None
    return [tabs.tabText(i) for i in range(tabs.count())]


def test_settings_connections_tab_both_states(qapp, tmp_path) -> None:
    from browser_ui.dialogs import SettingsDialog

    # Switch OFF (the default): no Connections tab, and accepting the
    # dialog must leave the stored tokens/addresses exactly as seeded.
    store, path = _store(tmp_path, show_retired_tools=False)
    dlg = SettingsDialog(store, SimpleNamespace())
    titles = _tab_titles(dlg)
    assert "Connections" not in titles
    assert titles == ["General", "Privacy", "Appearance", "Updates"]
    dlg.accept()
    saved = json.loads(path.read_text(encoding="utf-8"))
    for key, value in TOKENS.items():
        assert saved[key] == value, key

    # Switch ON: the tab is back, fields show the stored values.
    store2, _ = _store(tmp_path, show_retired_tools=True)
    dlg2 = SettingsDialog(store2, SimpleNamespace())
    assert "Connections" in _tab_titles(dlg2)
    assert dlg2.hub_token.text() == TOKENS["hub_token"]
    assert dlg2.claw_token.text() == TOKENS["claw_token"]
    assert dlg2.hub_url.text() == TOKENS["hub_url"]
    assert dlg2.claw_url.text() == TOKENS["claw_url"]
    dlg2.reject()


def _menu_action_texts(win) -> set[str]:
    texts: set[str] = set()
    for menu in win.findChildren(QMenu):
        for action in menu.actions():
            if action.text():
                texts.add(action.text().replace("&", ""))
    return texts


def _make_window(qapp, tmp_path: Path, show_retired: bool):
    from browser_core.adblock import AdBlockInterceptor
    from browser_core.permissions import PermissionStore
    from browser_core.profile import default_profile
    from browser_core.session import SessionStore
    from browser_core.storage import Storage
    from browser_core.workspaces import WorkspaceStore
    from browser_ui.main_window import MainWindow

    store, _ = _store(tmp_path, show_retired_tools=show_retired)

    class FakeApp:
        """Real components where they matter; any other app method the
        window wires up becomes a harmless no-op (the pass-3 recipe)."""

        def __init__(self, **kw):
            self.__dict__.update(kw)

        def __getattr__(self, _name):
            return lambda *a, **k: None

    fake_app = FakeApp(
        qapp=qapp,
        settings=store,
        storage=Storage(),
        workspaces=WorkspaceStore(),
        permissions=PermissionStore(),
        session_store=SessionStore(),
        profile=default_profile(),
        adblock=AdBlockInterceptor(enabled=False),
        harness=None,
        control_server=None,
        terminal_server=None,
        install_local_auth_cookies=lambda profile: None,
    )
    win = MainWindow(fake_app)
    fake_app.windows = [win]
    return win


def test_tools_menu_chat_entry_both_states(qapp, tmp_path) -> None:
    win = _make_window(qapp, tmp_path, show_retired=False)
    try:
        texts = _menu_action_texts(win)
        assert "Network Monitor" in texts  # menu construction sanity
        assert "LuckyD Chat" not in texts
        assert "Coding Agent" not in texts  # pass-1 entries still gated too
    finally:
        win.close()
        win.deleteLater()
        qapp.processEvents()

    win = _make_window(qapp, tmp_path, show_retired=True)
    try:
        texts = _menu_action_texts(win)
        assert "LuckyD Chat" in texts
        assert "Coding Agent" in texts
    finally:
        win.close()
        win.deleteLater()
        qapp.processEvents()
