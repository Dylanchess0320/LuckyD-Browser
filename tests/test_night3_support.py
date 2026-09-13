"""Night-3 tests: core/checkpoint.py + core/rules_loader.py.

Covers checkpoint record/undo/undo_to/diff/persistence/pruning and the
project-rules loader (discovery, per-file + total caps, missing dirs).
"""

from __future__ import annotations

import json

import pytest

from core.checkpoint import CheckpointManager
from core.rules_loader import find_rule_files, load_project_rules


@pytest.fixture()
def mgr(tmp_path):
    return CheckpointManager(persist_dir=tmp_path / "cps")


def _write(path, content):
    path.write_text(content, encoding="utf-8")
    return str(path)


# ── checkpoint ─────────────────────────────────────────────────────────


def test_snapshot_before_missing_and_present(mgr, tmp_path):
    assert mgr.snapshot_before(str(tmp_path / "nope.txt")) is None
    f = _write(tmp_path / "a.txt", "hello")
    assert mgr.snapshot_before(f) == "hello"


def test_record_and_undo_roundtrip(mgr, tmp_path):
    f = _write(tmp_path / "a.txt", "v1")
    before = mgr.snapshot_before(f)
    _write(tmp_path / "a.txt", "v2")
    cp = mgr.record_change(f, before, "v2", tool_call_id="t1")
    diff = mgr.undo_last()
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "v1"
    assert diff is not None
    assert diff.additions >= 1 and diff.deletions >= 1
    assert "v2" in diff.diff_text
    assert mgr.list_checkpoints() == []
    # checkpoint id is unique-ish and recorded
    assert cp.checkpoint_id.startswith("cp_")


def test_undo_last_empty(mgr):
    assert mgr.undo_last() is None


def test_undo_last_missing_file_consumes_checkpoint(mgr, tmp_path):
    f = str(tmp_path / "gone.txt")
    mgr.record_change(f, "before", "after")
    assert mgr.undo_last() is None  # nothing to restore
    assert mgr.list_checkpoints() == []  # but checkpoint is consumed


def test_undo_to_specific_checkpoint(mgr, tmp_path):
    f = _write(tmp_path / "a.txt", "v0")
    mgr.record_edit(f, "v0", "v1")
    cp2 = mgr.record_edit(f, "v1", "v2")
    _write(tmp_path / "a.txt", "v3")
    mgr.record_edit(f, "v2", "v3")
    first_id = mgr.checkpoints[0].checkpoint_id
    diffs = mgr.undo_to(first_id)
    # undo_to restores everything *after* the target checkpoint, so the file
    # ends at that checkpoint's after-state ("v1"), with cp0 remaining.
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "v1"
    assert len(mgr.checkpoints) == 1
    assert mgr.checkpoints[0].checkpoint_id == first_id
    assert len(diffs) == 2
    assert mgr.undo_to("missing-id") == []
    # cp2's id is gone now; undo_to it returns []
    assert mgr.undo_to(cp2.checkpoint_id) == []


def test_get_diff(mgr, tmp_path):
    f = _write(tmp_path / "a.txt", "v1")
    assert mgr.get_diff() is None
    cp = mgr.record_edit(f, "v1", "v2\nline2\n")
    d = mgr.get_diff()
    assert d is not None and d.additions == 2
    d2 = mgr.get_diff(cp.checkpoint_id)
    assert d2 is not None and d2.file_path == cp.file_path
    assert mgr.get_diff("nope") is None


def test_list_checkpoints_summary(mgr, tmp_path):
    f = _write(tmp_path / "a.txt", "v1")
    cp = mgr.record_edit(f, "v1", "v22", tool_call_id="t9")
    (lst,) = mgr.list_checkpoints()
    assert lst["id"] == cp.checkpoint_id
    assert lst["tool_call_id"] == "t9"
    assert lst["size_before"] == 2 and lst["size_after"] == 3
    mgr.clear()
    assert mgr.list_checkpoints() == []


def test_persist_and_load(mgr, tmp_path):
    f = _write(tmp_path / "a.txt", "v1")
    mgr.record_edit(f, "v1", "v2")
    files = list((tmp_path / "cps").glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["content_after"] == "v2"
    loaded = mgr.load_persisted(f)
    assert len(loaded) == 1 and loaded[0].content_after == "v2"
    assert mgr.load_persisted(str(tmp_path / "other.txt")) == []


def test_persist_prunes_beyond_50(mgr, tmp_path):
    f = _write(tmp_path / "a.txt", "v")
    for i in range(55):
        mgr.record_edit(f, "v", f"v{i}")
    files = list((tmp_path / "cps").glob("*.json"))
    assert len(files) == 50


def test_record_change_none_before(mgr, tmp_path):
    f = _write(tmp_path / "a.txt", "v1")
    cp = mgr.record_change(f, None, "v1")
    assert cp.content_before == ""
    d = mgr.get_diff(cp.checkpoint_id)
    assert d is not None and d.additions == 1


# ── rules_loader ───────────────────────────────────────────────────────


def test_find_rule_files_discovers_and_dedupes(tmp_path):
    (tmp_path / "AGENTS.md").write_text("rules", encoding="utf-8")
    (tmp_path / ".clinerules").write_text("more", encoding="utf-8")
    found = find_rule_files([tmp_path, tmp_path])  # same dir twice → deduped
    assert len(found) == 2
    assert found[0].name == "AGENTS.md"  # RULE_FILE_NAMES order


def test_find_rule_files_missing_dir_ok(tmp_path):
    assert find_rule_files([tmp_path / "missing"]) == []
    assert find_rule_files([None]) == []


def test_load_project_rules_empty(tmp_path, monkeypatch):
    # load_project_rules scans cwd AND config.PROJECT_DIR; point both at an
    # empty dir to exercise the no-rules path.
    import config

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "PROJECT_DIR", tmp_path)
    assert load_project_rules(extra_dirs=[]) == ""


def test_load_project_rules_combines_and_caps(tmp_path, monkeypatch):
    import config

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "PROJECT_DIR", tmp_path)
    (tmp_path / "AGENTS.md").write_text("A" * 7000, encoding="utf-8")  # over per-file cap
    (tmp_path / "CLAUDE.md").write_text("B" * 7000, encoding="utf-8")
    out = load_project_rules(extra_dirs=[])
    assert "## Project Rules" in out
    assert "... [rules truncated]" in out
    assert len(out) <= 12000 + 2000  # total cap respected (with headers)


def test_load_project_rules_extra_dirs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    (other / "LUCKYD.md").write_text("lucky rules", encoding="utf-8")
    out = load_project_rules(extra_dirs=[other])
    assert "lucky rules" in out
    assert "LUCKYD.md" in out
