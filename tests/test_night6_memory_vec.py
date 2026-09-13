"""Night-6 wave-2 tests: memory/vector_store.py + memory/embeddings.py.

Extends test_night2_memory.py (which only covered basic VectorStore search)
into: backend selection, TF-IDF index building, hash backend internals,
persistence roundtrips, HybridMemoryStore RRF fusion, ONNX-fallback paths,
and the embeddings module's no-network behavior (onnxruntime/torch/numpy
are NOT installed in this environment, so fallbacks must hold).
"""

from __future__ import annotations

import json
import math
import sys
import types

import pytest

from memory import embeddings as emb_mod
from memory.vector_store import HybridMemoryStore, VectorStore


def _ok(result) -> bool:
    return not bool(getattr(result, "error", False))


# ── VectorStore: backend selection ──────────────────────────────────────


class TestBackendSelection:
    def test_auto_falls_back_to_tfidf_without_onnx(self):
        vs = VectorStore(backend="auto")
        assert vs.backend == "tfidf"  # onnxruntime not installed here

    def test_explicit_backends_kept(self):
        assert VectorStore(backend="hash").backend == "hash"
        assert VectorStore(backend="tfidf").backend == "tfidf"

    def test_unknown_backend_falls_back_to_tfidf_embed(self):
        vs = VectorStore(backend="bogus")
        vec = vs._embed("hello world")
        assert len(vec) == vs.dim  # did not raise; tfidf fallback used

    def test_auto_selects_onnx_when_importable(self, monkeypatch):
        fake_ort = types.ModuleType("onnxruntime")
        monkeypatch.setitem(sys.modules, "onnxruntime", fake_ort)
        assert VectorStore(backend="auto").backend == "onnx"


# ── VectorStore: add / batch / remove / clear / len ─────────────────────


class TestStoreOps:
    def test_add_batch(self):
        vs = VectorStore(backend="tfidf")
        vs.add_batch([("d1", "cats are great", {"k": 1}), ("d2", "dogs are great", None)])
        assert len(vs) == 2
        assert vs._metadata["d1"] == {"k": 1}
        assert vs._metadata["d2"] == {}

    def test_remove_unknown(self):
        vs = VectorStore(backend="tfidf")
        assert vs.remove("nope") is False
        assert len(vs) == 0

    def test_clear(self):
        vs = VectorStore(backend="tfidf")
        vs.add("d1", "hello")
        vs.clear()
        assert len(vs) == 0
        assert vs.search("hello") == []

    def test_len_tracks_add_remove(self):
        vs = VectorStore(backend="hash")
        assert len(vs) == 0
        vs.add("a", "x")
        vs.add("b", "y")
        assert len(vs) == 2
        vs.remove("a")
        assert len(vs) == 1


# ── TF-IDF internals ───────────────────────────────────────────────────


class TestTfidf:
    def test_build_index_populates_vocab_and_idf(self):
        vs = VectorStore(backend="tfidf")
        vs.build_tfidf_index(
            {"d1": "the quick brown fox", "d2": "the lazy dog", "d3": "python code"}
        )
        assert "the" in vs._tfidf_vocab
        # 'the' appears in 2 of 3 docs -> lower idf than a unique term
        assert vs._tfidf_idf["the"] < vs._tfidf_idf["python"]
        # idf formula: log(n_docs / (1 + df))
        assert vs._tfidf_idf["the"] == pytest.approx(math.log(3 / 3))
        assert vs._tfidf_idf["python"] == pytest.approx(math.log(3 / 2))

    def test_embed_known_term_nonzero_unknown_term_zero(self):
        vs = VectorStore(backend="tfidf")
        vs.build_tfidf_index({"d1": "unique zebra word"})
        vec = vs._embed("zebra")
        assert any(v != 0.0 for v in vec)
        vec2 = vs._embed("qqqzzz-not-in-vocab")
        assert all(v == 0.0 for v in vec2)

    def test_embed_is_l2_normalized(self):
        vs = VectorStore(backend="tfidf")
        vs.build_tfidf_index({"d1": "alpha beta gamma delta"})
        vec = vs._embed("alpha beta")
        norm = math.sqrt(sum(v * v for v in vec))
        assert norm == pytest.approx(1.0)

    def test_embed_empty_text_no_crash(self):
        vs = VectorStore(backend="tfidf")
        vec = vs._embed("")
        assert len(vec) == vs.dim
        assert all(v == 0.0 for v in vec)

    def test_embed_respects_dim(self):
        vs = VectorStore(backend="tfidf", dim=64)
        assert len(vs._embed("some text here")) == 64

    def test_tokenize(self):
        assert VectorStore._tokenize("a bb ccc D_D") == ["bb", "ccc", "d_d"]
        assert VectorStore._tokenize("Hello, WORLD!") == ["hello", "world"]


