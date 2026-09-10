"""
Skills marketplace — discover, review, and install community skills safely.

A skill is a Markdown file with YAML frontmatter (the format tools/skill_tools
already understands). A *registry* is a tiny open JSON index anyone can host
(a GitHub repo, a gist, a local file):

    {"registry": "my-skills", "skills": [
       {"name": "...", "description": "...", "version": "1.0", "author": "...",
        "tags": ["..."], "url": "https://.../my-skill.md", "sha256": "..."}]}

Safety model:
- Installing is a two-step flow: SkillFetch (read-only: downloads the skill,
  verifies its sha256 against the registry when present) returns a review —
  name, version, author, source, tools it needs, and a content preview.
  SkillInstall(fetch_id) then writes it, and only that step needs approval.
- Every install records provenance (registry, URL, hash, timestamp) so
  SkillUpdate can detect tampering or newer versions.
- Installed skills live in ~/.luckyd/skills (never in the repo tree).
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# ── skill parsing (shared with tools/skill_tools) ─────────────────────────

try:
    from tools.skill_tools import _parse_skill as _parse_skill_file
except Exception:  # pragma: no cover — import cycle guard
    _parse_skill_file = None


def user_skills_dir() -> Path:
    from core.trust import _data_dir

    d = _data_dir() / "skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


def parse_skill_markdown(text: str, filename: str = "<remote>") -> dict[str, Any] | None:
    """Parse skill markdown text (same frontmatter rules as local skills)."""
    import re

    try:
        import yaml
    except ImportError:
        yaml = None
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
    if not m or yaml is None:
        return None
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except Exception:
        return None
    return {
        "name": str(fm.get("name", Path(filename).stem)),
        "description": str(fm.get("description", "")),
        "version": str(fm.get("version", "1.0")),
        "author": str(fm.get("author", "unknown")),
        "tags": list(fm.get("tags", []) or []),
        "requires_tools": list(fm.get("requires_tools", []) or []),
        "prompt": m.group(2).strip(),
        "file": filename,
    }


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── data types ────────────────────────────────────────────────────────────


@dataclass
class SkillPackage:
    name: str
    description: str
    version: str
    author: str
    tags: list[str] = field(default_factory=list)
    requires_tools: list[str] = field(default_factory=list)
    prompt: str = ""
    markdown: str = ""
    sha256: str = ""
    source_name: str = ""
    source_url: str = ""
    registry_sha256: str = ""  # hash the registry advertised ("" if none)

    @property
    def verified(self) -> bool:
        return bool(self.registry_sha256) and self.registry_sha256 == self.sha256


@dataclass
class RegistrySource:
    name: str
    url: str  # http(s)://... or file path to a registry.json

    @property
    def is_remote(self) -> bool:
        return urlparse(self.url).scheme in ("http", "https")


# ── registry fetching ─────────────────────────────────────────────────────


def _read_text_location(url: str, base_dir: Path | None = None, timeout: float = 15.0) -> str:
    scheme = urlparse(url).scheme
    if scheme in ("http", "https"):
        try:
            import httpx
        except ImportError as e:
            raise RuntimeError("httpx is not installed — cannot fetch remote registries") from e
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        return resp.text
    # local file: absolute path, file:// URL, or relative to the registry file
    if scheme == "file":
        path = Path(urlparse(url).path)
    else:
        path = Path(url)
        if not path.is_absolute() and base_dir is not None:
            path = base_dir / path
    return path.read_text(encoding="utf-8")


def fetch_registry(source: RegistrySource) -> list[dict[str, Any]]:
    """Return the raw skill entries advertised by a registry."""
    import json as _json

    text = _read_text_location(source.url)
    data = _json.loads(text)
    skills = data.get("skills", [])
    if not isinstance(skills, list):
        raise ValueError(f"registry {source.name!r}: 'skills' must be a list")
    base_dir = None
    if not source.is_remote:
        p = Path(urlparse(source.url).path or source.url)
        base_dir = p.parent if p.is_absolute() else Path(source.url).parent
    for entry in skills:
        entry["_base_dir"] = str(base_dir) if base_dir else ""
    return skills


def fetch_package(entry: dict[str, Any], source: RegistrySource) -> SkillPackage:
    """Download a skill entry and verify integrity. Raises on mismatch."""
    url = entry.get("url", "")
    base_dir = Path(entry["_base_dir"]) if entry.get("_base_dir") else None
    markdown = _read_text_location(url, base_dir)
    parsed = parse_skill_markdown(markdown, filename=entry.get("name", "<remote>"))
    if not parsed:
        raise ValueError(f"could not parse skill markdown from {url!r}")
    digest = sha256_text(markdown)
    advertised = str(entry.get("sha256", "") or "")
    if advertised and advertised != digest:
        raise ValueError(
            f"INTEGRITY FAILURE for skill {parsed['name']!r}: registry hash {advertised[:12]}… "
            f"does not match downloaded content {digest[:12]}… — refusing to install."
        )
    return SkillPackage(
        name=parsed["name"],
        description=parsed["description"],
        # The registry entry is the version authority (update checks compare
        # entry versions); frontmatter is the fallback for bare downloads.
        version=str(entry.get("version") or parsed["version"]),
        author=parsed["author"],
        tags=parsed["tags"],
        requires_tools=parsed["requires_tools"],
        prompt=parsed["prompt"],
        markdown=markdown,
        sha256=digest,
        source_name=source.name,
        source_url=url,
        registry_sha256=advertised,
    )


def review_text(pkg: SkillPackage) -> str:
    integrity = (
        "✅ hash verified against registry"
        if pkg.verified
        else "⚠️ registry published no hash — review carefully"
    )
    tools = ", ".join(pkg.requires_tools) if pkg.requires_tools else "none declared"
    preview = pkg.markdown[:1200]
    return (
        f"📦 {pkg.name} v{pkg.version} by {pkg.author}\n"
        f"{pkg.description}\n"
        f"Source: {pkg.source_name} ({pkg.source_url})\n"
        f"Integrity: {integrity}\n"
        f"Tags: {', '.join(pkg.tags) or '—'} · Needs tools: {tools}\n"
        f"sha256: {pkg.sha256[:16]}…\n"
        f"── preview ──\n{preview}" + ("…" if len(pkg.markdown) > 1200 else "")
    )


# ── SQLite store: sources + installed manifest ────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (name TEXT PRIMARY KEY, url TEXT NOT NULL, added_at TEXT);
CREATE TABLE IF NOT EXISTS installed (
    name TEXT PRIMARY KEY, version TEXT, author TEXT,
    source_name TEXT, source_url TEXT, sha256 TEXT,
    installed_at TEXT, path TEXT
);
"""


