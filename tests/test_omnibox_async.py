"""P2: async omnibox completions.

refresh_completions ran two sqlite queries on the GUI thread on every
page load. Rows now load on a worker thread (fresh connection) and the
model swaps in on the signal; merging is a pure tested function.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

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


class _LineEditStub:
    def __init__(self, *args, **kwargs):
        pass


sys.modules["PySide6.QtWidgets"].QLineEdit = _LineEditStub


# _CompletionLoader needs a real QThread base. The updater suites set one
# already (they sort first); only fill the gap standalone.
if not isinstance(sys.modules["PySide6.QtCore"].QThread, type):

    class _ThreadStub:
        def __init__(self, *args, **kwargs):
            pass

    sys.modules["PySide6.QtCore"].QThread = _ThreadStub

import browser_ui.omnibox as omni_mod
from browser_core.storage import Storage
from browser_ui.omnibox import Omnibox, _CompletionLoader, build_completion_entries


def _box(**overrides):
    ns = SimpleNamespace(
        _label_url={},
        _loader=None,
        _model=MagicMock(),
        _storage=MagicMock(),
    )
    for name in ("refresh_completions", "_apply_completions", "_clear_loader"):
        setattr(ns, name, MethodType(getattr(Omnibox, name), ns))
    for key, value in overrides.items():
        setattr(ns, key, value)
    return ns


class FakeLoader:
    instances: list = []

    def __init__(self, db_path, parent=None):
        type(self).instances.append(self)
        self.db_path = db_path
        self.loaded = MagicMock()
        self.finished = MagicMock()
        self.deleteLater = MagicMock()
        self.started = False

    def isRunning(self):  # noqa: N802 (Qt API name)
        return False

    def start(self):
        self.started = True


class TestMerge:
    def test_titles_win_and_dedupe(self):
        history = [
            ("https://a.example/", "A page", 3.0),
            ("https://b.example/", "", 2.0),
            ("https://a.example/", "A page", 1.0),
        ]
        bookmarks = [("https://c.example/", "C mark", "", 0.0)]
        labels, mapping = build_completion_entries(history, bookmarks)
        assert labels == ["A page", "https://b.example/", "C mark"]
        assert mapping["A page"] == "https://a.example/"
        assert mapping["C mark"] == "https://c.example/"

    def test_empty_rows(self):
        assert build_completion_entries([], []) == ([], {})


class TestAsyncRefresh:
    def test_refresh_spawns_loader_with_db_path(self, tmp_path, monkeypatch):
        FakeLoader.instances.clear()
        monkeypatch.setattr(omni_mod, "_CompletionLoader", FakeLoader)
        box = _box()
        box._storage.db_path = tmp_path / "b.db"
        box.refresh_completions()
        assert len(FakeLoader.instances) == 1
        assert FakeLoader.instances[0].db_path == tmp_path / "b.db"
        assert FakeLoader.instances[0].started is True
        assert box._loader is FakeLoader.instances[0]

    def test_second_refresh_while_running_is_noop(self, tmp_path, monkeypatch):
        FakeLoader.instances.clear()
        monkeypatch.setattr(omni_mod, "_CompletionLoader", FakeLoader)
        running = MagicMock()
        running.isRunning.return_value = True
        box = _box(_loader=running)
        box.refresh_completions()
        assert FakeLoader.instances == []

    def test_loaded_signal_applies_model(self):
        box = _box()
        box._apply_completions(
            [("https://a.example/", "A page", 1.0)],
            [("https://b.example/", "B mark", "", 0.0)],
        )
        box._model.setStringList.assert_called_once_with(["A page", "B mark"])
        assert box._label_url == {
            "A page": "https://a.example/",
            "B mark": "https://b.example/",
        }

    def test_loader_run_queries_fresh_db(self, tmp_path):
        store = Storage(tmp_path / "b.db")
        store.add_visit("https://a.example/", "A page")
        store.add_bookmark("https://b.example/", "B mark")
        loader = SimpleNamespace(_db_path=tmp_path / "b.db", loaded=MagicMock())
        _CompletionLoader.run(loader)
        rows, marks = loader.loaded.emit.call_args.args
        assert any(r[0] == "https://a.example/" for r in rows)
        assert any(m[0] == "https://b.example/" for m in marks)

    def test_loader_run_never_raises(self, tmp_path):
        loader = SimpleNamespace(_db_path=tmp_path / "nope" / "b.db", loaded=MagicMock())
        _CompletionLoader.run(loader)  # Storage creates dirs; still must not raise
        assert loader.loaded.emit.called