# ── Hash backend ───────────────────────────────────────────────────────


class TestHashBackend:
    def test_deterministic(self):
        vs = VectorStore(backend="hash")
        assert vs._hash_embed("same text") == vs._hash_embed("same text")

    def test_normalized_and_dim(self):
        vs = VectorStore(backend="hash", dim=64)
        vec = vs._hash_embed("some words here")
        assert len(vec) == 64
        assert math.sqrt(sum(v * v for v in vec)) == pytest.approx(1.0)

    def test_empty_text_zero_vector(self):
        vs = VectorStore(backend="hash")
        assert all(v == 0.0 for v in vs._hash_embed(""))


# ── search() semantics ─────────────────────────────────────────────────


class TestSearch:
    def test_threshold_filters(self):
        vs = VectorStore(backend="tfidf")
        # 3-doc corpus so the query term gets a nonzero idf
        # (idf = log(n_docs / (1 + df)); with 2 docs a unique term gets 0).
        vs.build_tfidf_index({"d1": "zebra stripes", "d2": "python snake", "d3": "lion king"})
        vs.add("d1", "zebra stripes")
        vs.add("d2", "python snake")
        vs.add("d3", "lion king")
        all_res = vs.search("zebra", top_k=5, threshold=0.0)
        assert len(all_res) == 3  # threshold 0 keeps zero-sim docs
        strict = vs.search("zebra", top_k=5, threshold=0.5)
        assert [r[0] for r in strict] == ["d1"]

    def test_top_k_and_sort_order(self):
        vs = VectorStore(backend="tfidf")
        vs.build_tfidf_index({"d1": "cat cat cat", "d2": "cat", "d3": "unrelated words here"})
        vs.add("d1", "cat cat cat")
        vs.add("d2", "cat")
        vs.add("d3", "unrelated words here")
        res = vs.search("cat", top_k=2)
        assert len(res) == 2
        scores = [r[1] for r in res]
        assert scores == sorted(scores, reverse=True)
        assert res[0][0] == "d1"  # more term overlap wins

    def test_metadata_returned(self):
        vs = VectorStore(backend="tfidf")
        vs.add("d1", "hello world test", {"source": "unit"})
        res = vs.search("hello", top_k=1)
        assert res[0][2] == {"source": "unit"}

    def test_cosine_mismatched_lengths(self):
        assert VectorStore._cosine_similarity([1.0, 2.0], [1.0]) == 0.0

    def test_cosine_zero_vector(self):
        assert VectorStore._cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0


# ── persistence ────────────────────────────────────────────────────────


class TestPersistence:
    def test_save_load_roundtrip(self, tmp_path):
        vs = VectorStore(backend="tfidf")
        vs.build_tfidf_index({"d1": "roundtrip zebra"})
        vs.add("d1", "roundtrip zebra", {"m": 1})
        p = tmp_path / "vs.json"
        vs.save(p)
        raw = json.loads(p.read_text())
        assert raw["backend"] == "tfidf"
        assert raw["dim"] == 384
        assert "d1" in raw["vectors"]

        vs2 = VectorStore(backend="hash")
        vs2.load(p)
        assert vs2.backend == "tfidf"
        assert len(vs2) == 1
        assert vs2._metadata["d1"] == {"m": 1}
        assert "zebra" in vs2._tfidf_vocab
        res = vs2.search("zebra", top_k=1)
        assert res[0][0] == "d1"

    def test_load_missing_path_is_noop(self, tmp_path):
        vs = VectorStore(backend="tfidf")
        vs.load(tmp_path / "does-not-exist.json")  # must not raise
        assert len(vs) == 0

    def test_doc_count_restored(self, tmp_path):
        vs = VectorStore(backend="tfidf")
        vs.add("a", "one two")
        vs.add("b", "three four")
        p = tmp_path / "vs.json"
        vs.save(p)
        vs2 = VectorStore(backend="tfidf")
        vs2.load(p)
        assert len(vs2) == 2


