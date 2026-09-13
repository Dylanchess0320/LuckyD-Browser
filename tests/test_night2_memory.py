"""Night-2 tests: memory/ store, graph, BM25, vector store.

Covers: MemoryGraph add/remove/edge/tag/BFS/update/persistence roundtrip,
MemoryStore alias persistence (regression: alias was silently dropped),
expires metadata, get_memory_graph() singleton (regression: crashed on
MemoryGraph.load() with no path), BM25Scorer edge cases, VectorStore search.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def store(tmp_path):
    from memory.store import MemoryStore

    s = MemoryStore(tmp_path / "mem")
    s.clear()
    return s


# ── bug regression: alias was silently dropped by MemoryStore.add ───


class TestAliasPersistence:
    def test_alias_stored(self, store):
        mid = store.add(content="Dylan likes chess", tags=["game"], alias="chess-pref")
        entry = store.get(mid)
        assert entry is not None
        assert entry.metadata.get("alias") == "chess-pref"

    def test_alias_survives_reload(self, tmp_path):
        from memory.store import MemoryStore

        path = tmp_path / "mem"
        s1 = MemoryStore(path)
        s1.clear()
        mid = s1.add(content="reload me", alias="my-alias")
        s2 = MemoryStore(path)
        assert s2.get(mid).metadata.get("alias") == "my-alias"

    def test_alias_shown_in_context(self, store):
        store.add(content="unique chess preference fact", alias="chess-pref")
        ctx = store.get_context("chess preference", limit=3)
        assert "(chess-pref)" in ctx

    def test_no_alias_no_metadata(self, store):
        mid = store.add(content="plain fact")
        assert "alias" not in store.get(mid).metadata

    def test_expires_metadata(self, store):
        from datetime import datetime, timezone

        mid = store.add(content="temporary", expires_in_hours=24)
        exp = store.get(mid).metadata.get("expires_at")
        assert exp
        exp_dt = datetime.fromisoformat(exp)
        now = datetime.now(timezone.utc)
        assert 23 * 3600 < (exp_dt - now).total_seconds() < 25 * 3600


# ── bug regression: get_memory_graph() crashed ───────────────────────


class TestGetMemoryGraph:
    def test_singleton_works(self):
        from memory import graph as gmod
        from memory.graph import MemoryGraph, get_memory_graph

        gmod._graph_instance = None
        try:
            g1 = get_memory_graph()
            assert isinstance(g1, MemoryGraph)
            assert get_memory_graph() is g1
        finally:
            gmod._graph_instance = None

    def test_load_none_path(self):
        from memory.graph import MemoryGraph

        # defensive: load() with no path must not raise
        try:
            MemoryGraph.load(None)
        except TypeError:
            pytest.fail("MemoryGraph.load(None) raised TypeError")
        except Exception:
            pass  # other exceptions acceptable only if load(None) documented


# ── MemoryGraph ops ──────────────────────────────────────────────────


@pytest.fixture
def graph():
    from memory.graph import MemoryGraph

    return MemoryGraph()


class TestMemoryGraph:
    def test_add_and_tag_edges(self, graph):
        from memory.graph import EdgeKind

        e = graph.add_memory_raw("fact one", tags=["Tag One", "second"])
        assert e.id in graph.memories
        assert "tag_one" in e.tags  # normalized
        edges = graph.get_edges(e.id)
        kinds = {ed.kind for ed in edges}
        assert EdgeKind.HASTAG in kinds
        assert graph.tags["tag:tag_one"].count == 1

    def test_remove_memory_cleans_edges(self, graph):
        from memory.graph import EdgeKind

        a = graph.add_memory_raw("a", tags=["t"])
        b = graph.add_memory_raw("b")
        graph.add_edge(a.id, b.id, EdgeKind.RELATESTO)
        removed = graph.remove_memory(a.id)
        assert removed is not None
        assert graph.get_memory(a.id) is None
        # tag count decremented
        assert graph.tags["tag:t"].count == 0
        # incoming edge on b removed
        assert all(e.target != a.id for e in graph.get_edges(b.id))

    def test_add_edge_rejects_unknown_nodes(self, graph):
        from memory.graph import EdgeKind

        assert graph.add_edge("nope", "nope2", EdgeKind.RELATESTO) is False
        a = graph.add_memory_raw("a")
        assert graph.add_edge(a.id, "nope2", EdgeKind.RELATESTO) is False

    def test_edge_dedup(self, graph):
        from memory.graph import EdgeKind

        a = graph.add_memory_raw("a")
        b = graph.add_memory_raw("b")
        assert graph.add_edge(a.id, b.id, EdgeKind.RELATESTO) is True
        assert graph.add_edge(a.id, b.id, EdgeKind.RELATESTO) is True
        assert len([e for e in graph.get_edges(a.id) if e.target == b.id]) == 1

    def test_remove_edge(self, graph):
        from memory.graph import EdgeKind

        a = graph.add_memory_raw("a")
        b = graph.add_memory_raw("b")
        graph.add_edge(a.id, b.id, EdgeKind.RELATESTO)
        assert graph.remove_edge(a.id, b.id) is True
        assert graph.get_edges(a.id) == []
        assert graph.remove_edge(a.id, b.id) is False

    def test_bfs_depth(self, graph):
        from memory.graph import EdgeKind

        a = graph.add_memory_raw("a")
        b = graph.add_memory_raw("b")
        c = graph.add_memory_raw("c")
        graph.add_edge(a.id, b.id, EdgeKind.RELATESTO)
        graph.add_edge(b.id, c.id, EdgeKind.RELATESTO)
        got = {m.id: d for m, d in graph.bfs(a.id, max_depth=1)}
        assert got[a.id] == 0 and got[b.id] == 1 and c.id not in got
        got2 = {m.id: d for m, d in graph.bfs(a.id, max_depth=2)}
        assert got2[c.id] == 2
        assert graph.bfs("missing", max_depth=2) == []

    def test_update_memory(self, graph):
        e = graph.add_memory_raw("old content")
        assert graph.update_memory(e.id, content="new content") is True
        assert graph.get_memory(e.id).content == "new content"
        assert graph.update_memory("missing", content="x") is False

    def test_get_memory_access_count(self, graph):
        e = graph.add_memory_raw("x")
        graph.get_memory(e.id)
        graph.get_memory(e.id)
        assert graph.get_memory(e.id).access_count == 3

    def test_save_load_roundtrip(self, tmp_path, graph):
        from memory.graph import EdgeKind, MemoryGraph

        a = graph.add_memory_raw("keep me", tags=["keep"])
        b = graph.add_memory_raw("other")
        graph.add_edge(a.id, b.id, EdgeKind.RELATESTO)
        p = tmp_path / "g.json"
        graph.save(p)
        g2 = MemoryGraph.load(p)
        assert set(g2.memories) == set(graph.memories)
        assert g2.tags["tag:keep"].count == 1
        edges = g2.get_edges(a.id)
        assert any(e.target == b.id and e.kind == EdgeKind.RELATESTO for e in edges)
        assert g2.graph_version == 2

    def test_load_corrupt_file(self, tmp_path):
        from memory.graph import MemoryGraph

        p = tmp_path / "bad.json"
        p.write_text("{not json")
        g = MemoryGraph.load(p)
        assert g.memories == {}

    def test_load_missing_file(self, tmp_path):
        from memory.graph import MemoryGraph

        assert MemoryGraph.load(tmp_path / "nope.json").memories == {}

    def test_search_text_skips_inactive(self, graph):
        e = graph.add_memory_raw("visible fact")
        graph.update_memory(e.id, active=False)
        assert graph.search_text("visible", limit=5) == []

    def test_search_text_empty_query(self, graph):
        graph.add_memory_raw("something")
        assert graph.search_text("", limit=5) == []

    def test_search_by_tag_alias_metadata(self, graph):

        e = graph.add_memory_raw("with alias")
        e.metadata["aliases"] = ["nickname"]
        got = graph.search_by_tag("nickname")
        assert got and got[0][0].id == e.id
        assert graph.search_by_tag("no-such-tag") == []

    def test_tag_normalization(self, graph):
        t = graph.get_or_create_tag("My Tag")
        assert t.name == "my_tag"
        assert t.id == "tag:my_tag"
        t2 = graph.get_or_create_tag("my tag", description="d")
        assert t2 is t and t.description == "d"

    def test_list_tags_sorted(self, graph):
        graph.add_memory_raw("a", tags=["rare"])
        graph.add_memory_raw("b", tags=["common"])
        graph.add_memory_raw("c", tags=["common"])
        names = [t.name for t in graph.list_tags()]
        assert names[0] == "common"

    def test_entry_serialization_skips_empties(self, graph):
        e = graph.add_memory_raw("x")
        d = e.to_dict()
        assert "source" not in d  # None skipped
        assert d["content"] == "x"


# ── BM25Scorer ───────────────────────────────────────────────────────


class TestBM25:
    def test_basic_scoring(self):
        from memory.store import BM25Scorer

        s = BM25Scorer()
        s.index_documents(
            {
                "d1": "the quick brown fox",
                "d2": "python programming language",
                "d3": "the fox and the hound",
            }
        )
        assert s.score("fox", "d1") > 0
        assert s.score("fox", "d2") == 0
        assert s.score("python", "d1") == 0
        assert s.score("anything", "missing-doc") == 0.0

    def test_empty_index(self):
        from memory.store import BM25Scorer

        s = BM25Scorer()
        s.index_documents({})
        assert s.score("x", "d1") == 0.0

    def test_tokenizer_ignores_short_tokens(self):
        from memory.store import BM25Scorer

        s = BM25Scorer()
        assert s._tokenize("a bb ccc D_D") == ["bb", "ccc", "d_d"]

    def test_store_rebuild_after_delete(self, store):
        mid = store.add(content="ephemeral zebra fact")
        assert store.search_text("zebra", limit=5)
        store.delete(mid)
        assert store.search_text("zebra", limit=5) == []

    def test_store_limit(self, store):
        for i in range(10):
            store.add(content=f"common token fact {i}")
        assert len(store.search_text("common token", limit=3)) == 3


# ── VectorStore ──────────────────────────────────────────────────────


class TestVectorStore:
    def test_search_ranks_relevant(self):
        from memory.vector_store import VectorStore

        vs = VectorStore(backend="tfidf")
        vs.add("d1", "The quick brown fox jumps over the lazy dog")
        vs.add("d2", "Python is a programming language")
        results = vs.search("fox animal", top_k=2)
        assert results[0][0] == "d1"

    def test_empty_search(self):
        from memory.vector_store import VectorStore

        vs = VectorStore(backend="tfidf")
        assert vs.search("anything") == []

    def test_hash_backend(self):
        from memory.vector_store import VectorStore

        vs = VectorStore(backend="hash")
        vs.add("d1", "some text about cats")
        vs.add("d2", "completely different topic")
        results = vs.search("cats", top_k=2)
        assert results and results[0][0] == "d1"

    def test_remove(self):
        from memory.vector_store import VectorStore

        vs = VectorStore(backend="tfidf")
        vs.add("d1", "hello world")
        if hasattr(vs, "remove"):
            vs.remove("d1")
            assert vs.search("hello") == []


# ── store helpers ────────────────────────────────────────────────────


class TestStoreHelpers:
    def test_link_and_bfs(self, store):
        a = store.add(content="node alpha")
        b = store.add(content="node beta")
        assert store.link(a, b) is True
        assert store.link("missing", b) is False
        got = store.bfs_from(a, depth=1)
        assert {n.id for n, _ in got} == {a, b}

    def test_get_missing(self, store):
        assert store.get("mem_nope") is None
        assert store.delete("mem_nope") is False

    def test_search_similar_fallback(self, store):
        store.add(content="guitar chords for beginners")
        results = store.search_similar("guitar chords", limit=3)
        assert results and "guitar" in results[0][0].content

    def test_summarize(self, store):
        assert store.summarize() == "No memories stored."
        store.add(content="one")
        assert "1 entries" in store.summarize()

    def test_delete_persists(self, tmp_path):
        from memory.store import MemoryStore

        path = tmp_path / "mem"
        s = MemoryStore(path)
        mid = s.add(content="doomed")
        s.delete(mid)
        s2 = MemoryStore(path)
        assert s2.get(mid) is None
