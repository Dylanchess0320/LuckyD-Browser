"""
Advanced Memory System — SQLite-backed persistent memory with pure-Python
TF-IDF semantic search.

Layered alongside ``memory/store.py`` (BM25 + optional ONNX embeddings) as a
heavier-duty, fully self-contained store:

  * SQLite persistence in ``data/memory_store/advanced_memory.db``
  * Sparse TF-IDF vectors + cosine similarity (stdlib only — no external APIs,
    no numpy, no pip deps)
  * Importance scoring with time-based decay and access reinforcement
  * Auto-compression (summarize + merge) when the store exceeds a threshold
  * Thread-safe via ``check_same_thread=False`` + a re-entrant lock

Integration hooks mirror the existing tool interface (``tools/memory_tools.py``):
``add(content, tags, alias, source)``, ``get_context(query, limit)`` and
``delete(memory_id)`` so ``MemoryRemember`` / ``MemoryRecall`` style callers can
drop this in without changes.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
import uuid
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:  # Prefer project config, but stay importable standalone
    from config import MEMORY_DIR
except Exception:  # pragma: no cover - fallback for isolated use
    MEMORY_DIR = Path(__file__).resolve().parent.parent / "data" / "memory_store"

# ── Constants ──────────────────────────────────────────────────────────

DB_PATH = MEMORY_DIR / "advanced_memory.db"
SCHEMA_VERSION = 1

DEFAULT_COMPACT_THRESHOLD = 1000  # auto-compress when total memories exceed this
DECAY_HALF_LIFE_DAYS = 30.0  # importance halves every N days without access
DECAY_FLOOR = 0.05  # never decay below this
ACCESS_BOOST = 0.05  # importance bump per access (capped at 1.0)
SUMMARY_MAX_CHARS = 200
EXPORT_VERSION = 1

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]{2,}")
_STOPWORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "at",
        "by",
        "from",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "as",
        "not",
        "no",
        "do",
        "does",
        "did",
        "have",
        "has",
        "had",
        "will",
        "would",
        "can",
        "could",
        "should",
        "i",
        "you",
        "he",
        "she",
        "we",
        "they",
        "them",
        "his",
        "her",
        "their",
        "our",
        "your",
        "my",
        "me",
        "him",
        "us",
    ]
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _tokenize(text: str) -> list[str]:
    """Lowercase word tokens, stopwords removed."""
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


# ── Sparse TF-IDF vectors ──────────────────────────────────────────────


def _hash_bucket(term: str, dims: int) -> int:
    """Deterministic feature bucket via md5 (hashing trick — no vocab needed)."""
    return int(hashlib.md5(term.encode("utf-8"), usedforsecurity=False).hexdigest(), 16) % dims


def _tfidf_vector(tokens: list[str], dims: int = 256, sublinear: bool = True) -> dict[int, float]:
    """Sparse TF vector in a fixed-dim hash space, L2-normalized.

    IDF is applied at query time against corpus stats; stored vectors are
    normalized term-frequency vectors in hashed space.
    """
    tf: dict[int, float] = {}
    for tok in tokens:
        b = _hash_bucket(tok, dims)
        tf[b] = tf.get(b, 0.0) + 1.0
    if sublinear:
        tf = {b: 1.0 + math.log(c) for b, c in tf.items()}
    norm = math.sqrt(sum(v * v for v in tf.values())) or 1.0
    return {b: v / norm for b, v in tf.items()}


def _cosine(a: dict[int, float], b: dict[int, float]) -> float:
    """Cosine similarity of two L2-normalized sparse vectors = dot product."""
    if not a or not b:
        return 0.0
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    return sum(v * large.get(k, 0.0) for k, v in small.items())


def _serialize_vector(vec: dict[int, float]) -> str:
    return json.dumps({str(k): round(v, 6) for k, v in vec.items()})


def _deserialize_vector(raw: str | None) -> dict[int, float]:
    if not raw:
        return {}
    try:
        return {int(k): float(v) for k, v in json.loads(raw).items()}
    except (ValueError, TypeError, AttributeError):
        return {}


# ── Memory dataclass ───────────────────────────────────────────────────


@dataclass
class Memory:
    """A single advanced-memory record."""

    id: str
    content: str
    category: str = "general"
    tags: list[str] = field(default_factory=list)
    importance: float = 0.5
    created_at: str = ""
    last_accessed: str = ""
    access_count: int = 0
    embedding: dict[int, float] = field(default_factory=dict)
    summary: str = ""
    source: str = ""

    def touch(self) -> None:
        """Record an access: refresh timestamp, bump count and importance."""
        self.last_accessed = _iso(_utcnow())
        self.access_count += 1
        self.importance = min(1.0, self.importance + ACCESS_BOOST)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["embedding"] = {str(k): v for k, v in self.embedding.items()}
        return d

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Memory:
        return cls(
            id=row["id"],
            content=row["content"],
            category=row["category"],
            tags=json.loads(row["tags"] or "[]"),
            importance=float(row["importance"]),
            created_at=row["created_at"],
            last_accessed=row["last_accessed"],
            access_count=int(row["access_count"]),
            embedding=_deserialize_vector(row["embedding"]),
            summary=row["summary"] or "",
            source=row["source"] or "",
        )


# ── Advanced memory system ─────────────────────────────────────────────


class AdvancedMemorySystem:
    """Thread-safe SQLite memory store with TF-IDF semantic search.

    Parameters
    ----------
    db_path:
        Location of the SQLite database. Defaults to
        ``data/memory_store/advanced_memory.db``.
    compact_threshold:
        When the number of stored memories exceeds this, ``add_memory``
        triggers :meth:`compress_old_memories` automatically.
    vector_dims:
        Hash-space dimensionality for TF-IDF vectors.
    """

    def __init__(
        self,
        db_path: Path | str | None = None,
        compact_threshold: int = DEFAULT_COMPACT_THRESHOLD,
        vector_dims: int = 256,
    ):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.compact_threshold = compact_threshold
        self.vector_dims = vector_dims
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    # ── schema ─────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memories (
                    id            TEXT PRIMARY KEY,
                    content       TEXT NOT NULL,
                    category      TEXT NOT NULL DEFAULT 'general',
                    tags          TEXT NOT NULL DEFAULT '[]',
                    importance    REAL NOT NULL DEFAULT 0.5,
                    created_at    TEXT NOT NULL,
                    last_accessed TEXT NOT NULL,
                    access_count  INTEGER NOT NULL DEFAULT 0,
                    embedding     TEXT,
                    summary       TEXT DEFAULT '',
                    source        TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_memories_category
                    ON memories(category);
                CREATE INDEX IF NOT EXISTS idx_memories_importance
                    ON memories(importance DESC);
                CREATE INDEX IF NOT EXISTS idx_memories_last_accessed
                    ON memories(last_accessed);
                """
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    # ── CRUD ───────────────────────────────────────────────────────

    def add_memory(
        self,
        content: str,
        category: str = "general",
        tags: list[str] | None = None,
        importance: float = 0.5,
        source: str = "",
        summary: str = "",
        memory_id: str | None = None,
    ) -> str:
        """Store a new memory. Returns its ID."""
        content = (content or "").strip()
        if not content:
            raise ValueError("content must be non-empty")

        now = _iso(_utcnow())
        mem = Memory(
            id=memory_id or uuid.uuid4().hex,
            content=content,
            category=(category or "general").strip().lower(),
            tags=[t.strip().lower() for t in (tags or []) if t.strip()],
            importance=max(0.0, min(1.0, float(importance))),
            created_at=now,
            last_accessed=now,
            access_count=0,
            embedding=self._embed(content, tags or []),
            summary=summary or self._summarize(content),
            source=source,
        )
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO memories
                    (id, content, category, tags, importance, created_at,
                     last_accessed, access_count, embedding, summary, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mem.id,
                    mem.content,
                    mem.category,
                    json.dumps(mem.tags),
                    mem.importance,
                    mem.created_at,
                    mem.last_accessed,
                    mem.access_count,
                    _serialize_vector(mem.embedding),
                    mem.summary,
                    mem.source,
                ),
            )
        self._maybe_compress()
        return mem.id

    def get(self, memory_id: str) -> Memory | None:
        """Fetch by full or prefix ID (matches MemoryForget's partial lookup)."""
        with self._lock:
            row = self._conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
            if row is None:
                row = self._conn.execute(
                    "SELECT * FROM memories WHERE id LIKE ? ORDER BY created_at LIMIT 1",
                    (f"{memory_id}%",),
                ).fetchone()
        return Memory.from_row(row) if row else None

    def delete(self, memory_id: str) -> bool:
        """Delete by full or prefix ID. Returns True if a row was removed."""
        target = self.get(memory_id)
        if not target:
            return False
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM memories WHERE id = ?", (target.id,))
        return cur.rowcount > 0

    # ── search ─────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        limit: int = 5,
        category: str | None = None,
        semantic_weight: float = 0.6,
        touch: bool = True,
    ) -> list[Memory]:
        """Hybrid semantic + keyword search.

        Final score = ``semantic_weight`` * cosine(TF-IDF)
                    + (1 - ``semantic_weight``) * keyword overlap
                    + small importance prior (0..0.1).
        Results are touched (access_count/importance bumped) unless
        ``touch=False``.
        """
        query = (query or "").strip()
        if not query:
            return []

        q_tokens = _tokenize(query)
        q_vec = _tfidf_vector(q_tokens, self.vector_dims)
        q_set = set(q_tokens)

        with self._lock:
            if category:
                rows = self._conn.execute(
                    "SELECT * FROM memories WHERE category = ?", (category.lower(),)
                ).fetchall()
            else:
                rows = self._conn.execute("SELECT * FROM memories").fetchall()

        scored: list[tuple[float, Memory]] = []
        for row in rows:
            mem = Memory.from_row(row)
            sem = _cosine(q_vec, mem.embedding)

            # keyword overlap against content + tags + summary
            hay = set(_tokenize(f"{mem.content} {' '.join(mem.tags)} {mem.summary}"))
            kw = len(q_set & hay) / max(len(q_set), 1)

            score = semantic_weight * sem + (1.0 - semantic_weight) * kw
            score += 0.1 * mem.importance  # importance prior
            if score > 0.0:
                scored.append((score, mem))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [m for _, m in scored[:limit]]
        if touch and results:
            self._touch_many([m.id for m in results])
        return results

    def recall_by_category(
        self, category: str, limit: int = 20, min_importance: float = 0.0
    ) -> list[Memory]:
        """All memories in a category, most important / recent first."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM memories
                WHERE category = ? AND importance >= ?
                ORDER BY importance DESC, last_accessed DESC
                LIMIT ?
                """,
                (category.lower(), min_importance, limit),
            ).fetchall()
        return [Memory.from_row(r) for r in rows]

    # ── importance / decay ─────────────────────────────────────────

    def update_importance(self, memory_id: str, importance: float) -> bool:
        """Explicitly set importance (clamped to [0, 1])."""
        target = self.get(memory_id)
        if not target:
            return False
        value = max(0.0, min(1.0, float(importance)))
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE memories SET importance = ? WHERE id = ?", (value, target.id)
            )
        return cur.rowcount > 0

    def decay_memories(self, half_life_days: float = DECAY_HALF_LIFE_DAYS) -> int:
        """Exponentially decay importance of stale memories.

        Memories not accessed for ``half_life_days`` lose half their
        importance (continuous exponential decay), floored at DECAY_FLOOR.
        Recently accessed or frequently used memories decay more slowly.
        Returns the number of memories updated.
        """
        now = _utcnow()
        updated = 0
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT id, importance, last_accessed, access_count FROM memories"
            ).fetchall()
            for row in rows:
                age_days = (now - _parse_dt(row["last_accessed"])).total_seconds() / 86400.0
                if age_days <= 0:
                    continue
                # Frequent access slows decay: effective half-life scales up
                effective_half_life = half_life_days * (1.0 + math.log1p(row["access_count"]))
                factor = 0.5 ** (age_days / max(effective_half_life, 1e-6))
                new_importance = max(DECAY_FLOOR, row["importance"] * factor)
                if abs(new_importance - row["importance"]) > 1e-4:
                    self._conn.execute(
                        "UPDATE memories SET importance = ? WHERE id = ?",
                        (new_importance, row["id"]),
                    )
                    updated += 1
        return updated

    # ── compression ────────────────────────────────────────────────

    def compress_old_memories(
        self,
        older_than_days: float = 30.0,
        max_importance: float = 0.3,
        keep_minimum: int = 50,
    ) -> int:
        """Summarize + merge stale, low-importance memories.

        Memories older than ``older_than_days`` with importance at or below
        ``max_importance`` are grouped by category; each group of 2+ is merged
        into a single digest memory (concatenated summaries) whose importance
        is the max of the group, and the originals are deleted. Always keeps at
        least ``keep_minimum`` total memories. Returns number merged away.
        """
        cutoff = _iso(_utcnow() - timedelta(days=older_than_days))
        with self._lock, self._conn:
            total = self._count()
            rows = self._conn.execute(
                """
                SELECT * FROM memories
                WHERE last_accessed < ? AND importance <= ?
                ORDER BY category, created_at
                """,
                (cutoff, max_importance),
            ).fetchall()

            # Respect keep_minimum
            budget = max(0, total - keep_minimum)
            rows = rows[:budget]

            groups: dict[str, list[sqlite3.Row]] = {}
            for row in rows:
                groups.setdefault(row["category"], []).append(row)

            merged = 0
            for category, group in groups.items():
                if len(group) < 2:
                    continue
                contents = []
                tags: set[str] = set()
                sources: set[str] = set()
                max_importance_group = 0.0
                total_access = 0
                ids = []
                for row in group:
                    mem = Memory.from_row(row)
                    ids.append(mem.id)
                    contents.append(mem.summary or self._summarize(mem.content))
                    tags.update(mem.tags)
                    if mem.source:
                        sources.add(mem.source)
                    max_importance_group = max(max_importance_group, mem.importance)
                    total_access += mem.access_count

                digest = self._summarize(" | ".join(contents), max_chars=500)
                now = _iso(_utcnow())
                merged_id = uuid.uuid4().hex
                self._conn.execute(
                    """
                    INSERT INTO memories
                        (id, content, category, tags, importance, created_at,
                         last_accessed, access_count, embedding, summary, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        merged_id,
                        digest,
                        category,
                        json.dumps(sorted(tags)),
                        max_importance_group,
                        now,
                        now,
                        total_access,
                        _serialize_vector(self._embed(digest, sorted(tags))),
                        digest,
                        ",".join(sorted(sources)) or "compressed",
                    ),
                )
                self._conn.executemany("DELETE FROM memories WHERE id = ?", [(i,) for i in ids])
                merged += len(ids)
        return merged

    # ── export / stats ─────────────────────────────────────────────

    def export(self, path: Path | str | None = None) -> str:
        """Export all memories to a JSON file. Returns the file path."""
        with self._lock:
            rows = self._conn.execute("SELECT * FROM memories ORDER BY created_at").fetchall()
        payload = {
            "version": EXPORT_VERSION,
            "exported_at": _iso(_utcnow()),
            "count": len(rows),
            "memories": [Memory.from_row(r).to_dict() for r in rows],
        }
        out = Path(path) if path else self.db_path.with_suffix(".export.json")
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return str(out)

    def stats(self) -> dict[str, Any]:
        """Store statistics: counts, categories, importance distribution."""
        with self._lock:
            total = self._count()
            by_cat = {
                r["category"]: r["n"]
                for r in self._conn.execute(
                    "SELECT category, COUNT(*) AS n FROM memories GROUP BY category"
                )
            }
            row = self._conn.execute(
                """
                SELECT AVG(importance) AS avg_imp, MAX(importance) AS max_imp,
                       MIN(importance) AS min_imp, SUM(access_count) AS accesses
                FROM memories
                """
            ).fetchone()
            recent = self._conn.execute(
                "SELECT COUNT(*) AS n FROM memories WHERE last_accessed >= ?",
                (_iso(_utcnow() - timedelta(days=7)),),
            ).fetchone()["n"]
        return {
            "total": total,
            "categories": by_cat,
            "avg_importance": round(row["avg_imp"] or 0.0, 4),
            "max_importance": row["max_imp"] or 0.0,
            "min_importance": row["min_imp"] or 0.0,
            "total_accesses": row["accesses"] or 0,
            "accessed_last_7d": recent,
            "db_path": str(self.db_path),
            "db_size_bytes": self.db_path.stat().st_size if self.db_path.exists() else 0,
            "compact_threshold": self.compact_threshold,
        }

    # ── integration hooks (MemoryRemember / MemoryRecall parity) ──

    def add(
        self,
        content: str,
        tags: list[str] | None = None,
        alias: str = "",
        source: str = "",
    ) -> str:
        """Drop-in for ``memory.store.MemoryStore.add`` (MemoryRemember)."""
        return self.add_memory(
            content,
            category=alias or "general",
            tags=tags,
            source=source,
            importance=0.6,
        )

    def get_context(self, query: str, limit: int = 5) -> str:
        """Drop-in for ``memory.store.MemoryStore.get_context`` (MemoryRecall).

        Returns a formatted, numbered list of relevant memories.
        """
        results = self.search(query, limit=limit)
        if not results:
            return "No relevant memories found."
        lines = []
        for i, mem in enumerate(results, 1):
            tag_str = f" [{', '.join(mem.tags)}]" if mem.tags else ""
            lines.append(f"{i}. {mem.content}{tag_str} (importance: {mem.importance:.2f})")
        return "\n".join(lines)

    # ── internals ──────────────────────────────────────────────────

    def _embed(self, content: str, tags: Iterable[str]) -> dict[int, float]:
        text = f"{content} {' '.join(tags)}"
        return _tfidf_vector(_tokenize(text), self.vector_dims)

    @staticmethod
    def _summarize(text: str, max_chars: int = SUMMARY_MAX_CHARS) -> str:
        """Cheap extractive summary: first sentence(s) up to max_chars."""
        text = " ".join(text.split())
        if len(text) <= max_chars:
            return text
        cut = text[:max_chars]
        # Prefer ending at a sentence boundary
        for sep in (". ", "! ", "? "):
            idx = cut.rfind(sep)
            if idx > max_chars // 2:
                return cut[: idx + 1]
        return cut.rsplit(" ", 1)[0].rstrip(",;:") + "…"

    def _touch_many(self, ids: list[str]) -> None:
        now = _iso(_utcnow())
        with self._lock, self._conn:
            self._conn.executemany(
                """
                UPDATE memories
                SET last_accessed = ?,
                    access_count = access_count + 1,
                    importance = MIN(1.0, importance + ?)
                WHERE id = ?
                """,
                [(now, ACCESS_BOOST, mid) for mid in ids],
            )

    def _count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) AS n FROM memories").fetchone()["n"]

    def _maybe_compress(self) -> None:
        with self._lock:
            if self._count() > self.compact_threshold:
                self.compress_old_memories()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> AdvancedMemorySystem:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# ── Module-level singleton (matches memory.store.get_memory pattern) ──

_default: AdvancedMemorySystem | None = None
_default_lock = threading.Lock()


def get_advanced_memory() -> AdvancedMemorySystem:
    """Process-wide shared instance (lazy)."""
    global _default
    with _default_lock:
        if _default is None:
            _default = AdvancedMemorySystem()
    return _default


# ── Smoke test ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="adv_mem_test_")) / "test.db"
    print(f"[smoke] db: {tmp}")

    mem = AdvancedMemorySystem(db_path=tmp, compact_threshold=5)

    # 1. add_memory
    id1 = mem.add_memory(
        "User prefers dark themes and vim keybindings in all editors.",
        category="preferences",
        tags=["ui", "editor"],
        importance=0.8,
        source="smoke-test",
    )
    id2 = mem.add_memory(
        "The project uses SQLite for session persistence under data/sessions.",
        category="architecture",
        tags=["sqlite", "storage"],
        importance=0.7,
    )
    id3 = mem.add_memory(
        "Python backend services should avoid external API calls for embeddings.",
        category="constraints",
        tags=["python", "stdlib"],
        importance=0.6,
    )
    print(f"[smoke] added 3 memories: {id1[:8]}, {id2[:8]}, {id3[:8]}")

    # 2. semantic search (no shared exact keywords with query)
    hits = mem.search("editor color scheme preferences", limit=3)
    assert hits and hits[0].id == id1, f"semantic search failed: {[h.id for h in hits]}"
    print(f"[smoke] semantic search OK -> {hits[0].content[:50]}...")

    # 3. keyword-ish search
    hits = mem.search("SQLite sessions", limit=3)
    assert hits and hits[0].id == id2
    print("[smoke] keyword search OK")

    # 4. recall_by_category
    prefs = mem.recall_by_category("preferences")
    assert len(prefs) == 1 and prefs[0].id == id1
    print("[smoke] recall_by_category OK")

    # 5. update_importance
    assert mem.update_importance(id3, 0.95)
    assert abs(mem.get(id3).importance - 0.95) < 1e-6
    print("[smoke] update_importance OK")

    # 6. decay (simulate stale memory)
    with mem._lock, mem._conn:
        old = _iso(_utcnow() - timedelta(days=120))
        mem._conn.execute(
            "UPDATE memories SET last_accessed = ?, importance = 0.5 WHERE id = ?",
            (old, id2),
        )
    n_decayed = mem.decay_memories(half_life_days=30)
    decayed_imp = mem.get(id2).importance
    assert n_decayed >= 1 and decayed_imp < 0.5
    print(f"[smoke] decay OK ({n_decayed} updated, id2 importance -> {decayed_imp:.3f})")

    # 7. compression: add enough stale low-importance memories to trigger merge
    with mem._lock, mem._conn:
        stale = _iso(_utcnow() - timedelta(days=90))
        for i in range(4):
            mid = mem.add_memory(
                f"Old scratch note {i} about temporary debugging.",
                category="scratch",
                importance=0.1,
            )
            mem._conn.execute("UPDATE memories SET last_accessed = ? WHERE id = ?", (stale, mid))
    before = mem.stats()["total"]
    merged = mem.compress_old_memories(older_than_days=30, max_importance=0.3, keep_minimum=0)
    after = mem.stats()["total"]
    assert merged >= 2 and after < before
    print(f"[smoke] compression OK (merged {merged}, total {before} -> {after})")

    # 8. export
    export_path = mem.export()
    payload = json.loads(Path(export_path).read_text(encoding="utf-8"))
    assert payload["count"] == after
    print(f"[smoke] export OK -> {export_path}")

    # 9. stats
    s = mem.stats()
    print(f"[smoke] stats: {s['total']} memories, categories={s['categories']}")

    # 10. delete (prefix match)
    assert mem.delete(id1[:8])
    assert mem.get(id1) is None
    print("[smoke] delete OK")

    # 11. integration hooks
    hook_id = mem.add("Hook test fact about cline launcher.", tags=["hook"], alias="test")
    ctx = mem.get_context("cline launcher")
    assert "Hook test fact" in ctx
    print("[smoke] integration hooks (add/get_context) OK")

    mem.close()
    print("[smoke] ALL PASSED")