# ── ONNX backend fallback ──────────────────────────────────────────────


class TestOnnxFallback:
    def test_onnx_embed_falls_back_when_unavailable(self, monkeypatch):
        monkeypatch.setattr(emb_mod, "is_available", lambda: False)
        vs = VectorStore(backend="onnx")
        vec = vs._onnx_embed("hello world")
        # falls back to tfidf: zero vector (no vocab built), correct dim
        assert len(vec) == vs.dim

    def test_onnx_embed_uses_pipeline_when_available(self, monkeypatch):
        monkeypatch.setattr(emb_mod, "is_available", lambda: True)
        monkeypatch.setattr(emb_mod, "encode", lambda texts, batch_size=64: [[0.5] * 512])
        vs = VectorStore(backend="onnx", dim=384)
        vec = vs._onnx_embed("hi")
        assert vec == [0.5] * 384  # truncated to dim

    def test_onnx_embed_empty_result_falls_back(self, monkeypatch):
        monkeypatch.setattr(emb_mod, "is_available", lambda: True)
        monkeypatch.setattr(emb_mod, "encode", lambda texts, batch_size=64: [])
        vs = VectorStore(backend="onnx")
        vec = vs._onnx_embed("hi")
        assert len(vec) == vs.dim


# ── HybridMemoryStore ──────────────────────────────────────────────────


class TestHybridMemoryStore:
    def test_rrf_prefers_doc_in_both_lists(self):
        hs = HybridMemoryStore(VectorStore(backend="tfidf"))
        hs.add("d1", "zebra stripes animal")
        hs.add("d2", "completely different topic words")
        hs.add("d3", "zebra")
        res = hs.search("zebra", top_k=3)
        ids = [r[0] for r in res]
        # Vector search with threshold=0.0 lists every doc, so the RRF union
        # contains all three; d1 (present in BOTH vector and BM25 lists)
        # must outrank d2 (vector list only).
        assert set(ids) == {"d1", "d2", "d3"}
        assert ids[0] == "d1"
        assert ids.index("d2") > ids.index("d3")

    def test_bm25_only_match_still_returned(self):
        hs = HybridMemoryStore(VectorStore(backend="hash"))
        hs.add("d1", "xylophone music instrument")
        res = hs.search("xylophone", top_k=1)
        assert res[0][0] == "d1"

    def test_metadata_flows_through(self):
        hs = HybridMemoryStore(VectorStore(backend="tfidf"))
        hs.add("d1", "unique content words", {"tag": "t"})
        res = hs.search("unique content", top_k=1)
        assert res[0][2] == {"tag": "t"}

    def test_bm25_search_overlap_scoring(self):
        hs = HybridMemoryStore(VectorStore(backend="tfidf"))
        hs.add("d1", "apple banana cherry")
        hs.add("d2", "apple")
        got = hs._bm25_search("apple banana", top_k=5)
        assert got[0][0] == "d1"  # 2/2 overlap beats 1/2
        assert got[0][1] == pytest.approx(1.0)
        assert hs._bm25_search("qqqzzz", top_k=5) == []

    def test_empty_store_search(self):
        hs = HybridMemoryStore(VectorStore(backend="tfidf"))
        assert hs.search("anything") == []

    def test_default_vector_store_created(self):
        hs = HybridMemoryStore()
        assert isinstance(hs.vector_store, VectorStore)


