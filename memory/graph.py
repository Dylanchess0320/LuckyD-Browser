from __future__ import annotations

"""
Memory Graph — persistent knowledge graph with tags, clusters, edges, and graph traversal.
Inspired by jcode's jcode-memory-types graph module.

Data model:
  - Memories are nodes with content, tags, confidence, embeddings
  - Tags are nodes (tag:{name})
  - Clusters are auto-grouped nodes (cluster:{id})
  - Edges connect memories: HasTag, RelatesTo, Supersedes, Contradicts, DerivedFrom
  - BFS cascade retrieval walks edges for context-sensitive recall
"""

import json
import math
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────────

MEMORY_GRAPH_VERSION = 2
DEFAULT_CONFIDENCE = 1.0
DEFAULT_STRENGTH = 1
LEGACY_EMBEDDING_MODEL = "minilm-l6-v2"


# ── Enums ──────────────────────────────────────────────────────────────


class TrustLevel(str, Enum):
    HIGH = "high"  # User explicitly stated
    MEDIUM = "medium"  # Observed from behavior
    LOW = "low"  # Inferred by the agent

    @classmethod
    def default(cls):
        return cls.MEDIUM


class EdgeKind(str, Enum):
    HASTAG = "has_tag"
    INCLUSTER = "in_cluster"
    RELATESTO = "relates_to"
    SUPERSEDES = "supersedes"
    CONTRADICTS = "contradicts"
    DERIVEDFROM = "derived_from"

    def traversal_weight(self) -> float:
        return {
            EdgeKind.HASTAG: 0.8,
            EdgeKind.INCLUSTER: 0.6,
            EdgeKind.RELATESTO: 1.0,
            EdgeKind.SUPERSEDES: 0.9,
            EdgeKind.CONTRADICTS: 0.3,
            EdgeKind.DERIVEDFROM: 0.7,
        }[self]


# ── Core Data Classes ──────────────────────────────────────────────────


@dataclass
class Reinforcement:
    session_id: str
    message_index: int
    timestamp: str  # ISO format

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**d)


@dataclass
class MemoryEntry:
    id: str
    category: str
    content: str
    tags: list = field(default_factory=list)
    search_text: str = ""
    created_at: str = ""  # ISO format
    updated_at: str = ""
    access_count: int = 0
    source: str | None = None
    trust: str = "medium"
    strength: int = 1
    active: bool = True
    superseded_by: str | None = None
    reinforcements: list = field(default_factory=list)
    embedding: list | None = None
    embedding_model: str | None = None
    confidence: float = 1.0
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now
        if not self.search_text:
            self.refresh_search_text()

    def refresh_search_text(self):
        """Build normalized search text from content + tags."""
        text = self.content.lower()
        for t in self.tags:
            text += " " + t.lower().replace("_", " ").replace("-", " ")
        self.search_text = text

    def to_dict(self):
        d = {}
        for k, v in asdict(self).items():
            if k == "reinforcements":
                d[k] = [r.to_dict() if isinstance(r, Reinforcement) else r for r in v]
            elif v is not None and v != [] and v != "" and v != 0 and v != 0.0:
                d[k] = v
        return d

    @classmethod
    def from_dict(cls, d):
        # Handle reinforcements
        if d.get("reinforcements"):
            d["reinforcements"] = [
                Reinforcement.from_dict(r) if isinstance(r, dict) else r
                for r in d["reinforcements"]
            ]
        return cls(**d)

    def effective_embedding_model(self):
        return self.embedding_model or LEGACY_EMBEDDING_MODEL

    def embedding_matches_model(self, model: str) -> bool:
        return self.embedding is not None and self.effective_embedding_model() == model


@dataclass
class TagEntry:
    id: str
    name: str
    description: str | None = None
    count: int = 0
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self):
        return {k: v for k, v in asdict(self).items() if v is not None and v != ""}

    @classmethod
    def from_dict(cls, d):
        return cls(**d)

    @classmethod
    def new(cls, name: str):
        name = name.strip().lower().replace(" ", "_")
        return cls(id=f"tag:{name}", name=name, created_at=datetime.now(timezone.utc).isoformat())


