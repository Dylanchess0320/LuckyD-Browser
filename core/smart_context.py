"""
Smart context engine — relevance-scored file selection and compression for prompts.

Given a task query, SmartContextEngine ranks candidate files (keyword overlap,
path matching, recency, file-type weights, import-graph distance), compresses
their contents (comment/blank-line stripping, function truncation, head+tail
smart truncation), and packs as many high-value files as fit inside a token
budget. Scored results are cached with a TTL to avoid re-parsing unchanged files.

Stdlib only. Reuses estimate_tokens from core.context_manager.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .context_manager import estimate_tokens

# ── File type registry ────────────────────────────────────────────────

# Weight multipliers per extension — code beats config beats docs beats data.
_FILE_TYPE_WEIGHTS: dict[str, float] = {
    ".py": 1.00,
    ".pyi": 0.95,
    ".js": 0.90,
    ".ts": 0.90,
    ".jsx": 0.85,
    ".tsx": 0.85,
    ".go": 0.90,
    ".rs": 0.90,
    ".java": 0.85,
    ".c": 0.85,
    ".h": 0.80,
    ".cpp": 0.85,
    ".cs": 0.85,
    ".rb": 0.85,
    ".php": 0.80,
    ".sh": 0.70,
    ".bat": 0.60,
    ".ps1": 0.60,
    ".md": 0.55,
    ".rst": 0.50,
    ".txt": 0.40,
    ".toml": 0.45,
    ".yaml": 0.45,
    ".yml": 0.45,
    ".json": 0.40,
    ".ini": 0.35,
    ".cfg": 0.35,
    ".xml": 0.30,
    ".html": 0.40,
    ".css": 0.40,
    ".sql": 0.50,
    ".lock": 0.10,
}
_DEFAULT_TYPE_WEIGHT = 0.30

# Extensions treated as code for token estimation + comment stripping.
_CODE_EXTS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".c", ".h",
    ".cpp", ".cs", ".rb", ".php", ".sh", ".ps1", ".sql",
}

# Comment syntax per family — (line_comment_prefixes, block_comment_pairs).
_LINE_COMMENTS = ("#",)
_BLOCK_COMMENTS: tuple[tuple[str, str], ...] = ()
_C_STYLE_LINE = ("//",)
_C_STYLE_BLOCK = (("/*", "*/"),)

_COMMENT_SYNTAX: dict[str, tuple[tuple[str, ...], tuple[tuple[str, str], ...]]] = {
    ".py": (("#",), ()),
    ".sh": (("#",), ()),
    ".rb": (("#",), ()),
    ".yaml": (("#",), ()),
    ".yml": (("#",), ()),
    ".toml": (("#",), ()),
    ".js": (_C_STYLE_LINE, _C_STYLE_BLOCK),
    ".jsx": (_C_STYLE_LINE, _C_STYLE_BLOCK),
    ".ts": (_C_STYLE_LINE, _C_STYLE_BLOCK),
    ".tsx": (_C_STYLE_LINE, _C_STYLE_BLOCK),
    ".go": (_C_STYLE_LINE, _C_STYLE_BLOCK),
    ".rs": (_C_STYLE_LINE, _C_STYLE_BLOCK),
    ".java": (_C_STYLE_LINE, _C_STYLE_BLOCK),
    ".c": (_C_STYLE_LINE, _C_STYLE_BLOCK),
    ".h": (_C_STYLE_LINE, _C_STYLE_BLOCK),
    ".cpp": (_C_STYLE_LINE, _C_STYLE_BLOCK),
    ".cs": (_C_STYLE_LINE, _C_STYLE_BLOCK),
    ".php": (_C_STYLE_LINE, _C_STYLE_BLOCK),
    ".css": ((), _C_STYLE_BLOCK),
    ".sql": (("--",), _C_STYLE_BLOCK),
}

# Stop words stripped from queries so they don't pollute keyword overlap.
_STOP_WORDS = frozenset(
    "a an and are as at be by for from has how i in is it of on or that the "
    "this to was what when where which who why will with should would could "
    "can do does did me my we you your our their its them they".split()
)

# ── Data structures ───────────────────────────────────────────────────


@dataclass
class FileScore:
    """Relevance breakdown for a single file against a task query."""

    path: str
    total: float  # Weighted combination of the components below.
    keyword: float = 0.0
    path_match: float = 0.0
    recency: float = 0.0
    type_weight: float = 0.0
    import_distance: float = 0.0


@dataclass
class ContextItem:
    """A file selected for inclusion in a built context bundle."""

    path: str
    content: str
    score: float
    tokens: int = 0


@dataclass
class _CacheEntry:
    """TTL-cached score record keyed by (path, query) + file fingerprint."""

    score: FileScore
    created_at: float
    fingerprint: str


@dataclass
class SmartContextEngine:
    """Ranks and compresses project files to fit a token budget for a task."""

    root: str = "."
    cache_ttl: float = 300.0  # seconds
    recency_half_life: float = 86400.0 * 7  # 1 week in seconds
    max_function_lines: int = 40  # truncate bodies longer than this
    head_tail_lines: int = 120  # smart truncation: keep this many head + tail lines
    # Component weights — must sum to ~1.0 (import distance is a bonus term).
    w_keyword: float = 0.40
    w_path: float = 0.25
    w_recency: float = 0.20
    w_type: float = 0.15
    w_import: float = 0.20  # bonus multiplier, not part of base sum

    _cache: dict[tuple[str, str], _CacheEntry] = field(default_factory=dict, init=False)
    _import_graph: dict[str, set[str]] = field(default_factory=dict, init=False)
    _graph_built_at: float = field(default=0.0, init=False)

    # ── Public API ────────────────────────────────────────────────────

    def score_file(self, path: str, task_query: str) -> float:
        """Score a single file's relevance to the task query (0.0–~1.5).

        Results are cached with a TTL and invalidated when the file changes.
        """
        norm = self._norm_path(path)
        fp = self._fingerprint(norm)
        key = (norm, task_query)
        entry = self._cache.get(key)
        if entry and entry.fingerprint == fp and (time.time() - entry.created_at) < self.cache_ttl:
            return entry.score.total

        score = self._compute_score(norm, task_query, fp)
        self._cache[key] = _CacheEntry(score=score, created_at=time.time(), fingerprint=fp)
        return score.total

    def rank_files(self, paths: Iterable[str], task_query: str) -> list[FileScore]:
        """Score and sort files by descending relevance."""
        scores: list[FileScore] = []
        for p in paths:
            norm = self._norm_path(p)
            fp = self._fingerprint(norm)
            key = (norm, task_query)
            entry = self._cache.get(key)
            if entry and entry.fingerprint == fp and (time.time() - entry.created_at) < self.cache_ttl:
                scores.append(entry.score)
                continue
            score = self._compute_score(norm, task_query, fp)
            self._cache[key] = _CacheEntry(score=score, created_at=time.time(), fingerprint=fp)
            scores.append(score)
        scores.sort(key=lambda s: s.total, reverse=True)
        return scores

    def build_context(
        self, task_query: str, max_tokens: int = 8000
    ) -> list[tuple[str, str, float]]:
        """Return an optimized list of (path, content, score) fitting the token budget.

        Walks the project tree, ranks all scorable files, then greedily packs
        the highest-scoring files — compressing each as needed — until the
        budget is exhausted.
        """
        candidates = self._walk_project()
        ranked = self.rank_files(candidates, task_query)

        items: list[ContextItem] = []
        budget_left = max_tokens
        for fs in ranked:
            if fs.total <= 0.0 or budget_left <= 0:
                break
            raw = self._read_file(fs.path)
            if raw is None:
                continue
            is_code = self._ext(fs.path) in _CODE_EXTS
            compressed = self.compress_content(fs.path, raw)
            tokens = estimate_tokens(compressed, is_code=is_code)
            if tokens > budget_left:
                # Try harder: keep only head+tail within the remaining budget.
                compressed = self._truncate_to_budget(fs.path, compressed, budget_left)
                tokens = estimate_tokens(compressed, is_code=is_code)
                if tokens <= 0 or tokens > budget_left:
                    continue
            items.append(ContextItem(path=fs.path, content=compressed, score=fs.total, tokens=tokens))
            budget_left -= tokens

        return [(it.path, it.content, it.score) for it in items]

    # ── Scoring internals ─────────────────────────────────────────────

    def _compute_score(self, path: str, task_query: str, fingerprint: str) -> FileScore:
        query_terms = self._query_terms(task_query)
        content = self._read_file(path) or ""

        keyword = self._keyword_score(path, content, query_terms)
        path_match = self._path_score(path, query_terms)
        recency = self._recency_score(path)
        type_w = _FILE_TYPE_WEIGHTS.get(self._ext(path), _DEFAULT_TYPE_WEIGHT)
        import_dist = self._import_distance_score(path, query_terms)

        base = (
            self.w_keyword * keyword
            + self.w_path * path_match
            + self.w_recency * recency
            + self.w_type * type_w
        )
        total = base * (1.0 + self.w_import * import_dist)
        return FileScore(
            path=path,
            total=round(total, 6),
            keyword=keyword,
            path_match=path_match,
            recency=recency,
            type_weight=type_w,
            import_distance=import_dist,
        )

    def _keyword_score(self, path: str, content: str, query_terms: set[str]) -> float:
        """Term-frequency-style overlap between query terms and file content."""
        if not query_terms or not content:
            return 0.0
        lowered = content.lower()
        hits = 0
        for term in query_terms:
            # Count up to 5 occurrences; log-dampen to avoid keyword-stuffed files.
            count = min(lowered.count(term), 5)
            if count:
                hits += 1 + math.log2(count)
        # Normalize by query size; small path bonus for term in filename.
        score = hits / (len(query_terms) * 3.3)  # ~1.0 when every term hits 5x
        name = os.path.basename(path).lower()
        if any(t in name for t in query_terms):
            score += 0.15
        return min(score, 1.0)

    def _path_score(self, path: str, query_terms: set[str]) -> float:
        """How many query terms appear in the file's path segments."""
        if not query_terms:
            return 0.0
        segments = [s for s in re.split(r"[\\/_.\-]+", path.lower()) if s]
        hits = sum(
            1
            for t in query_terms
            if any(t in seg or (len(seg) > 2 and seg in t) for seg in segments)
        )
        return hits / len(query_terms)

    def _recency_score(self, path: str) -> float:
        """Exponential decay based on file mtime (newer = higher)."""
        try:
            age = max(0.0, time.time() - os.path.getmtime(path))
        except OSError:
            return 0.0
        # 0.5 at half-life, approaching 1.0 for fresh files.
        return math.exp(-math.log(2) * age / self.recency_half_life)

    def _import_distance_score(self, path: str, query_terms: set[str]) -> float:
        """Bonus for files that import (or are imported by) keyword-matching files.

        Builds a lazy regex-parsed import graph for Python files and rewards
        files within one hop of a strong keyword match.
        """
        if self._ext(path) != ".py":
            return 0.0
        self._ensure_import_graph()
        neighbors = self._import_graph.get(path, set())
        if not neighbors:
            return 0.0
        content_cache: dict[str, str] = {}

        def _matches(p: str) -> bool:
            c = content_cache.get(p)
            if c is None:
                c = (self._read_file(p) or "").lower()
                content_cache[p] = c
            return any(t in c for t in query_terms)

        direct = sum(1 for n in neighbors if _matches(n))
        return min(direct / max(len(neighbors), 1), 1.0)

    # ── Import graph (regex-based, Python only) ───────────────────────

    _IMPORT_RE = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.M)

    def _ensure_import_graph(self) -> None:
        """(Re)build the import graph if stale."""
        if self._import_graph and (time.time() - self._graph_built_at) < self.cache_ttl:
            return
        graph: dict[str, set[str]] = {}
        py_files = [p for p in self._walk_project() if p.endswith(".py")]
        # Map module dotted names to paths for resolution.
        module_map: dict[str, str] = {}
        for p in py_files:
            rel = os.path.relpath(p, self.root)
            dotted = rel.replace(os.sep, ".").replace("/", ".")
            if dotted.endswith(".py"):
                dotted = dotted[:-3]
            if dotted.endswith(".__init__"):
                dotted = dotted[: -len(".__init__")]
            module_map[dotted] = p
        for p in py_files:
            content = self._read_file(p) or ""
            deps: set[str] = set()
            for m in self._IMPORT_RE.finditer(content):
                mod = m.group(1) or m.group(2) or ""
                # Resolve exact module or its longest prefix present in the map.
                while mod:
                    if mod in module_map:
                        deps.add(module_map[mod])
                        break
                    mod = mod.rpartition(".")[0]
            graph[p] = deps
        # Add reverse edges so importers also count as neighbors.
        for src, deps in list(graph.items()):
            for dep in deps:
                graph.setdefault(dep, set()).add(src)
        self._import_graph = graph
        self._graph_built_at = time.time()

    # ── Compression ───────────────────────────────────────────────────

    def compress_content(self, path: str, content: str) -> str:
        """Compress file content: strip comments/blank lines, truncate long
        functions, and smart-truncate very long files (head + tail)."""
        ext = self._ext(path)
        out = content
        if ext in _CODE_EXTS:
            out = self._strip_comments(ext, out)
            if ext == ".py":
                out = self._truncate_long_functions(out)
        out = self._strip_blank_runs(out)
        lines = out.splitlines()
        if len(lines) > self.head_tail_lines * 2:
            out = self._head_tail(out)
        return out

    def _strip_comments(self, ext: str, content: str) -> str:
        """Remove line and block comments using per-language syntax."""
        line_prefixes, block_pairs = _COMMENT_SYNTAX.get(ext, ((), ()))
        # Block comments first (multi-line pass).
        for start, end in block_pairs:
            pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
            content = pattern.sub("", content)
        if line_prefixes:
            kept: list[str] = []
            for line in content.splitlines():
                stripped = line.lstrip()
                if any(stripped.startswith(p) for p in line_prefixes):
                    continue  # full-line comment
                kept.append(line)
            content = "\n".join(kept)
        return content

    _PY_DEF_RE = re.compile(r"^([ \t]*)(?:async\s+)?def\s+\w+\s*\(.*", re.M)

    def _truncate_long_functions(self, content: str) -> str:
        """For Python: keep the def signature plus first few body lines, elide rest."""
        lines = content.splitlines()
        out: list[str] = []
        i = 0
        n = len(lines)
        while i < n:
            out.append(lines[i])
            m = self._PY_DEF_RE.match(lines[i])
            if m is None:
                i += 1
                continue
            indent = len(m.group(1))
            # Collect the function body (deeper-indented lines).
            body_start = i + 1
            j = body_start
            while j < n:
                ln = lines[j]
                if ln.strip() == "":
                    j += 1
                    continue
                cur = len(ln) - len(ln.lstrip())
                if cur <= indent:
                    break
                j += 1
            body_len = j - body_start
            if body_len > self.max_function_lines:
                keep = 8  # keep the first few meaningful body lines
                out.extend(lines[body_start : body_start + keep])
                elided = body_len - keep
                out.append(" " * (indent + 4) + f"...  # ({elided} lines elided)")
            else:
                out.extend(lines[body_start:j])
            i = j
        return "\n".join(out)

    @staticmethod
    def _strip_blank_runs(content: str) -> str:
        """Collapse 2+ consecutive blank lines into nothing."""
        return re.sub(r"\n{2,}", "\n", content)

    def _head_tail(self, content: str) -> str:
        """Keep the head and tail of a long file with an elision marker."""
        lines = content.splitlines()
        head = lines[: self.head_tail_lines]
        tail = lines[-self.head_tail_lines :]
        elided = len(lines) - len(head) - len(tail)
        return "\n".join(head + [f"\n... ({elided} lines elided) ...\n"] + tail)

    def _truncate_to_budget(self, path: str, content: str, token_budget: int) -> str:
        """Hard-truncate compressed content to fit a remaining token budget."""
        is_code = self._ext(path) in _CODE_EXTS
        # Approximate char budget from token budget.
        cpt = 3.5 if is_code else 4.0
        char_budget = int(token_budget * cpt)
        if len(content) <= char_budget:
            return content
        half = char_budget // 2
        elided_chars = len(content) - char_budget
        return (
            content[:half]
            + f"\n... ({elided_chars} chars elided) ...\n"
            + content[-half:]
        )

    # ── Filesystem helpers ────────────────────────────────────────────

    _IGNORE_DIRS = frozenset(
        {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build", ".idea", ".vscode"}
    )
    _MAX_FILE_BYTES = 512 * 1024  # skip files > 512 KB

    def _walk_project(self) -> list[str]:
        """Collect candidate files under root, skipping junk dirs and huge files."""
        results: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in self._IGNORE_DIRS and not d.startswith(".")]
            for name in filenames:
                p = os.path.join(dirpath, name)
                try:
                    if os.path.getsize(p) > self._MAX_FILE_BYTES:
                        continue
                except OSError:
                    continue
                if self._ext(p) in _FILE_TYPE_WEIGHTS:
                    results.append(p)
        return results

    def _norm_path(self, path: str) -> str:
        """Normalize to an absolute-ish path rooted at self.root."""
        if os.path.isabs(path):
            return os.path.normpath(path)
        return os.path.normpath(os.path.join(self.root, path))

    @staticmethod
    def _ext(path: str) -> str:
        return Path(path).suffix.lower()

    @staticmethod
    def _read_file(path: str) -> str | None:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return None

    def _fingerprint(self, path: str) -> str:
        """Cheap change-detection fingerprint: mtime+size hashed."""
        try:
            st = os.stat(path)
        except OSError:
            return "missing"
        raw = f"{st.st_mtime_ns}:{st.st_size}"
        return hashlib.md5(raw.encode()).hexdigest()

    @staticmethod
    def _query_terms(query: str) -> set[str]:
        """Tokenize a task query into lowercase terms minus stop words."""
        tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", query.lower())
        return {t for t in tokens if t not in _STOP_WORDS and len(t) > 1}

    # ── Cache management ──────────────────────────────────────────────

    def clear_cache(self) -> None:
        """Drop all cached scores and the import graph."""
        self._cache.clear()
        self._import_graph.clear()
        self._graph_built_at = 0.0

    def prune_cache(self) -> int:
        """Remove expired cache entries; returns count removed."""
        now = time.time()
        stale = [k for k, e in self._cache.items() if (now - e.created_at) >= self.cache_ttl]
        for k in stale:
            del self._cache[k]
        return len(stale)