# ══════════════════════ memory/embeddings.py ═══════════════════════════


@pytest.fixture
def fresh_emb_state(monkeypatch):
    """Reset the module-level availability cache around each test."""
    monkeypatch.setattr(emb_mod, "_onnx_available", None)
    monkeypatch.setattr(emb_mod, "_session", None)
    monkeypatch.setattr(emb_mod, "_tokenizer", None)
    yield
    monkeypatch.setattr(emb_mod, "_onnx_available", None)
    monkeypatch.setattr(emb_mod, "_session", None)
    monkeypatch.setattr(emb_mod, "_tokenizer", None)


class TestIsAvailable:
    def test_unavailable_without_onnxruntime(self, fresh_emb_state):
        assert "onnxruntime" not in sys.modules
        assert emb_mod.is_available() is False

    def test_result_cached(self, fresh_emb_state):
        assert emb_mod.is_available() is False
        assert emb_mod._onnx_available is False
        # Cache branch: once populated, the cached value is returned directly.
        emb_mod._onnx_available = True  # simulate a cached positive
        assert emb_mod.is_available() is True

    def test_download_not_attempted_when_import_fails(self, fresh_emb_state, monkeypatch):
        called = []
        monkeypatch.setattr(emb_mod, "_download_model", lambda: called.append(1))
        assert emb_mod.is_available() is False
        assert called == []  # import of onnxruntime fails before any download


class TestEncode:
    def test_returns_empty_when_unavailable_no_crash(self, fresh_emb_state):
        # Regression: encode() imported numpy/torch BEFORE the availability
        # check and raised ModuleNotFoundError instead of returning [].
        assert emb_mod.encode(["hello world"]) == []

    def test_empty_input(self, fresh_emb_state):
        assert emb_mod.encode([]) == []


class TestCosineSimilarity:
    def test_identical(self):
        assert emb_mod.cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_orthogonal(self):
        assert emb_mod.cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite(self):
        assert emb_mod.cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_empty_inputs(self):
        assert emb_mod.cosine_similarity([], [1.0]) == 0.0
        assert emb_mod.cosine_similarity([1.0], []) == 0.0
        assert emb_mod.cosine_similarity([], []) == 0.0

    def test_mismatched_lengths(self):
        assert emb_mod.cosine_similarity([1.0, 2.0], [1.0]) == 0.0

    def test_zero_vectors(self):
        assert emb_mod.cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0


# ── _mean_pool with a minimal pure-python tensor ───────────────────────


class _T:
    """Tiny stand-in for a torch tensor: just enough for _mean_pool."""

    def __init__(self, d):
        self.d = d

    def size(self):
        def shape(x):
            return (len(x), *shape(x[0])) if isinstance(x, list) else ()

        return shape(self.d)

    def unsqueeze(self, dim):
        assert dim == -1
        return _T([[[v] for v in row] for row in self.d])

    def expand(self, shape):
        b, s, dd = shape
        return _T([[[self.d[i][j][0]] * dd for j in range(s)] for i in range(b)])

    def float(self):
        return self

    def __mul__(self, o):
        return _T(
            [
                [
                    [a * b for a, b in zip(r1, r2, strict=False)]
                    for r1, r2 in zip(b1, b2, strict=False)
                ]
                for b1, b2 in zip(self.d, o.d, strict=False)
            ]
        )

    def sum(self, dim):
        assert dim == 1
        return _T([[sum(col) for col in zip(*batch, strict=False)] for batch in self.d])

    def clamp(self, min=None):
        return _T([[max(v, min) for v in row] for row in self.d])

    def __truediv__(self, o):
        return _T(
            [
                [a / b for a, b in zip(r1, r2, strict=False)]
                for r1, r2 in zip(self.d, o.d, strict=False)
            ]
        )


