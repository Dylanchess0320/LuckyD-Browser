"""Settings tabs contract (Dylan 2026-10-23: the Settings dialog "gets
messed up" — Privacy, Appearance and Updates tabs don't show).

Root cause (fixed 2026-10-23): the Connections page widget was built
unconditionally as a child of the QTabWidget, but ``tabs.addTab(...)`` —
the call that hands a page to the tab stack, which is what hides
non-current pages — only ran while the (since removed) retired-tools
switch was on. With the switch off (the default) the page belonged to
no stack, so Qt never hid it: it floated visible at the tab widget's
top-left, painting its Hub/Claw form over the tab bar and the current
page and burying the neighbouring tabs.

The contract this file pins (source level — the suite's Qt is mocked):

- EVERY Settings tab is added unconditionally:
  General, Privacy, Appearance, Connections, Updates.
- Every page widget built inside SettingsDialog is handed to the tab
  stack exactly once — a built-but-never-added page is the orphan
  mechanism above and must never come back.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

_DIALOGS = (_BROWSER_DIR / "browser_ui" / "dialogs.py").read_text(encoding="utf-8")

EXPECTED_TABS = [
    ("general", "General"),
    ("privacy", "Privacy"),
    ("appearance", "Appearance"),
    ("connections", "Connections"),
    ("updates", "Updates"),
]


def _settings_dialog_src() -> str:
    start = _DIALOGS.index("class SettingsDialog")
    end = _DIALOGS.index("class ScriptsDialog")
    return _DIALOGS[start:end]


def test_every_settings_tab_added_unconditionally_in_order() -> None:
    src = _settings_dialog_src()
    # Anchored at the constructor's own indent (8 spaces): an addTab
    # nested any deeper sits inside a conditional — i.e. gated.
    found = re.findall(r'^        tabs\.addTab\(_scrollable\((\w+)\), "(\w+)"\)', src, flags=re.M)
    assert found == EXPECTED_TABS
    # …and there are no other (deeper-indented, conditional) addTab
    # calls hiding in the dialog at all.
    assert len(re.findall(r"tabs\.addTab\(", src)) == len(EXPECTED_TABS)


def test_every_built_page_is_handed_to_the_tab_stack() -> None:
    """A page widget that is constructed but never addTab'ed is never
    hidden by Qt — it floats over the dialog (the 2026-10-23 bug)."""
    src = _settings_dialog_src()
    built = re.findall(r"^        (\w+) = QWidget\(tabs\)$", src, flags=re.M)
    added = re.findall(r"^        tabs\.addTab\(_scrollable\((\w+)\),", src, flags=re.M)
    assert sorted(built) == sorted(added)
    assert len(built) == len(EXPECTED_TABS)
