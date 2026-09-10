"""
Optional ONNX embedding backend for semantic memory search.

Uses the all-MiniLM-L6-v2 ONNX model (~80 MB download) via onnxruntime.
Falls back gracefully if ONNX is not available.

Architecture:
  1. On init, check if ONNX + model files are available
  2. Gather all MemoryEntry nodes that lack embeddings
  3. Batch-encode new entries and update in-place
  4. Cosine-similarity search over stored embeddings
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from onnxruntime import InferenceSession
    from transformers import AutoTokenizer


# ── Constants ──────────────────────────────────────────────────────────

MODEL_ID = "all-MiniLM-L6-v2"
MODEL_FILENAME = "all-MiniLM-L6-v2.onnx"
MODEL_URL = (
    "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/onnx/model.onnx"
)
EXPECTED_HASH = None  # Optional: verify with hashlib.sha256
DIM = 384  # Embedding vector length for this model
NORMALIZE = True  # L2 normalize after encoding
MAX_BATCH = 64  # Encode up to this many entries per batch

# Where the ONNX model file lives
from config import MEMORY_DIR

MODEL_DIR = Path(MEMORY_DIR) / "embedding_model"
MODEL_PATH = MODEL_DIR / MODEL_FILENAME


# ── Availability check ─────────────────────────────────────────────────

_onnx_available: bool | None = None
_session: InferenceSession | None = None  # type: ignore[name-defined]
_tokenizer: AutoTokenizer | None = None  # type: ignore[name-defined]


def is_available() -> bool:
    """Check if ONNX runtime + tokenizer + model are available."""
    global _onnx_available, _session, _tokenizer
    if _onnx_available is not None:
        return _onnx_available

    try:
        import onnxruntime as ort
        from transformers import AutoTokenizer

        # Lazy-load model (download if missing)
        if not MODEL_PATH.exists():
            _download_model()

        _session = ort.InferenceSession(str(MODEL_PATH))
        model_ref = f"sentence-transformers/{MODEL_ID}"  # trusted constant
        _tokenizer = AutoTokenizer.from_pretrained(model_ref)  # nosec B615
        _onnx_available = True
        return True
    except Exception:
        _onnx_available = False
        return False


def _download_model() -> None:
    """Download the ONNX model file from HuggingFace."""
    import urllib.request

    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    print(f"  [EMBED] Downloading ONNX model '{MODEL_ID}' (~80 MB)...")
    urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)  # nosec B310
    print(f"  [EMBED] Model saved to {MODEL_PATH}")


# ── Token pooling ──────────────────────────────────────────────────────


def _mean_pool(token_embeddings, attention_mask):
    """Average pool token embeddings weighted by attention mask."""
    # Expand mask to same shape as embeddings [batch, seq, dim]
    mask = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
    summed = (token_embeddings * mask).sum(dim=1)
    count = mask.sum(dim=1).clamp(min=1e-9)
    return summed / count


def _l2_normalize(vectors):
    """L2-normalize each row in-place."""
    import numpy as np

    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-12)
    return vectors / norms


# ── Batch encoding ─────────────────────────────────────────────────────


def encode(texts: list[str], batch_size: int = MAX_BATCH) -> list[list[float]]:
    """Encode a list of texts into embedding vectors. Returns list of float lists.

    Returns empty list if ONNX is unavailable.
    """
    import numpy as np
    import torch

    if not is_available():
        return []

    all_embeddings: list[np.ndarray] = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        tokens = _tokenizer(
            list(batch),
            padding=True,
            truncation=True,
            max_length=256,
            return_tensors="pt",
        )

        # Run ONNX model
        inputs = {
            "input_ids": tokens["input_ids"].numpy(),
            "attention_mask": tokens["attention_mask"].numpy(),
            "token_type_ids": tokens.get(
                "token_type_ids", np.zeros_like(tokens["input_ids"].numpy())
            ),
        }
        outputs = _session.run(None, inputs)  # Usually outputs[0] is the tensor

        # Mean pooling
        token_embeds = torch.from_numpy(outputs[0])
        attention = tokens["attention_mask"]
        pooled = _mean_pool(token_embeds, attention)

        pooled_np = _l2_normalize(pooled.numpy()) if NORMALIZE else pooled.numpy()

        all_embeddings.append(pooled_np)

    if not all_embeddings:
        return []

    final = np.concatenate(all_embeddings, axis=0)
    return [row.tolist() for row in final]


# ── Cosine similarity ──────────────────────────────────────────────────


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Cosine similarity between two float lists."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=False))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a < 1e-12 or norm_b < 1e-12:
        return 0.0
    return dot / (norm_a * norm_b)


# ── Memory graph integration ───────────────────────────────────────────


def embed_all_memories(graph) -> int:
    """Find all unembedded memories, batch-encode them, and update in-place.
    Returns the number of embeddings created.

    The graph parameter should be a MemoryGraph instance.
    """
    if not is_available():
        return 0

    # Collect entries without embeddings
    unembedded: list[tuple[str, str]] = []  # (mem_id, content)
    for mem_id, entry in graph.memories.items():
        if entry.embedding is None:
            # Use search_text which includes tags
            text = f"{entry.content} {' '.join(entry.tags)}"
            unembedded.append((mem_id, text))

    if not unembedded:
        return 0

    ids, texts = zip(*unembedded, strict=False)
    embeddings = encode(list(texts))

    if not embeddings:
        return 0

    count = 0
    for mem_id, emb in zip(ids, embeddings, strict=False):
        entry = graph.memories.get(mem_id)
        if entry is not None:
            entry.embedding = emb
            entry.embedding_model = MODEL_ID  # type: ignore[assignment]
            count += 1

    return count


def search_embedding(graph, query: str, limit: int = 10) -> list[tuple]:
    """Search memories by embedding cosine similarity. Falls back to BM25.

    Returns list of (MemoryEntry, score) tuples.
    """
    if not is_available() or not _session:
        return graph.search_text(query, limit)

    # Encode the query
    query_emb = encode([query])
    if not query_emb:
        return graph.search_text(query, limit)
    query_vec = query_emb[0]

    scores: list[tuple[str, float]] = []
    for mem_id, entry in graph.memories.items():
        if entry.embedding is None:
            continue
        sim = cosine_similarity(query_vec, entry.embedding)
        if sim > 0:
            scores.append((mem_id, sim))

    scores.sort(key=lambda x: x[1], reverse=True)
    return [(graph.memories[mid], score) for mid, score in scores[:limit]]
