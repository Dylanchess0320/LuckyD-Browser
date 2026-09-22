"""Coverage tests for browser_core/workflows.py — record/replay plumbing.

Targets the ranges missed by the existing suite: the frozen DATA_DIR
branch, elements_js, step_record edge cases, every score_fingerprint
field branch, resolve_index heal/fallback paths, WorkflowRecorder
properties, and WorkflowStore list/load/save/delete.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from browser_core import workflows
from browser_core.workflows import (
    MAX_STEPS,
    WorkflowRecorder,
    WorkflowStore,
    elements_js,
    fingerprint_js,
    resolve_index,
    score_fingerprint,
    slugify,
    step_record,
)

# ── module-level branches ────────────────────────────────────────────


def test_frozen_data_dir_uses_localappdata(monkeypatch):
    """The sys.frozen import branch: LOCALAPPDATA wins when frozen."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", "C:/Users/T/AppData/Local")
    try:
        importlib.reload(workflows)
        assert Path("C:/Users/T/AppData/Local") / "LuckyDBrowser" == workflows.DATA_DIR
        assert workflows.WORKFLOWS_DIR == workflows.DATA_DIR / "workflows"
    finally:
        monkeypatch.undo()
        importlib.reload(workflows)


def test_fingerprint_js_embeds_index():
    js = fingerprint_js(3)
    assert '[data-ld-agent="3"]' in js
    assert "JSON.stringify" in js


def test_elements_js_returns_all_elements_probe():
    js = elements_js()
    assert "data-ld-agent" in js
    assert "JSON.stringify" in js


def test_slugify_empty_string_returns_empty():
    assert slugify("") == ""


# ── step_record ──────────────────────────────────────────────────────


def test_step_record_rejects_unknown_action():
    assert step_record({"action": "dance"}) is None
    assert step_record({}) is None


def test_step_record_indexed_action_requires_index():
    assert step_record({"action": "click"}) is None
    assert step_record({"action": "click", "index": -1}) is None
    assert step_record({"action": "click", "index": ""}) is None


def test_step_record_keeps_fingerprint_when_tagged():
    fp = {"tag": "button", "text": "Buy"}
    assert step_record({"action": "click", "index": 2}, fp) == {
        "action": "click",
        "index": 2,
        "target": fp,
    }


def test_step_record_ignores_untagged_fingerprint():
    step = step_record({"action": "type", "index": 0, "text": "hi"}, {"tag": ""})
    assert step == {"action": "type", "index": 0, "text": "hi"}
    assert "target" not in step


def test_step_record_text_actions_carry_text():
    for kind in ("press", "scroll", "wait"):
        assert step_record({"action": kind, "text": "x"}) == {"action": kind, "text": "x"}
    # type/select are indexed actions: they need an index too.
    for kind in ("type", "select"):
        assert step_record({"action": kind, "index": 0, "text": "x"}) == {
            "action": kind,
            "index": 0,
            "text": "x",
        }


def test_step_record_navigate_requires_http_url():
    assert step_record({"action": "navigate", "url": "ftp://x/y"}) is None
    assert step_record({"action": "navigate"}) is None
    assert step_record({"action": "navigate", "url": "https://example.com/a"}) == {
        "action": "navigate",
        "url": "https://example.com/a",
    }


# ── score_fingerprint ────────────────────────────────────────────────


def _fp(**kw):
    base = {
        "tag": "button",
        "text": "Buy now",
        "el_id": "b1",
        "name": "buy",
        "aria": "Buy",
        "href": "https://x/y",
    }
    base.update(kw)
    return base


def test_score_fingerprint_field_weights():
    saved = _fp()
    assert score_fingerprint(saved, {"tag": "button"}) == 0.15
    assert score_fingerprint(saved, {"tag": "x", "el_id": "b1"}) == 0.5
    assert score_fingerprint(saved, {"tag": "x", "name": "buy"}) == 0.35
    assert score_fingerprint(saved, {"tag": "x", "aria": "Buy"}) == 0.3
    assert score_fingerprint(saved, {"tag": "x", "href": "https://x/y"}) == 0.2
    assert score_fingerprint(saved, {"tag": "x", "text": "Buy now"}) == pytest.approx(0.4)
    # Mismatched values contribute nothing.
    assert score_fingerprint(saved, {"tag": "x", "el_id": "other"}) == 0.0
    assert score_fingerprint(saved, {"tag": "x", "name": "sell"}) == 0.0
    # Missing/blank text on either side contributes nothing.
    assert score_fingerprint(saved, {"tag": "x", "text": ""}) == 0.0
    assert score_fingerprint({"tag": "x"}, {"tag": "x", "text": "Buy"}) == 0.15


def test_score_fingerprint_perfect_match_caps_at_one():
    assert score_fingerprint(_fp(), _fp()) == 1.0


def test_score_fingerprint_partial_text_similarity():
    saved = _fp(text="Buy now please")
    cand = {"tag": "x", "text": "buy now"}
    score = score_fingerprint(saved, cand)
    assert 0.0 < score < 0.4


# ── resolve_index ────────────────────────────────────────────────────


def test_resolve_index_heals_to_best_match():
    saved = {"tag": "button", "text": "Buy now", "el_id": "b1"}
    cands = [
        {"index": 0, "tag": "div", "text": "nope"},
        {"index": 7, "tag": "button", "text": "Buy now", "el_id": "b1"},
    ]
    assert resolve_index(saved, cands, 3) == (7, True)


