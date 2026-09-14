"""Coverage tests for browser_core/workspaces.py — workspace CRUD + storage.

Targets the ranges missed by the existing suite: the frozen _data_dir
branches, sanitize_tabs filtering/capping, load() validation paths,
save() failure, set_active guard, MAX_WORKSPACES eviction, update/rename
misses, delete active-fallback, and the snapshot/url helpers.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from browser_core.session import MAX_TABS_PER_WINDOW
from browser_core.workspaces import (
    MAX_WORKSPACES,
    WorkspaceStore,
    _data_dir,
    is_workspace_url,
    sanitize_tabs,
    snapshot_from_records,
)

# ── _data_dir ────────────────────────────────────────────────────────


def test_data_dir_frozen_with_localappdata(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", "C:/Users/T/AppData/Local")
    assert _data_dir() == Path("C:/Users/T/AppData/Local") / "LuckyDBrowser"


def test_data_dir_frozen_without_localappdata(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert _data_dir() == Path.home() / "AppData" / "Local" / "LuckyDBrowser"


def test_data_dir_dev():
    assert _data_dir() == Path(__file__).resolve().parent.parent / "browser" / "data"


# ── sanitize_tabs ────────────────────────────────────────────────────


def test_sanitize_tabs_skips_non_dicts_and_unrestorable():
    tabs = [
        "not-a-dict",
        None,
        42,
        {"url": "about:blank", "title": "x"},  # not restorable: dropped
        {"url": "https://example.com", "title": "Ex", "pinned": True, "group": "g"},
    ]
    assert sanitize_tabs(tabs) == [
        {"url": "https://example.com", "title": "Ex", "pinned": True, "group": "g"}
    ]
    assert sanitize_tabs(None) == []


def test_sanitize_tabs_caps_at_max_tabs():
    tabs = [{"url": f"https://example.com/{i}"} for i in range(MAX_TABS_PER_WINDOW + 5)]
    assert len(sanitize_tabs(tabs)) == MAX_TABS_PER_WINDOW


# ── load() ───────────────────────────────────────────────────────────


def _write(path: Path, obj) -> Path:
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def test_load_missing_file_starts_empty(tmp_path):
    store = WorkspaceStore(tmp_path / "ws.json")
    assert store.list() == []
    assert store.active_id == ""


def test_load_non_dict_json_starts_empty(tmp_path):
    store = WorkspaceStore(_write(tmp_path / "ws.json", [1, 2, 3]))
    assert store.list() == []
    assert store.active_id == ""


def test_load_ignores_non_list_workspaces(tmp_path):
    store = WorkspaceStore(_write(tmp_path / "ws.json", {"active": "", "workspaces": "nope"}))
    assert store.list() == []


def test_load_skips_items_without_id(tmp_path):
    store = WorkspaceStore(
        _write(
            tmp_path / "ws.json",
            {
                "active": "ws_x",
                "workspaces": [
                    "junk",
                    {"name": "No id"},
                    {
                        "id": "ws_x",
                        "name": "  Keep me  ",
                        "tabs": [{"url": "https://a.example/"}],
                        "current": 2,
                        "updated_at": 123.0,
                    },
                ],
            },
        )
    )
    assert [w["id"] for w in store.list()] == ["ws_x"]
    w = store.get("ws_x")
    assert w["name"] == "  Keep me  "  # load() does not strip; create()/rename() do
    assert w["tabs"] == [{"url": "https://a.example/", "title": "", "pinned": False}]
    assert w["current"] == 2
    assert w["updated_at"] == 123.0
    assert store.active_id == "ws_x"


def test_load_resets_unknown_active_id(tmp_path):
    store = WorkspaceStore(
        _write(tmp_path / "ws.json", {"active": "ws_missing", "workspaces": [{"id": "ws_a"}]})
    )
    assert store.active_id == ""


# ── save() ───────────────────────────────────────────────────────────


def test_save_roundtrip_persists_active(tmp_path):
    store = WorkspaceStore(tmp_path / "ws.json")
    a = store.create("A", [{"url": "https://a.example/"}])
    assert WorkspaceStore(tmp_path / "ws.json").active_id == a["id"]
    assert WorkspaceStore(tmp_path / "ws.json").get(a["id"])["name"] == "A"


def test_save_failure_returns_false(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")  # parent is a file: mkdir fails
    assert WorkspaceStore(blocker / "ws.json").save() is False


# ── set_active ───────────────────────────────────────────────────────


def test_set_active_unknown_id_is_ignored(tmp_path):
    store = WorkspaceStore(tmp_path / "ws.json")
    item = store.create("One", [])
    store.set_active("ws_nope")
    assert store.active_id == item["id"]


def test_set_active_blank_clears(tmp_path):
    store = WorkspaceStore(tmp_path / "ws.json")
    store.create("One", [])
    store.set_active("")
    assert store.active_id == ""


def test_set_active_switches(tmp_path):
    store = WorkspaceStore(tmp_path / "ws.json")
    a = store.create("A", [])
    b = store.create("B", [])
    store.set_active(a["id"])
    assert store.active_id == a["id"]
    store.set_active(b["id"])
    assert store.active_id == b["id"]
    assert store.get("nope") is None


# ── create ───────────────────────────────────────────────────────────


def test_create_defaults(tmp_path):
    store = WorkspaceStore(tmp_path / "ws.json")
    item = store.create("   ", [], current=-3)
    assert item["name"] == "Workspace"
    assert item["current"] == 0
    assert item["id"].startswith("ws_")
    assert len(item["id"]) > 3
    assert store.active_id == item["id"]


def test_create_evicts_oldest_at_max(tmp_path):
    store = WorkspaceStore(tmp_path / "ws.json")
    store._workspaces = [
        {"id": f"ws_{i:02d}", "name": f"W{i}", "tabs": [], "current": 0, "updated_at": 0.0}
        for i in range(MAX_WORKSPACES)
    ]
    item = store.create("New", [])
    ids = [w["id"] for w in store.list()]
    assert len(ids) == MAX_WORKSPACES
    assert "ws_00" not in ids
    assert ids[-1] == item["id"]


# ── update_tabs / rename ─────────────────────────────────────────────


def test_update_tabs_unknown_id(tmp_path):
    assert WorkspaceStore(tmp_path / "ws.json").update_tabs("ws_nope", []) is False


def test_update_tabs_replaces_tabs(tmp_path):
    store = WorkspaceStore(tmp_path / "ws.json")
    item = store.create("A", [{"url": "https://a.example/"}])
    ok = store.update_tabs(item["id"], [{"url": "https://b.example/", "title": "B"}], current=-1)
    assert ok is True
    got = store.get(item["id"])
    assert got["tabs"] == [{"url": "https://b.example/", "title": "B", "pinned": False}]
    assert got["current"] == 0


def test_rename_unknown_id(tmp_path):
    assert WorkspaceStore(tmp_path / "ws.json").rename("ws_nope", "X") is False


def test_rename_blank_keeps_old_name(tmp_path):
    store = WorkspaceStore(tmp_path / "ws.json")
    item = store.create("Keep", [])
    assert store.rename(item["id"], "   ") is True
    assert store.get(item["id"])["name"] == "Keep"
    assert store.rename(item["id"], "  Renamed  ") is True
    assert store.get(item["id"])["name"] == "Renamed"


# ── delete ───────────────────────────────────────────────────────────


def test_delete_unknown_id(tmp_path):
    assert WorkspaceStore(tmp_path / "ws.json").delete("ws_nope") is False


def test_delete_active_falls_back_to_first_remaining(tmp_path):
    store = WorkspaceStore(tmp_path / "ws.json")
    a = store.create("A", [])
    b = store.create("B", [])
    assert store.active_id == b["id"]
    assert store.delete(b["id"]) is True
    assert store.active_id == a["id"]
    assert store.delete(a["id"]) is True
    assert store.active_id == ""


def test_delete_non_active_keeps_active(tmp_path):
    store = WorkspaceStore(tmp_path / "ws.json")
    a = store.create("A", [])
    b = store.create("B", [])
    assert store.delete(a["id"]) is True
    assert store.active_id == b["id"]


# ── helpers ──────────────────────────────────────────────────────────


def test_snapshot_from_records_clamps_current():
    recs = [{"url": "https://a.example/"}, {"url": "https://b.example/"}]
    snap = snapshot_from_records(recs, current=99)
    assert snap["current"] == 1
    assert len(snap["tabs"]) == 2
    assert snapshot_from_records(recs, current=-5)["current"] == 0
    assert snapshot_from_records([], current=3) == {"tabs": [], "current": 0}


def test_is_workspace_url():
    assert is_workspace_url("https://example.com/") is True
    assert is_workspace_url("file:///tmp/x.html") is True
    assert is_workspace_url("about:blank") is False