class MarketplaceStore:
    def __init__(self, path: Path | None = None):
        from core.trust import _data_dir

        self.path = Path(path) if path else _data_dir() / "marketplace.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
        self._ensure_bundled_source()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_bundled_source(self) -> None:
        bundled = Path(__file__).resolve().parent.parent / "skills" / "registry.json"
        if bundled.exists():
            with self._lock, self._connect() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO sources (name,url,added_at) VALUES (?,?,?)",
                    ("bundled", str(bundled), _now_iso()),
                )

    # sources
    def add_source(self, name: str, url: str) -> None:
        if not name.strip():
            raise ValueError("source name is required")
        # Validate the URL/registry eagerly so typos fail fast (raises if unreachable).
        fetch_registry(RegistrySource(name.strip(), url.strip()))
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO sources (name,url,added_at) VALUES (?,?,?)",
                (name.strip(), url.strip(), _now_iso()),
            )

    def remove_source(self, name: str) -> bool:
        if name == "bundled":
            raise ValueError("the bundled source cannot be removed")
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM sources WHERE name=?", (name,))
            return cur.rowcount > 0

    def sources(self) -> list[RegistrySource]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT name,url FROM sources ORDER BY name").fetchall()
        return [RegistrySource(r["name"], r["url"]) for r in rows]

    # installed manifest
    def record_install(self, pkg: SkillPackage, path: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO installed
                   (name,version,author,source_name,source_url,sha256,installed_at,path)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    pkg.name,
                    pkg.version,
                    pkg.author,
                    pkg.source_name,
                    pkg.source_url,
                    pkg.sha256,
                    _now_iso(),
                    path,
                ),
            )

    def record_remove(self, name: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM installed WHERE name=?", (name,))

    def installed(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM installed ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def get_installed(self, name: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM installed WHERE name=?", (name,)).fetchone()
        return dict(row) if row else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── install / remove ──────────────────────────────────────────────────────


def install_package(pkg: SkillPackage, store: MarketplaceStore) -> Path:
    """Write the skill into the user skills dir and record provenance."""
    target = user_skills_dir() / f"{pkg.name}.md"
    target.write_text(pkg.markdown, encoding="utf-8")
    store.record_install(pkg, str(target))
    return target


def remove_installed(name: str, store: MarketplaceStore) -> bool:
    manifest = store.get_installed(name)
    path = Path(manifest["path"]) if manifest else user_skills_dir() / f"{name}.md"
    existed = path.exists()
    if existed:
        path.unlink()
    store.record_remove(name)
    return existed


# ── pending-fetch cache (two-step install) ────────────────────────────────

_pending: dict[str, SkillPackage] = {}
_pending_lock = threading.Lock()


def stash_pending(pkg: SkillPackage) -> str:
    fid = uuid.uuid4().hex[:8]
    with _pending_lock:
        _pending[fid] = pkg
    return fid


def take_pending(fetch_id: str) -> SkillPackage | None:
    with _pending_lock:
        return _pending.pop(fetch_id, None)


# ── publishing ────────────────────────────────────────────────────────────


def publish_pack(skill_name: str, skills_dir: Path | None = None) -> dict[str, Any]:
    """Build a shareable pack for a local skill: folder with the .md plus the
    registry JSON entry the author pastes into their hosted registry.json."""
    import json as _json

    if skills_dir is None:
        from tools.skill_tools import SKILLS_DIR

        skills_dir = SKILLS_DIR
    src = skills_dir / f"{skill_name}.md"
    if not src.exists():
        # also try the user dir
        alt = user_skills_dir() / f"{skill_name}.md"
        src = alt if alt.exists() else src
    if not src.exists():
        raise ValueError(f"skill {skill_name!r} not found")
    markdown = src.read_text(encoding="utf-8")
    parsed = parse_skill_markdown(markdown, src.name)
    if not parsed:
        raise ValueError(f"could not parse {src}")
    from core.trust import _data_dir

    pack_dir = _data_dir() / "skill-packs" / parsed["name"]
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / f"{parsed['name']}.md").write_text(markdown, encoding="utf-8")
    entry = {
        "name": parsed["name"],
        "description": parsed["description"],
        "version": parsed["version"],
        "author": parsed["author"],
        "tags": parsed["tags"],
        "url": f"https://YOUR-HOST/{parsed['name']}.md",
        "sha256": sha256_text(markdown),
    }
    (pack_dir / "registry-entry.json").write_text(_json.dumps(entry, indent=2), encoding="utf-8")
    return {"pack_dir": str(pack_dir), "entry": entry, "skill": parsed["name"]}