@dataclass
class ClusterEntry:
    id: str
    name: str | None = None
    centroid: list = field(default_factory=list)
    member_count: int = 0
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    def to_dict(self):
        return {k: v for k, v in asdict(self).items() if v is not None and v != ""}

    @classmethod
    def from_dict(cls, d):
        return cls(**d)

    @classmethod
    def new(cls, cluster_id: str):
        now = datetime.now(timezone.utc).isoformat()
        return cls(id=f"cluster:{cluster_id}", created_at=now, updated_at=now)


@dataclass
class Edge:
    target: str
    kind: EdgeKind
    weight: float = 1.0

    def to_dict(self):
        d = {"target": self.target, "kind": self.kind.value}
        if self.kind == EdgeKind.RELATESTO and self.weight != 1.0:
            d["weight"] = self.weight
        return d

    @classmethod
    def from_dict(cls, d):
        kind = EdgeKind(d["kind"])
        weight = d.get("weight", 1.0)
        return cls(target=d["target"], kind=kind, weight=weight)

    @classmethod
    def new(cls, target: str, kind: EdgeKind, weight: float = 1.0):
        return cls(target=target, kind=kind, weight=weight)


@dataclass
class GraphMetadata:
    last_cluster_update: str | None = None
    retrieval_count: int = 0
    link_discovery_count: int = 0

    def to_dict(self):
        return {k: v for k, v in asdict(self).items() if v is not None and v != 0}

    @classmethod
    def from_dict(cls, d):
        return cls(**d)


# ── Memory Graph ───────────────────────────────────────────────────────


def _new_memory_id() -> str:
    ts = int(time.time() * 1000)
    rand = int.from_bytes(os.urandom(6), "big")
    return f"mem_{ts}_{rand}"


import os