def test_resolve_index_falls_back_below_threshold():
    saved = {"tag": "button", "text": "Buy now"}
    cands = [{"index": 7, "tag": "input", "text": "something else entirely"}]
    assert resolve_index(saved, cands, 3) == (3, False)


def test_resolve_index_without_fingerprint_uses_recorded():
    assert resolve_index(None, [{"index": 9}], 3) == (3, False)
    assert resolve_index(None, [], 3) == (3, False)


def test_resolve_index_skips_candidates_that_raise():
    saved = {"tag": "button"}
    assert resolve_index(saved, ["not-a-dict"], 4) == (4, False)


def test_resolve_index_ignores_zero_score_candidates():
    saved = {"tag": "button"}
    cands = [{"index": 5, "tag": "input"}]  # score 0.0: nothing matches
    assert resolve_index(saved, cands, 3) == (3, False)


def test_resolve_index_continues_past_bad_candidates():
    saved = {"tag": "button", "text": "Buy now", "el_id": "b1"}
    cands = [
        "not-a-dict",  # raises inside score_fingerprint -> skipped, loop continues
        {"index": 7, "tag": "button", "text": "Buy now", "el_id": "b1"},
    ]
    assert resolve_index(saved, cands, 3) == (7, True)


def test_resolve_index_honours_custom_threshold():
    saved = {"tag": "button"}
    cands = [{"index": 5, "tag": "button"}]  # score 0.15
    assert resolve_index(saved, cands, 2, threshold=0.1) == (5, True)
    assert resolve_index(saved, cands, 2, threshold=0.9) == (2, False)


# ── WorkflowRecorder ─────────────────────────────────────────────────


def test_recorder_lifecycle_and_status():
    rec = WorkflowRecorder()
    assert rec.active is False
    assert rec.name is None
    assert rec.status() == {"recording": False, "name": None, "steps": 0}
    slug = rec.start("My Flow!")
    assert rec.name == slug == "My-Flow"
    assert rec.status() == {"recording": True, "name": slug, "steps": 0}
    rec.add({"action": "click", "index": 1})
    assert rec.status()["steps"] == 1
    name, steps = rec.stop()
    assert (name, steps) == (slug, [{"action": "click", "index": 1}])
    assert rec.active is False
    assert rec.name is None


def test_recorder_add_none_is_ignored():
    rec = WorkflowRecorder()
    rec.start("x")
    rec.add(None)
    assert rec.status()["steps"] == 0


def test_recorder_add_while_idle_is_ignored():
    rec = WorkflowRecorder()
    rec.add({"action": "click", "index": 0})
    assert rec.status()["steps"] == 0


# ── WorkflowStore ────────────────────────────────────────────────────


def _store(tmp_path) -> WorkflowStore:
    return WorkflowStore(tmp_path / "wf")


def test_store_default_directory():
    assert WorkflowStore()._dir == workflows.WORKFLOWS_DIR


def test_store_save_and_load_roundtrip(tmp_path):
    store = _store(tmp_path)
    slug = store.save("My Flow!", [{"action": "click", "index": 1}])
    assert slug == "My-Flow"
    data = store.load("My Flow!")
    assert data["name"] == "My-Flow"
    assert data["steps"] == [{"action": "click", "index": 1}]
    assert data["version"] == 1
    assert data["created"] > 0


def test_store_save_truncates_to_max_steps(tmp_path):
    store = _store(tmp_path)
    steps = [{"action": "wait", "text": ""} for _ in range(MAX_STEPS + 10)]
    store.save("big", steps)
    assert len(store.load("big")["steps"]) == MAX_STEPS


def test_store_load_missing_file_returns_none(tmp_path):
    assert _store(tmp_path).load("nope") is None


def test_store_load_bad_payloads_return_none(tmp_path):
    store = _store(tmp_path)
    bad = store._path("bad")
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{not json", encoding="utf-8")
    assert store.load("bad") is None
    bad.write_text(json.dumps({"name": "x"}), encoding="utf-8")  # steps missing
    assert store.load("bad") is None
    bad.write_text(json.dumps({"steps": "nope"}), encoding="utf-8")  # not a list
    assert store.load("bad") is None
    bad.write_text(json.dumps([1, 2]), encoding="utf-8")  # not a dict
    assert store.load("bad") is None


def test_store_list_skips_bad_files(tmp_path):
    store = _store(tmp_path)
    store.save("good", [{"action": "back"}])
    (store._dir / "broken.json").write_text("{oops", encoding="utf-8")
    rows = store.list()
    assert [r["name"] for r in rows] == ["good"]
    assert rows[0]["steps"] == 1
    assert rows[0]["created"] > 0


def test_store_list_missing_dir_returns_empty(tmp_path):
    assert _store(tmp_path).list() == []


def test_store_delete(tmp_path):
    store = _store(tmp_path)
    store.save("gone", [])
    assert store.delete("gone") is True
    assert store.load("gone") is None
    assert store.delete("gone") is True  # missing_ok


def test_store_delete_failure_returns_false(tmp_path):
    store = _store(tmp_path)
    blocker = store._path("blocked")
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.mkdir()  # a directory: unlink raises IsADirectoryError
    assert store.delete("blocked") is False