class TestMeanPool:
    def test_masked_mean(self):
        te = _T([[[1.0, 2.0], [3.0, 4.0], [9.0, 9.0]]])
        mask = _T([[1, 1, 0]])
        out = emb_mod._mean_pool(te, mask)
        assert out.d == [[2.0, 3.0]]  # masked token excluded from the mean

    def test_all_masked_avoids_div_zero(self):
        te = _T([[[5.0, 5.0]]])
        mask = _T([[0]])
        out = emb_mod._mean_pool(te, mask)
        # count clamped to 1e-9 -> huge but finite, no ZeroDivisionError
        assert math.isfinite(out.d[0][0])


class TestL2Normalize:
    def test_normalizes_rows(self, monkeypatch):
        import math as _m

        class _Linalg:
            @staticmethod
            def norm(vectors, axis=1, keepdims=True):
                return [[_m.sqrt(sum(x * x for x in row))] for row in vectors]

        fake_np = types.ModuleType("numpy")
        fake_np.linalg = _Linalg()
        fake_np.maximum = staticmethod(lambda a, b: [[max(v, b) for v in row] for row in a])
        fake_np.isscalar = staticmethod(lambda obj: False)  # pytest.approx probes this
        fake_np.ndarray = ()  # isinstance(x, ()) is always False

        class _Arr(list):
            def __truediv__(self, o):
                # broadcast a single-column divisor like real numpy would
                def div_row(r1, r2):
                    if len(r2) == 1:
                        r2 = r2 * len(r1)
                    return [a / b for a, b in zip(r1, r2, strict=False)]

                return _Arr([div_row(r1, r2) for r1, r2 in zip(self, o, strict=False)])

        monkeypatch.setitem(sys.modules, "numpy", fake_np)
        out = emb_mod._l2_normalize(_Arr([[3.0, 4.0]]))
        assert abs(out[0][0] - 0.6) < 1e-9
        assert abs(out[0][1] - 0.8) < 1e-9

    def test_zero_row_clamped(self, monkeypatch):
        import math as _m

        class _Linalg:
            @staticmethod
            def norm(vectors, axis=1, keepdims=True):
                return [[_m.sqrt(sum(x * x for x in row))] for row in vectors]

        fake_np = types.ModuleType("numpy")
        fake_np.linalg = _Linalg()
        fake_np.maximum = staticmethod(lambda a, b: [[max(v, b) for v in row] for row in a])
        fake_np.isscalar = staticmethod(lambda obj: False)  # pytest.approx probes this
        fake_np.ndarray = ()  # isinstance(x, ()) is always False

        class _Arr(list):
            def __truediv__(self, o):
                # broadcast a single-column divisor like real numpy would
                def div_row(r1, r2):
                    if len(r2) == 1:
                        r2 = r2 * len(r1)
                    return [a / b for a, b in zip(r1, r2, strict=False)]

                return _Arr([div_row(r1, r2) for r1, r2 in zip(self, o, strict=False)])

        monkeypatch.setitem(sys.modules, "numpy", fake_np)
        out = emb_mod._l2_normalize(_Arr([[0.0, 0.0]]))
        assert all(v == 0.0 for v in out[0])  # no NaN / ZeroDivisionError


# ── graph integration ────────────────────────────────────────────────


class _Entry:
    def __init__(self, content, tags=()):
        self.content = content
        self.tags = list(tags)
        self.embedding = None
        self.embedding_model = None


class _Graph:
    def __init__(self):
        self.memories = {}
        self._bm25_hits = []

    def search_text(self, query, limit=10):
        return self._bm25_hits[:limit]