# ── Smoke test ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile

    print("SmartContextEngine smoke test")
    print("=" * 50)

    with tempfile.TemporaryDirectory() as tmp:
        # Fixture: a tiny Python project with an import edge and a doc.
        pkg = Path(tmp) / "mypkg"
        pkg.mkdir()

        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "auth.py").write_text(
            '"""Auth module."""\n'
            "import mypkg.db\n\n"
            "# login user\n"
            "def login(username, password):\n"
            "    # check credentials\n"
            "    user = mypkg.db.get_user(username)\n"
            + "\n".join(f"    x{i} = {i}" for i in range(60))
            + "\n    return user\n",
            encoding="utf-8",
        )
        (pkg / "db.py").write_text(
            "# database helpers\n\n\n"
            "def get_user(name):\n"
            "    return {'name': name}\n",
            encoding="utf-8",
        )
        (Path(tmp) / "README.md").write_text(
            "# My Project\n\nHandles login and auth flows.\n",
            encoding="utf-8",
        )
        (Path(tmp) / "data.json").write_text('{"key": "value"}\n', encoding="utf-8")

        engine = SmartContextEngine(root=tmp, cache_ttl=60.0)

        # 1) score_file
        s_auth = engine.score_file(str(pkg / "auth.py"), "fix login authentication bug")
        s_json = engine.score_file(str(Path(tmp) / "data.json"), "fix login authentication bug")
        print(f"score auth.py : {s_auth:.3f}")
        print(f"score data.json: {s_json:.3f}")
        assert s_auth > s_json, "code file matching query should outscore data file"

        # 2) rank_files
        all_files = [
            str(pkg / "auth.py"),
            str(pkg / "db.py"),
            str(Path(tmp) / "README.md"),
            str(Path(tmp) / "data.json"),
        ]
        ranked = engine.rank_files(all_files, "fix login authentication bug")
        print("ranked:", [(os.path.basename(r.path), round(r.total, 3)) for r in ranked])
        assert ranked[0].path.endswith("auth.py"), "auth.py should rank first"
        assert ranked[0].import_distance >= 0.0

        # 3) cache hit (second call uses TTL cache)
        t0 = time.perf_counter()
        engine.score_file(str(pkg / "auth.py"), "fix login authentication bug")
        t1 = time.perf_counter()
        print(f"cached score lookup: {(t1 - t0) * 1e3:.2f} ms")

        # 4) compression: comments stripped + long function truncated
        raw = (pkg / "auth.py").read_text(encoding="utf-8")
        compressed = engine.compress_content(str(pkg / "auth.py"), raw)
        assert "# login user" not in compressed, "comment should be stripped"
        assert "lines elided" in compressed, "long function should be elided"
        assert "def login(username, password):" in compressed, "signature kept"
        print(f"compression: {len(raw)} -> {len(compressed)} chars")

        # 5) build_context within token budget
        ctx = engine.build_context("fix login authentication bug", max_tokens=2000)
        total_tokens = sum(estimate_tokens(c, is_code=p.endswith(".py")) for p, c, _ in ctx)
        print(f"context: {len(ctx)} files, ~{total_tokens} tokens")
        for p, _c, sc in ctx:
            print(f"  {os.path.basename(p):12s} score={sc:.3f}")
        assert ctx, "context should not be empty"
        assert total_tokens <= 2000, "must respect token budget"

        # 6) prune_cache with expired TTL
        engine.cache_ttl = 0.0
        removed = engine.prune_cache()
        print(f"pruned {removed} expired cache entries")
        assert removed > 0

    print("=" * 50)
    print("All smoke tests passed.")
