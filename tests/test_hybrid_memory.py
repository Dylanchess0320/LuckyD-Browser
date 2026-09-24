"""P2: hybrid fused memory retrieval (RRF of BM25 + semantic).

get_context used BM25 with semantic as a last-resort fallback, so a
strong semantic hit behind one weak keyword match never surfaced.
Both rankings now fuse via reciprocal rank fusion.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from memory.store import MemoryStore


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path)


def _add(store, content, tags=()):
    return store.add(content=content, tags=list(tags), source="test")


class TestHybrid:
    def test_exact_match_still_first(self, store):
        _add(store, "Dylan likes chess openings")
        _add(store, "unrelated cooking note")
        hits = store.search_hybrid("chess openings", limit=2)
        assert hits and "chess" in hits[0][0].content

    def test_semantic_only_hit_surfaces(self, store):
        # BM25 finds nothing (no shared terms); stub the semantic leg as
        # ONNX would rank it, and fusion must surface it anyway.
        first = _add(store, "king pawn endgame technique")
        _add(store, "completely different subject")
        node = store.get(first)
        with (
            patch.object(MemoryStore, "search_similar", return_value=[(node, 0.9)]),
            patch.object(MemoryStore, "search_text", return_value=[]),
        ):
            hits = store.search_hybrid("chess endgames", limit=2)
        assert [n.id for n, _ in hits] == [first]

    def test_agreement_ranks_highest(self, store):
        a = _add(store, "alpha beta gamma")
        b = _add(store, "alpha delta epsilon")
        c = _add(store, "zeta eta theta")
        na, nb, nc = store.get(a), store.get(b), store.get(c)
        with (
            patch.object(MemoryStore, "search_text", return_value=[(na, 5.0), (nb, 1.0)]),
            patch.object(MemoryStore, "search_similar", return_value=[(nb, 0.9), (nc, 0.8)]),
        ):
            # nb appears in both lists -> outranks single-list hits.
            hits = store.search_hybrid("alpha", limit=3)
        assert next(n.id for n, _ in hits) == b

    def test_limit_respected(self, store):
        for i in range(5):
            _add(store, f"shared topic number {i}")
        assert len(store.search_hybrid("shared topic", limit=2)) == 2

    def test_get_context_uses_fusion(self, store):
        _add(store, "Dylan prefers dark mode")
        ctx = store.get_context("dark mode", limit=3)
        assert "dark mode" in ctx
        assert "(no relevant memories)" not in ctx

    def test_empty_store_message(self, store):
        assert store.get_context("anything") == "  (no relevant memories)"