class TestEmbedAllMemories:
    def test_noop_when_unavailable(self, fresh_emb_state):
        g = _Graph()
        g.memories["m1"] = _Entry("hello")
        assert emb_mod.embed_all_memories(g) == 0
        assert g.memories["m1"].embedding is None

    def test_embeds_unembedded_in_place(self, fresh_emb_state, monkeypatch):
        monkeypatch.setattr(emb_mod, "is_available", lambda: True)
        monkeypatch.setattr(
            emb_mod,
            "encode",
            lambda texts, batch_size=64: [[float(i)] * 4 for i in range(len(texts))],
        )
        g = _Graph()
        g.memories["m1"] = _Entry("first fact", tags=["t1"])
        g.memories["m2"] = _Entry("second fact")
        g.memories["m3"] = _Entry("already done")
        g.memories["m3"].embedding = [9.0] * 4  # must be skipped

        calls = []
        orig_encode = emb_mod.encode
        monkeypatch.setattr(
            emb_mod,
            "encode",
            lambda texts, batch_size=64: (calls.append(texts), orig_encode(texts))[1],
        )
        n = emb_mod.embed_all_memories(g)
        assert n == 2
        assert g.memories["m1"].embedding == [0.0] * 4
        assert g.memories["m2"].embedding == [1.0] * 4
        assert g.memories["m3"].embedding == [9.0] * 4  # untouched
        assert g.memories["m1"].embedding_model == emb_mod.MODEL_ID
        # search text includes tags
        assert calls and any("t1" in t for t in calls[0])

    def test_encode_failure_returns_zero(self, fresh_emb_state, monkeypatch):
        monkeypatch.setattr(emb_mod, "is_available", lambda: True)
        monkeypatch.setattr(emb_mod, "encode", lambda texts, batch_size=64: [])
        g = _Graph()
        g.memories["m1"] = _Entry("hello")
        assert emb_mod.embed_all_memories(g) == 0
        assert g.memories["m1"].embedding is None

    def test_nothing_unembedded(self, fresh_emb_state, monkeypatch):
        monkeypatch.setattr(emb_mod, "is_available", lambda: True)
        g = _Graph()
        assert emb_mod.embed_all_memories(g) == 0


class TestSearchEmbedding:
    def test_falls_back_to_bm25_when_unavailable(self, fresh_emb_state):
        g = _Graph()
        g._bm25_hits = ["hit1", "hit2"]
        assert emb_mod.search_embedding(g, "query") == ["hit1", "hit2"]

    def test_falls_back_when_session_missing(self, fresh_emb_state, monkeypatch):
        monkeypatch.setattr(emb_mod, "is_available", lambda: True)
        monkeypatch.setattr(emb_mod, "_session", None)
        g = _Graph()
        g._bm25_hits = ["bm25"]
        assert emb_mod.search_embedding(g, "query") == ["bm25"]

    def test_falls_back_when_encode_fails(self, fresh_emb_state, monkeypatch):
        monkeypatch.setattr(emb_mod, "is_available", lambda: True)
        monkeypatch.setattr(emb_mod, "_session", object())
        monkeypatch.setattr(emb_mod, "encode", lambda texts, batch_size=64: [])
        g = _Graph()
        g._bm25_hits = ["bm25"]
        assert emb_mod.search_embedding(g, "query") == ["bm25"]

    def test_semantic_ranking(self, fresh_emb_state, monkeypatch):
        monkeypatch.setattr(emb_mod, "is_available", lambda: True)
        monkeypatch.setattr(emb_mod, "_session", object())
        monkeypatch.setattr(emb_mod, "encode", lambda texts, batch_size=64: [[1.0, 0.0]])
        g = _Graph()
        e1, e2, e3 = _Entry("a"), _Entry("b"), _Entry("c")
        e1.embedding = [1.0, 0.0]  # identical -> sim 1.0
        e2.embedding = [0.0, 1.0]  # orthogonal -> sim 0.0, excluded (sim > 0 required)
        e3.embedding = None  # skipped
        g.memories = {"m1": e1, "m2": e2, "m3": e3}
        res = emb_mod.search_embedding(g, "query", limit=10)
        assert [(e.content, s) for e, s in res] == [("a", pytest.approx(1.0))]

    def test_limit_applied(self, fresh_emb_state, monkeypatch):
        monkeypatch.setattr(emb_mod, "is_available", lambda: True)
        monkeypatch.setattr(emb_mod, "_session", object())
        monkeypatch.setattr(emb_mod, "encode", lambda texts, batch_size=64: [[1.0, 1.0]])
        g = _Graph()
        for i in range(5):
            e = _Entry(f"doc{i}")
            e.embedding = [1.0, 1.0]
            g.memories[f"m{i}"] = e
        assert len(emb_mod.search_embedding(g, "q", limit=2)) == 2
