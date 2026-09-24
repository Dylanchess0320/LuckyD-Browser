"""
Persistent memory store with BM25 text search and file-based persistence.
Stores graph metadata alongside the in-memory graph for durability.

Supports optional ONNX embedding for semantic search (see embeddings.py).
"""

import contextlib
import math
import re
from collections import Counter
from pathlib import Path

from .graph import MemoryGraph

# Optional ONNX embedding support
try:
    from . import embeddings as _embeddings

    _HAS_EMBEDDINGS = _embeddings.is_available()
except Exception:
    _embeddings = None
    _HAS_EMBEDDINGS = False


class BM25Scorer:
    """Simple BM25-like text scorer for memory retrieval."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_lengths: dict[str, int] = {}
        self.total_docs = 0
        self.avg_doc_len = 0.0
        self.inverted_index: dict[str, dict[str, int]] = {}  # term -> {doc_id: tf}
        self.idf_cache: dict[str, float] = {}

    def _tokenize(self, text: str) -> list[str]:
        return re.findall(r"[a-zA-Z0-9_]{2,}", text.lower())

    def index_documents(self, docs: dict[str, str]):
        self.total_docs = len(docs)
        lengths = []
        for doc_id, text in docs.items():
            tokens = self._tokenize(text)
            self.doc_lengths[doc_id] = len(tokens)
            lengths.append(len(tokens))
            tf = Counter(tokens)
            for term, count in tf.items():
                if term not in self.inverted_index:
                    self.inverted_index[term] = {}
                self.inverted_index[term][doc_id] = count
        self.avg_doc_len = sum(lengths) / max(len(lengths), 1)
        self.idf_cache.clear()

    def _idf(self, term: str) -> float:
        if term in self.idf_cache:
            return self.idf_cache[term]
        df = len(self.inverted_index.get(term, {}))
        idf = math.log((self.total_docs - df + 0.5) / (df + 0.5) + 1.0)
        self.idf_cache[term] = idf
        return idf

    def score(self, query: str, doc_id: str) -> float:
        if doc_id not in self.doc_lengths:
            return 0.0
        query_terms = self._tokenize(query)
        score = 0.0
        doc_len = self.doc_lengths[doc_id]
        for term in query_terms:
            idf = self._idf(term)
            tf = self.inverted_index.get(term, {}).get(doc_id, 0)
            numerator = tf * (self.k1 + 1)
            denominator = tf + self.k1 * (1 - self.b + self.b * doc_len / max(self.avg_doc_len, 1))
            score += idf * numerator / max(denominator, 0.001)
        return score


class MemoryStore:
    """Persistent memory with BM25 text search layered on top of MemoryGraph."""

    def __init__(self, store_dir: Path):
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.graph = MemoryGraph.load(self._graph_path())
        self.bm25 = BM25Scorer()
        self._rebuild_index()

        # Auto-embed any unembedded memories on startup
        if _HAS_EMBEDDINGS and _embeddings:
            try:
                n = _embeddings.embed_all_memories(self.graph)
                if n > 0:
                    self._save()
                    print(f"  [EMBED] Created {n} new embeddings (all-MiniLM-L6-v2)")
            except Exception:
                pass

    def _graph_path(self) -> Path:
        return self.store_dir / "graph.json"

    def _rebuild_index(self):
        docs = {}
        for mem_id, node in self.graph.memories.items():
            text = f"{node.content} {' '.join(node.tags)}"
            docs[mem_id] = text
        # Always rebuild from scratch: when the last memory is deleted the
        # index must be cleared too, otherwise search_text iterates stale
        # doc ids and raises KeyError.
        self.bm25 = BM25Scorer()
        self.bm25.index_documents(docs)

    def add(
        self,
        content: str,
        tags: list[str] | None = None,
        alias: str = "",
        source: str = "",
        expires_in_hours: float = 0,
    ) -> str:
        entry = self.graph.add_memory_raw(content, tags=tags or [], source=source)
        # alias/expires were silently dropped before (get_context reads
        # metadata["alias"]); persist them on the entry metadata.
        if alias:
            entry.metadata["alias"] = alias
        if expires_in_hours:
            from datetime import datetime, timedelta, timezone

            entry.metadata["expires_at"] = (
                datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)
            ).isoformat()
        self._rebuild_index()
        self._save()

        # Auto-embed single new entry
        if _HAS_EMBEDDINGS and _embeddings:
            with contextlib.suppress(Exception):
                _embeddings.embed_all_memories(self.graph)

        return entry.id

    def get(self, mem_id: str):
        return self.graph.memories.get(mem_id)

    def delete(self, mem_id: str) -> bool:
        ok = self.graph.remove_memory(mem_id)
        if ok:
            self._rebuild_index()
            self._save()
        return ok is not None

    def search_text(self, query: str, limit: int = 10) -> list[tuple]:
        """BM25 text search."""
        scores = []
        for doc_id in self.bm25.doc_lengths:
            s = self.bm25.score(query, doc_id)
            if s > 0:
                scores.append((doc_id, s))
        scores.sort(key=lambda x: x[1], reverse=True)
        return [(self.graph.memories[mid], s) for mid, s in scores[:limit]]

    def search_similar(self, content: str, limit: int = 10) -> list[tuple]:
        """Semantic similarity search — uses ONNX embeddings when available."""
        # Try ONNX embedding similarity first
        if _HAS_EMBEDDINGS and _embeddings:
            try:
                results = _embeddings.search_embedding(self.graph, content, limit)
                if results:
                    return results
            except Exception:
                pass  # Fall through to BM25

        # Fallback: BM25 text similarity
        return self.graph.search_text(content, limit)

    def search_hybrid(self, query: str, limit: int = 10, rrf_k: int = 60) -> list[tuple]:
        """Reciprocal-rank-fused BM25 + semantic search.

        Either ranking alone misses: BM25 needs exact terms, semantic
        needs embeddings installed (else it falls back to BM25 itself).
        RRF fuses both orderings so a hit ranked highly anywhere
        surfaces, with agreement across both ranking highest.
        """
        seen: dict = {}
        fused: dict = {}
        for hits in (
            self.search_text(query, limit=limit * 2),
            self.search_similar(query, limit=limit * 2),
        ):
            for rank, (node, _score) in enumerate(hits):
                seen.setdefault(node.id, node)
                fused[node.id] = fused.get(node.id, 0.0) + 1.0 / (rrf_k + rank + 1)
        ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
        return [(seen[mid], score) for mid, score in ranked[:limit]]

    def search_by_tag(self, tag: str) -> list[tuple]:
        return self.graph.search_by_tag(tag)

    def link(self, mem_id_a: str, mem_id_b: str, edge_type: str = "related"):
        ok = self.graph.add_edge(mem_id_a, mem_id_b, edge_type)
        if ok:
            self._save()
        return ok

    def bfs_from(self, mem_id: str, depth: int = 2) -> list[tuple]:
        return self.graph.bfs(mem_id, depth)

    def get_context(self, query: str, limit: int = 5) -> str:
        """Return top memories formatted as context for the LLM."""
        results = self.search_hybrid(query, limit=limit)
        lines = []
        for i, (node, score) in enumerate(results):
            tags = ", ".join(node.tags) if node.tags else "none"
            alias = f" ({node.metadata.get('alias')})" if node.metadata.get("alias") else ""
            lines.append(f"  [{i}] {node.content}{alias}  (tags: {tags}, score: {score:.3f})")
        return "\n".join(lines) if lines else "  (no relevant memories)"

    def summarize(self) -> str:
        return self.graph.summarize()

    def clear(self):
        self.graph = MemoryGraph()
        self.bm25 = BM25Scorer()
        self._graph_path().unlink(missing_ok=True)

    def _save(self):
        self.graph.save(self._graph_path())

    @classmethod
    def load(cls, store_dir: Path | None = None):
        from config import MEMORY_DIR

        dir_path = store_dir or MEMORY_DIR
        store = cls(dir_path)
        return store


# Singleton
_store: MemoryStore | None = None


def get_memory() -> MemoryStore:
    global _store
    if _store is None:
        _store = MemoryStore.load()
    return _store