class MemoryGraph:
    """The main memory graph — HashMap-based for clean JSON serialization."""

    def __init__(self):
        self.graph_version = MEMORY_GRAPH_VERSION
        self.memories: dict[str, MemoryEntry] = {}
        self.tags: dict[str, TagEntry] = {}
        self.clusters: dict[str, ClusterEntry] = {}
        self.edges: dict[str, list[Edge]] = {}  # source_id -> [Edge]
        self.reverse_edges: dict[str, list[str]] = {}  # target_id -> [source_id]
        self.metadata = GraphMetadata()

    # ── Persistence ────────────────────────────────────────────────

    def save(self, path: Path):
        data = {
            "graph_version": MEMORY_GRAPH_VERSION,
            "memories": {k: v.to_dict() for k, v in self.memories.items()},
            "tags": {k: v.to_dict() for k, v in self.tags.items()},
            "clusters": {k: v.to_dict() for k, v in self.clusters.items()},
            "edges": {k: [e.to_dict() for e in v] for k, v in self.edges.items()},
            "reverse_edges": dict(self.reverse_edges),
            "metadata": self.metadata.to_dict(),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: Path) -> MemoryGraph:
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return cls()

        g = cls()
        g.memories = {k: MemoryEntry.from_dict(v) for k, v in data.get("memories", {}).items()}
        g.tags = {k: TagEntry.from_dict(v) for k, v in data.get("tags", {}).items()}
        g.clusters = {k: ClusterEntry.from_dict(v) for k, v in data.get("clusters", {}).items()}
        g.edges = {k: [Edge.from_dict(e) for e in v] for k, v in data.get("edges", {}).items()}
        g.reverse_edges = dict(data.get("reverse_edges", {}))
        g.metadata = GraphMetadata.from_dict(data.get("metadata", {}))
        g.graph_version = data.get("graph_version", 1)
        return g

    # ── Memory Operations ──────────────────────────────────────────

    def add_memory(self, entry: MemoryEntry) -> str:
        """Add a memory entry. Creates tag nodes and HasTag edges for tags."""
        entry.refresh_search_text()
        mem_id = entry.id

        for tag_name in entry.tags:
            self._ensure_tag(tag_name)
            tag_id = f"tag:{tag_name}"
            self._add_edge_internal(mem_id, tag_id, EdgeKind.HASTAG)
            if tag_id in self.tags:
                self.tags[tag_id].count += 1

        if entry.superseded_by:
            self._add_edge_internal(entry.superseded_by, mem_id, EdgeKind.SUPERSEDES)

        self.memories[mem_id] = entry
        return mem_id

    def add_memory_raw(
        self,
        content: str,
        category: str = "general",
        tags: list[str] | None = None,
        source: str | None = None,
        trust: str = "medium",
    ) -> MemoryEntry:
        """Convenience: create and add a memory entry."""
        tags = tags or []
        entry = MemoryEntry(
            id=_new_memory_id(),
            category=category,
            content=content,
            tags=[t.lower().replace(" ", "_") for t in tags],
            source=source,
            trust=trust,
        )
        self.add_memory(entry)
        return entry

    def get_memory(self, mem_id: str) -> MemoryEntry | None:
        entry = self.memories.get(mem_id)
        if entry:
            entry.access_count += 1
            entry.updated_at = datetime.now(timezone.utc).isoformat()
        return entry

    def remove_memory(self, mem_id: str) -> MemoryEntry | None:
        """Remove a memory and its associated edges."""
        # Remove outgoing edges
        if mem_id in self.edges:
            for edge in self.edges[mem_id]:
                if mem_id in self.reverse_edges.get(edge.target, []):
                    self.reverse_edges[edge.target].remove(mem_id)
                if edge.kind == EdgeKind.HASTAG:
                    tag = self.tags.get(edge.target)
                    if tag:
                        tag.count = max(0, tag.count - 1)
            del self.edges[mem_id]

        # Remove incoming edges
        if mem_id in self.reverse_edges:
            for src in self.reverse_edges[mem_id]:
                if src in self.edges:
                    self.edges[src] = [e for e in self.edges[src] if e.target != mem_id]
            del self.reverse_edges[mem_id]

        return self.memories.pop(mem_id, None)

    def update_memory(self, mem_id: str, **kwargs) -> bool:
        entry = self.memories.get(mem_id)
        if not entry:
            return False
        for k, v in kwargs.items():
            if hasattr(entry, k) and v is not None:
                setattr(entry, k, v)
        entry.updated_at = datetime.now(timezone.utc).isoformat()
        entry.refresh_search_text()
        return True

    # ── Tag Operations ─────────────────────────────────────────────

    def _ensure_tag(self, name: str):
        name = name.strip().lower().replace(" ", "_")
        tag_id = f"tag:{name}"
        if tag_id not in self.tags:
            self.tags[tag_id] = TagEntry.new(name)

    def get_or_create_tag(self, name: str, description: str | None = None) -> TagEntry:
        self._ensure_tag(name)
        # _ensure_tag normalizes ("My Tag" -> "my_tag"); use the normalized
        # id here or the lookup raises KeyError on the raw name.
        tag_id = f"tag:{name.strip().lower().replace(' ', '_')}"
        if description and self.tags[tag_id].description is None:
            self.tags[tag_id].description = description
        return self.tags[tag_id]

    def list_tags(self) -> list[TagEntry]:
        return sorted(self.tags.values(), key=lambda t: t.count, reverse=True)

    # ── Edge Operations ────────────────────────────────────────────

    def _add_edge_internal(self, source: str, target: str, kind: EdgeKind, weight: float = 1.0):
        if source not in self.edges:
            self.edges[source] = []
        # Avoid duplicates
        for e in self.edges[source]:
            if e.target == target and e.kind == kind:
                return
        self.edges[source].append(Edge.new(target, kind, weight))

        if target not in self.reverse_edges:
            self.reverse_edges[target] = []
        if source not in self.reverse_edges[target]:
            self.reverse_edges[target].append(source)

    def add_edge(self, source: str, target: str, kind: EdgeKind, weight: float = 1.0) -> bool:
        if isinstance(kind, str):
            # store.link() and other callers pass plain strings like "related";
            # accept the friendly aliases too.
            kind = {"related": EdgeKind.RELATESTO.value}.get(kind, kind)
            try:
                kind = EdgeKind(kind)
            except ValueError:
                return False
        if source not in self.memories and source not in self.tags and source not in self.clusters:
            return False
        if target not in self.memories and target not in self.tags and target not in self.clusters:
            return False
        self._add_edge_internal(source, target, kind, weight)
        self.metadata.link_discovery_count += 1
        return True

    def get_edges(self, node_id: str) -> list[Edge]:
        return self.edges.get(node_id, [])

    def remove_edge(self, source: str, target: str, kind: EdgeKind | None = None) -> bool:
        if source not in self.edges:
            return False
        before = len(self.edges[source])
        self.edges[source] = [
            e
            for e in self.edges[source]
            if not (e.target == target and (kind is None or e.kind == kind))
        ]
        removed = before - len(self.edges[source]) > 0
        if removed and target in self.reverse_edges and source in self.reverse_edges[target]:
            self.reverse_edges[target].remove(source)
        return removed

    # ── Search & Retrieval ─────────────────────────────────────────

    def search_text(self, query: str, limit: int = 10) -> list[tuple[MemoryEntry, float]]:
        """BM25-style text search over memory content."""
        query_terms = query.lower().split()
        if not query_terms:
            return []

        # TF-IDF scoring
        scores: list[tuple[str, float]] = []
        num_docs = max(len(self.memories), 1)

        for mem_id, entry in self.memories.items():
            if not entry.active:
                continue
            score = 0.0
            text = entry.search_text
            for term in query_terms:
                tf = text.count(term) / max(len(text.split()), 1)
                df = sum(1 for e in self.memories.values() if term in e.search_text)
                idf = math.log((num_docs - df + 0.5) / (df + 0.5) + 1.0)
                score += tf * idf
            if score > 0:
                # Boost by confidence and recency
                score *= entry.confidence
                scores.append((mem_id, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return [(self.memories[mem_id], score) for mem_id, score in scores[:limit]]

    def search_by_tag(self, tag: str) -> list[tuple[MemoryEntry, float]]:
        scores = []
        for mem_id, mem in self.memories.items():
            score = 0.0
            if tag in mem.tags:
                score += 1.0
            if tag in mem.metadata.get("aliases", []):
                score += 0.8
            if score > 0:
                scores.append((mem_id, score))
        scores.sort(key=lambda x: x[1], reverse=True)
        return [(self.memories[mem_id], score) for mem_id, score in scores]

    def bfs(self, start_id: str, max_depth: int = 2) -> list[tuple[MemoryEntry, int]]:
        """BFS from start, returning (node, depth) tuples up to max_depth."""
        if start_id not in self.memories:
            return []
        visited = {start_id}
        queue = [(start_id, 0)]
        results: list[tuple[MemoryEntry, int]] = []
        while queue:
            current, depth = queue.pop(0)
            if current in self.memories:
                results.append((self.memories[current], depth))
            if depth >= max_depth:
                continue
            for edge in self.edges.get(current, []):
                neighbor_id = edge.target
                if neighbor_id not in visited:
                    visited.add(neighbor_id)
                    queue.append((neighbor_id, depth + 1))
        return results

    def summarize(self) -> str:
        """Return a summary of all memories in the graph."""
        if not self.memories:
            return "No memories stored."
        lines = [f"Memory Graph: {len(self.memories)} entries"]
        for _mem_id, entry in self.memories.items():
            tags = ", ".join(entry.tags) if entry.tags else "none"
            preview = entry.content[:80].replace("\n", " ")
            lines.append(f"  [{entry.id}] {preview}  (tags: {tags})")
        return "\n".join(lines)


# -- Singleton accessor ---------------------------------------------------
_graph_instance: MemoryGraph | None = None


def get_memory_graph() -> MemoryGraph:
    """Return the process-wide memory graph, best-effort loaded from disk."""
    global _graph_instance
    if _graph_instance is None:
        try:
            from config import MEMORY_DIR

            _graph_instance = MemoryGraph.load(Path(MEMORY_DIR) / "graph.json")
        except Exception:
            _graph_instance = MemoryGraph()
    return _graph_instance
