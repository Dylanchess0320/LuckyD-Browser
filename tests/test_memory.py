"""Tests for the memory store."""

from __future__ import annotations

import pytest


@pytest.fixture
def store(tmp_path):
    from memory.store import MemoryStore

    s = MemoryStore(tmp_path / "mem_test")
    s.clear()
    return s


class TestMemoryStore:
    def test_add(self, store):
        store.add(content="User likes Python 3.10", tags=["python"], source="test")
        ctx = store.get_context("Python", limit=5)
        assert "Python 3.10" in ctx

    def test_add_duplicate(self, store):
        store.add(content="Unique fact", source="test")
        store.add(content="Unique fact", source="test")
        ctx = store.get_context("Unique", limit=5)
        assert "Unique" in ctx

    def test_search(self, store):
        store.add(content="Alpha", tags=["a"], source="s1")
        store.add(content="Beta", tags=["b"], source="s2")
        results = store.search_text("Alpha", limit=5)
        assert len(results) >= 1

    def test_search_by_tag(self, store):
        store.add(content="Alpha", tags=["important"], source="s1")
        results = store.graph.search_by_tag("important")
        assert len(results) >= 1

    def test_clear(self, store):
        store.add(content="Something", source="test")
        store.clear()
        results = store.search_text("Something", limit=5)
        assert len(results) == 0

    def test_get_context_empty(self, store):
        ctx = store.get_context("nonexistent_xyz", limit=3)
        assert "no relevant" in ctx.lower()

    def test_multiple_memories(self, store):
        for i in range(5):
            store.add(content=f"Fact number {i}", tags=[f"tag_{i}"], source="test")
        ctx = store.get_context("Fact", limit=5)
        assert "Fact number" in ctx

    def test_delete(self, store):
        mem_id = store.add(content="To delete", source="test")
        store.delete(mem_id)
        assert store.get(mem_id) is None

    def test_persistence(self, tmp_path):
        from memory.store import MemoryStore

        path = tmp_path / "mem"
        s1 = MemoryStore(path)
        s1.add(content="Persistent fact", source="test")
        s2 = MemoryStore(path)
        ctx = s2.get_context("Persistent", limit=5)
        assert "Persistent fact" in ctx

    def test_summarize_empty(self, store):
        summary = store.summarize()
        assert isinstance(summary, str)
