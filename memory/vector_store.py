"""
Vector store — pure-Python embedding + cosine similarity for semantic memory.

Replaces the ONNX dependency with a lightweight, zero-dependency approach:
  - TF-IDF vectors as fallback (no external model needed)
  - Optional: hash-based embeddings for fast approximate search
  - Optional: local ONNX if available (graceful degradation)

This gives semantic search without requiring model downloads.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any


class VectorStore:
    """Lightweight vector store with cosine similarity search.

    Supports three backends (auto-selected):
      1. Hash-based embeddings (fast, no deps, approximate)
      2. TF-IDF vectors (good quality, no deps)
      3. ONNX embeddings (best quality, requires model download)

    Usage:
        store = VectorStore()
        store.add("doc1", "The quick brown fox jumps over the lazy dog")
        store.add("doc2", "Python is a programming language")
        results = store.search("fox animal", top_k=2)
    """

    def __init__(self, dim: int = 384, backend: str = "auto"):
        self.dim = dim
        self.backend = self._select_backend(backend)
        self._vectors: dict[str, list[float]] = {}
        self._metadata: dict[str, dict[str, Any]] = {}
        self._tfidf_vocab: dict[str, int] = {}
        self._tfidf_idf: dict[str, float] = {}
        self._doc_count = 0

    def _select_backend(self, backend: str) -> str:
        """Select the best available backend."""
        if backend == "auto":
            # Try ONNX first, fall back to TF-IDF
            try:
                import onnxruntime  # noqa: F401

                return "onnx"
            except ImportError:
                return "tfidf"
        return backend

    def add(self, doc_id: str, text: str, metadata: dict[str, Any] | None = None):
        """Add a document to the vector store."""
        vector = self._embed(text)
        self._vectors[doc_id] = vector
        self._metadata[doc_id] = metadata or {}
        self._doc_count += 1

    def add_batch(self, docs: list[tuple[str, str, dict[str, Any] | None]]):
        """Add multiple documents at once."""
        for doc_id, text, meta in docs:
            self.add(doc_id, text, meta)

    def search(
        self, query: str, top_k: int = 5, threshold: float = 0.0
    ) -> list[tuple[str, float, dict[str, Any]]]:
        """Search for similar documents.

        Returns: [(doc_id, similarity_score, metadata), ...]
        """
        query_vec = self._embed(query)
        scores: list[tuple[str, float]] = []

        for doc_id, doc_vec in self._vectors.items():
            sim = self._cosine_similarity(query_vec, doc_vec)
            if sim >= threshold:
                scores.append((doc_id, sim))

        scores.sort(key=lambda x: x[1], reverse=True)
        results = []
        for doc_id, score in scores[:top_k]:
            results.append((doc_id, score, self._metadata.get(doc_id, {})))

        return results

    def remove(self, doc_id: str) -> bool:
        """Remove a document from the store."""
        if doc_id in self._vectors:
            del self._vectors[doc_id]
            self._metadata.pop(doc_id, None)
            self._doc_count -= 1
            return True
        return False

    def clear(self):
        """Remove all documents."""
        self._vectors.clear()
        self._metadata.clear()
        self._doc_count = 0

    def __len__(self) -> int:
        return self._doc_count

    def _embed(self, text: str) -> list[float]:
        """Embed text into a vector using the selected backend."""
        if self.backend == "tfidf":
            return self._tfidf_embed(text)
        elif self.backend == "hash":
            return self._hash_embed(text)
        elif self.backend == "onnx":
            return self._onnx_embed(text)
        else:
            return self._tfidf_embed(text)

    # ── TF-IDF Backend ────────────────────────────────────────────────

    def _tfidf_embed(self, text: str) -> list[float]:
        """Create a TF-IDF vector for the text."""
        tokens = self._tokenize(text)
        tf = Counter(tokens)
        total = len(tokens)

        # Build vector
        vec = [0.0] * self.dim

        for term, count in tf.items():
            if term in self._tfidf_vocab:
                idx = self._tfidf_vocab[term] % self.dim
                idf = self._tfidf_idf.get(term, 1.0)
                vec[idx] += (count / total) * idf

        # L2 normalize
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]

        return vec

    def build_tfidf_index(self, documents: dict[str, str]):
        """Build TF-IDF vocabulary and IDF from a corpus."""
        # Count document frequency
        df: Counter[str] = Counter()
        all_tokens: set[str] = set()

        for text in documents.values():
            tokens = set(self._tokenize(text))
            for token in tokens:
                df[token] += 1
            all_tokens.update(tokens)

        # Build vocabulary (top terms by document frequency)
        sorted_terms = sorted(df.items(), key=lambda x: x[1], reverse=True)
        self._tfidf_vocab = {term: idx for idx, (term, _) in enumerate(sorted_terms[:10000])}

        # Compute IDF
        n_docs = len(documents)
        self._tfidf_idf = {term: math.log(n_docs / (1 + freq)) for term, freq in df.items()}

    # ── Hash Backend ──────────────────────────────────────────────────

    def _hash_embed(self, text: str) -> list[float]:
        """Create a deterministic hash-based embedding.

        Uses SimHash-inspired approach: hash each token, accumulate bit vectors.
        Fast, no dependencies, but approximate.
        """
        tokens = self._tokenize(text)
        vec = [0.0] * self.dim

        for token in tokens:
            # Hash the token to get a pseudo-random vector
            h = hashlib.sha256(token.encode()).digest()
            # Use first `dim` bytes as vector components
            for i in range(min(self.dim, len(h) * 8)):
                byte_idx = i // 8
                bit_idx = i % 8
                if byte_idx < len(h):
                    bit = (h[byte_idx] >> bit_idx) & 1
                    vec[i] += 1.0 if bit else -1.0

        # L2 normalize
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]

        return vec

    # ── ONNX Backend (optional) ───────────────────────────────────────

    def _onnx_embed(self, text: str) -> list[float]:
        """Create embeddings using ONNX model (if available)."""
        try:
            from . import embeddings as _onnx_embeddings

            if _onnx_embeddings.is_available():
                # Use the existing ONNX pipeline
                result = _onnx_embeddings.encode([text])
                if result and len(result) > 0:
                    return result[0][: self.dim]
        except Exception:
            pass
        # Fallback to TF-IDF
        return self._tfidf_embed(text)

    # ── Utilities ─────────────────────────────────────────────────────

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Tokenize text into lowercase words."""
        return re.findall(r"[a-zA-Z0-9_]{2,}", text.lower())

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def save(self, path: Path):
        """Save the vector store to disk."""
        import json

        data = {
            "backend": self.backend,
            "dim": self.dim,
            "vectors": {k: v for k, v in self._vectors.items()},
            "metadata": self._metadata,
            "tfidf_vocab": self._tfidf_vocab,
            "tfidf_idf": self._tfidf_idf,
        }
        path.write_text(json.dumps(data), encoding="utf-8")

    def load(self, path: Path):
        """Load the vector store from disk."""
        import json

        if not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        self.backend = data.get("backend", "tfidf")
        self.dim = data.get("dim", 384)
        self._vectors = data.get("vectors", {})
        self._metadata = data.get("metadata", {})
        self._tfidf_vocab = data.get("tfidf_vocab", {})
        self._tfidf_idf = data.get("tfidf_idf", {})
        self._doc_count = len(self._vectors)


