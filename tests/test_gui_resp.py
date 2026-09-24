"""B4: GuiInvoker never deadlocks GUI-thread callers.

``GuiInvoker.run`` marshals work to the GUI thread and blocks for the
result — calling it FROM the GUI thread used to freeze the UI until
the 20s timeout (the docstring admitted the deadlock without guarding
it). GUI-thread callers now run inline: nested QEventLoops inside the
closures still pump, so behavior is correct instead of frozen.
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


def _cs():
    """control_server import that cannot precede the suite's Qt stubs.

    tests/test_cov_control_server.py owns this suite's Qt-stub world and
    asserts GuiInvoker subclasses its stub QObject. Importing
    control_server first (real Qt is installed here) would bind the wrong
    QObject and break those tests in reverse collection order — so when
    the module is not imported yet, execute that file's top level (stub
    install only; no tests run on import) to force the stub flavor.
    """
    import importlib.util

    if "browser_core.control_server" not in sys.modules:
        path = Path(__file__).parent / "test_cov_control_server.py"
        spec = importlib.util.spec_from_file_location("_cov_qt_stubs", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    import browser_core.control_server as control_server

    return control_server


class TestGuiThreadDetection:
    def test_headless_is_not_gui_thread(self, monkeypatch):
        # Simulate a failed Qt import WITHOUT deleting stub modules:
        # removing entries forces a real re-import, which segfaults when
        # mixed with the suite's Qt stubs. A None entry raises ImportError.
        monkeypatch.setitem(sys.modules, "PySide6.QtCore", None)
        assert _cs()._on_gui_thread() is False

    def test_same_thread_detected(self, monkeypatch):
        sentinel = object()
        qtcore = MagicMock()
        qtcore.QThread.currentThread.return_value = sentinel
        qtwidgets = MagicMock()
        qtwidgets.QApplication.instance.return_value.thread.return_value = sentinel
        monkeypatch.setitem(sys.modules, "PySide6.QtCore", qtcore)
        monkeypatch.setitem(sys.modules, "PySide6.QtWidgets", qtwidgets)
        assert _cs()._on_gui_thread() is True

    def test_worker_thread_detected(self, monkeypatch):
        qtcore = MagicMock()
        qtcore.QThread.currentThread.return_value = object()
        qtwidgets = MagicMock()
        qtwidgets.QApplication.instance.return_value.thread.return_value = object()
        monkeypatch.setitem(sys.modules, "PySide6.QtCore", qtcore)
        monkeypatch.setitem(sys.modules, "PySide6.QtWidgets", qtwidgets)
        assert _cs()._on_gui_thread() is False


# NOTE: the inline-run branch itself is tested in
# tests/test_cov_control_server.py::test_gui_invoker_runs_inline_on_gui_thread,
# which owns this suite's Qt-stub world (see _cs() above).
