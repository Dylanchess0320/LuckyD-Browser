"""Night-5 tests: core/advanced_memory.py (was ~0% covered).

CRUD, hybrid semantic+keyword search, category recall, importance decay,
old-memory compression, export/stats, and the MemoryRemember/MemoryRecall
drop-in shims — all against a throwaway sqlite file in tmp_path.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from core.advanced_memory import (
    AdvancedMemorySystem,
    Memory,
    _cosine,
    _deserialize_vector,
    _hash_bucket,
    _serialize_vector,
    _tfidf_vector,
    _tokenize,
    _utcnow,
)

_summarize_fn = AdvancedMemorySystem._summarize


@pytest.fixture()
def memsys(tmp_path):
    ms = AdvancedMemorySystem(db_path=tmp_path / "test_mem.db", compact_threshold=10_000)
    yield ms
    ms.close()


# ── helpers ──────────────────────────────────────────────────────────────


def test_tokenize_and_hash_bucket():
    toks = _tokenize("Hello, world! Hello.")
    assert toks == ["hello", "world", "hello"]
    assert _tokenize("") == []
    assert 0 <= _hash_bucket("term", 256) < 256


def test_tfidf_vector_sublinear_and_cosine():
    vec = _tfidf_vector(["a", "a", "a", "b"], dims=64)
    # 'a' appears 3x -> sublinear tf: 1 + log(3); 'b' once -> 1.0
    assert len(vec) > 0
    for w in vec.values():
        assert w > 0
    assert _cosine(vec, vec) == pytest.approx(1.0)
    other = _tfidf_vector(["zzzqqq"], dims=64)
    assert _cosine(vec, other) < 0.5


def test_serialize_vector_roundtrip():
    vec = {3: 1.5, 200: 0.25}
    assert _deserialize_vector(_serialize_vector(vec)) == vec
    assert _deserialize_vector(None) == {}
    assert _deserialize_vector("") == {}


# ── CRUD ─────────────────────────────────────────────────────────────────


def test_add_memory_roundtrip(memsys):
    mid = memsys.add_memory(
        "Dylan prefers dark mode",
        category="Prefs",
        tags=[" UI ", "theme"],
        importance=0.8,
        source="chat",
    )
    m = memsys.get(mid)
    assert isinstance(m, Memory)
    assert m.content == "Dylan prefers dark mode"
    assert m.category == "prefs"  # normalized
    assert m.tags == ["ui", "theme"]  # normalized
    assert m.importance == 0.8
    assert m.source == "chat"
    assert m.summary  # auto-generated
    assert m.access_count == 0


def test_add_memory_rejects_empty(memsys):
    with pytest.raises(ValueError):
        memsys.add_memory("   ")


def test_add_memory_importance_clamped(memsys):
    mid = memsys.add_memory("x", importance=5.0)
    assert memsys.get(mid).importance == 1.0
    mid2 = memsys.add_memory("y", importance=-2.0)
    assert memsys.get(mid2).importance == 0.0


def test_add_memory_explicit_id_and_summary(memsys):
    mid = memsys.add_memory("content here", memory_id="abc123", summary="custom")
    assert mid == "abc123"
    assert memsys.get("abc123").summary == "custom"


def test_get_prefix_id(memsys):
    mid = memsys.add_memory("prefix lookup test")
    assert memsys.get(mid[:8]).id == mid
    assert memsys.get("does-not-exist") is None


def test_delete_full_and_prefix(memsys):
    mid = memsys.add_memory("to be deleted")
    assert memsys.delete(mid) is True
    assert memsys.get(mid) is None
    mid2 = memsys.add_memory("to be deleted too")
    assert memsys.delete(mid2[:6]) is True
    assert memsys.delete("nope") is False


def test_memory_to_dict_and_from_row(memsys):
    mid = memsys.add_memory("round trip", tags=["t"], importance=0.4)
    m = memsys.get(mid)
    d = m.to_dict()
    assert d["content"] == "round trip"
    assert d["tags"] == ["t"]
    assert "embedding" in d


# ── search ───────────────────────────────────────────────────────────────


def test_search_empty_query(memsys):
    memsys.add_memory("something")
    assert memsys.search("") == []
    assert memsys.search("   ") == []


def test_search_finds_relevant_first(memsys):
    memsys.add_memory("Dylan likes chess puzzles", category="hobby", importance=0.5)
    memsys.add_memory("grocery list: milk and eggs", category="todo", importance=0.5)
    results = memsys.search("chess puzzles rating")
    assert results
    assert "chess" in results[0].content


def test_search_category_filter(memsys):
    memsys.add_memory("chess tactics book", category="hobby")
    memsys.add_memory("chess engine config", category="work")
    results = memsys.search("chess", category="work")
    assert all(m.category == "work" for m in results)
    assert len(results) == 1


def test_search_touch_bumps_access_count(memsys):
    mid = memsys.add_memory("touch me", importance=0.5)
    memsys.search("touch me", touch=True)
    m = memsys.get(mid)
    assert m.access_count == 1
    assert m.importance > 0.5  # access boost applied
    memsys.search("touch me", touch=False)
    assert memsys.get(mid).access_count == 1  # untouched


def test_search_limit(memsys):
    for i in range(5):
        memsys.add_memory(f"shared topic number {i}", category="x")
    assert len(memsys.search("shared topic", limit=2)) == 2


def test_recall_by_category_ordering(memsys):
    memsys.add_memory("low", category="c", importance=0.2)
    memsys.add_memory("high", category="c", importance=0.9)
    memsys.add_memory("other cat", category="z", importance=1.0)
    got = memsys.recall_by_category("c")
    assert [m.content for m in got] == ["high", "low"]
    got2 = memsys.recall_by_category("c", min_importance=0.5)
    assert [m.content for m in got2] == ["high"]


def test_update_importance_clamps(memsys):
    mid = memsys.add_memory("imp", importance=0.5)
    assert memsys.update_importance(mid, 0.99) is True
    assert memsys.get(mid).importance == pytest.approx(0.99)
    memsys.update_importance(mid, 42.0)
    assert memsys.get(mid).importance == 1.0
    assert memsys.update_importance("missing", 0.5) is False


# ── decay & compression ──────────────────────────────────────────────────


def _age_memory(memsys, mid, days):
    old = (_utcnow() - timedelta(days=days)).isoformat()
    with memsys._lock, memsys._conn:
        memsys._conn.execute(
            "UPDATE memories SET last_accessed = ?, created_at = ? WHERE id = ?",
            (old, old, mid),
        )


def test_decay_memories_aged_loses_importance(memsys):
    fresh = memsys.add_memory("fresh", importance=0.8)
    stale = memsys.add_memory("stale", importance=0.8)
    _age_memory(memsys, stale, days=60)
    updated = memsys.decay_memories(half_life_days=30.0)
    assert updated == 1
    assert memsys.get(stale).importance < 0.8
    assert memsys.get(fresh).importance == pytest.approx(0.8)


def test_decay_memories_floored(memsys):
    mid = memsys.add_memory("ancient", importance=0.05)
    _age_memory(memsys, mid, days=3650)
    memsys.decay_memories(half_life_days=30.0)
    assert memsys.get(mid).importance >= 0.0


def test_compress_old_memories_merges_group(memsys):
    ids = [
        memsys.add_memory(f"old fact {i}", category="notes", importance=0.1, tags=["t"])
        for i in range(3)
    ]
    for mid in ids:
        _age_memory(memsys, mid, days=60)
    merged = memsys.compress_old_memories(older_than_days=30.0, max_importance=0.3, keep_minimum=0)
    assert merged == 3
    remaining = memsys.recall_by_category("notes")
    assert len(remaining) == 1  # digest
    assert "old fact" in remaining[0].content
    assert remaining[0].source == "compressed"
    for mid in ids:
        assert memsys.get(mid) is None


def test_compress_old_memories_respects_keep_minimum(memsys):
    ids = [memsys.add_memory(f"fact {i}", category="notes", importance=0.1) for i in range(4)]
    for mid in ids:
        _age_memory(memsys, mid, days=60)
    merged = memsys.compress_old_memories(older_than_days=30.0, max_importance=0.3, keep_minimum=10)
    assert merged == 0  # keep_minimum budget is 0


def test_compress_old_memories_singleton_group_untouched(memsys):
    mid = memsys.add_memory("lonely old fact", category="notes", importance=0.1)
    _age_memory(memsys, mid, days=60)
    merged = memsys.compress_old_memories(older_than_days=30.0, max_importance=0.3, keep_minimum=0)
    assert merged == 0
    assert memsys.get(mid) is not None


# ── export / stats / shims ───────────────────────────────────────────────


def test_export_writes_json(memsys, tmp_path):
    memsys.add_memory("export me", category="c")
    out = memsys.export(tmp_path / "exp.json")
    payload = json.loads((tmp_path / "exp.json").read_text(encoding="utf-8"))
    assert payload["version"]
    assert payload["count"] == 1
    assert payload["memories"][0]["content"] == "export me"
    assert out == str(tmp_path / "exp.json")


def test_stats(memsys):
    memsys.add_memory("a", category="x", importance=0.2)
    memsys.add_memory("b", category="x", importance=0.8)
    memsys.add_memory("c", category="y", importance=0.5)
    s = memsys.stats()
    assert s["total"] == 3
    assert s["categories"] == {"x": 2, "y": 1}
    assert s["avg_importance"] == pytest.approx(0.5)
    assert s["max_importance"] == pytest.approx(0.8)
    assert s["accessed_last_7d"] == 3
    assert s["db_size_bytes"] > 0


def test_add_and_get_context_shims(memsys):
    mid = memsys.add("remember the meeting notes", tags=["work"], alias="work")
    assert memsys.get(mid).category == "work"
    ctx = memsys.get_context("meeting notes")
    assert ctx.startswith("1. remember the meeting notes")
    assert "(importance:" in ctx


def test_get_context_no_memories(tmp_path):
    empty = AdvancedMemorySystem(db_path=tmp_path / "empty.db")
    assert empty.get_context("anything at all") == "No relevant memories found."
    empty.close()


def test_summarize_truncation():
    long_text = "First sentence here. " + "word " * 100
    s = _summarize_fn(long_text, max_chars=60)
    assert len(s) <= 62  # 60 + ellipsis
    assert s.endswith("…") or s.endswith(".")
    short = "tiny"
    assert _summarize_fn(short) == "tiny"


def test_context_manager_protocol(memsys):
    with AdvancedMemorySystem(
        db_path=memsys.db_path.parent / "ctx.db", compact_threshold=10_000
    ) as ms2:
        mid = ms2.add_memory("ctx test")
        assert ms2.get(mid) is not None
    # closed cleanly; reopening works
    ms3 = AdvancedMemorySystem(db_path=memsys.db_path.parent / "ctx.db")
    assert ms3.get(mid) is not None
    ms3.close()