# ── Hybrid Memory Store ───────────────────────────────────────────────


class HybridMemoryStore:
    """Combines BM25 (keyword) + Vector (semantic) search for best results.

    Merges results using Reciprocal Rank Fusion (RRF).
    """

    def __init__(self, vector_store: VectorStore | None = None):
        self.vector_store = vector_store or VectorStore()
        self._bm25_corpus: dict[str, str] = {}

    def add(self, doc_id: str, text: str, metadata: dict[str, Any] | None = None):
        """Add a document to both indexes."""
        self._bm25_corpus[doc_id] = text
        self.vector_store.add(doc_id, text, metadata)

    def search(self, query: str, top_k: int = 5) -> list[tuple[str, float, dict[str, Any]]]:
        """Hybrid search combining BM25 + vector similarity."""
        # Get vector results
        vec_results = self.vector_store.search(query, top_k=top_k * 2)
        vec_ranks = {doc_id: rank for rank, (doc_id, _, _) in enumerate(vec_results)}

        # Get BM25 results
        bm25_results = self._bm25_search(query, top_k=top_k * 2)
        bm25_ranks = {doc_id: rank for rank, (doc_id, _) in enumerate(bm25_results)}

        # Reciprocal Rank Fusion
        all_docs = set(vec_ranks.keys()) | set(bm25_ranks.keys())
        rrf_scores: dict[str, float] = {}

        k = 60  # RRF constant
        for doc_id in all_docs:
            score = 0.0
            if doc_id in vec_ranks:
                score += 1.0 / (k + vec_ranks[doc_id] + 1)
            if doc_id in bm25_ranks:
                score += 1.0 / (k + bm25_ranks[doc_id] + 1)
            rrf_scores[doc_id] = score

        # Sort by RRF score
        sorted_docs = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)

        results = []
        for doc_id, score in sorted_docs[:top_k]:
            meta = self.vector_store._metadata.get(doc_id, {})
            results.append((doc_id, score, meta))

        return results

    def _bm25_search(self, query: str, top_k: int = 5) -> list[tuple[str, float]]:
        """Simple BM25 search over the corpus."""
        query_terms = set(self.vector_store._tokenize(query))
        scores: list[tuple[str, float]] = []

        for doc_id, text in self._bm25_corpus.items():
            doc_terms = self.vector_store._tokenize(text)
            doc_term_set = set(doc_terms)

            # Simple overlap score (real BM25 would use TF-IDF)
            overlap = len(query_terms & doc_term_set)
            if overlap > 0:
                score = overlap / len(query_terms)
                scores.append((doc_id, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]
