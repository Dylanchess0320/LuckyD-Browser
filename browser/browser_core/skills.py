"""Bundled skills registry for the AI sidebar.

Reads ``skills/registry.json`` defensively — malformed entries are skipped,
never fatal — and exposes :func:`list_skills` plus :func:`match_skill` so the
sidebar can surface contextual suggestion chips. Skill docs (the ``.md``
files) are loaded lazily, path-traversal guarded, and integrity-checked
against the registry's sha256 when present.

No network access, no secrets, no execution: a skill only ever becomes a
system-message context for the normal chat flow, so permission scopes and
audit logging behave exactly as they do for any other chat message.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

REGISTRY_FILE = "registry.json"
DOC_BUDGET = 6000  # max chars of a skill doc attached as chat context

_WORD = re.compile(r"[a-z0-9]+")

# Common glue words that would otherwise match skill descriptions by accident
# ("the", "with", ...) and make every chip fire on unrelated input.
_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "what",
        "how",
        "why",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "whose",
        "are",
        "was",
        "were",
        "been",
        "this",
        "that",
        "these",
        "those",
        "from",
        "have",
        "has",
        "had",
        "having",
        "you",
        "your",
        "yours",
        "can",
        "could",
        "should",
        "would",
        "will",
        "shall",
        "just",
        "about",
        "into",
        "over",
        "after",
        "before",
        "between",
        "its",
        "it's",
        "but",
        "not",
        "all",
        "any",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "than",
        "too",
        "very",
        "per",
        "via",
        "etc",
        "out",
        "off",
        "there",
        "their",
        "then",
        "them",
        "they",
        "his",
        "her",
        "she",
        "him",
        "our",
        "ours",
        "does",
        "did",
        "doing",
        "done",
        "get",
        "got",
        "let",
        "like",
        "want",
        "need",
        "needs",
        "help",
        "please",
    }
)


@dataclass
class Skill:
    """One bundled skill from the registry."""

    name: str
    description: str = ""
    tags: tuple[str, ...] = ()
    version: str = ""
    author: str = ""
    url: str = ""
    sha256: str = ""
    # Directory the skill was loaded from (set by the loader, not the JSON).
    source_dir: Path | None = field(default=None, repr=False, compare=False)


def _candidate_dirs() -> list[Path]:
    """Where ``skills/`` may live: next to this package, or under the CWD."""
    here = Path(__file__).resolve()
    repo_root = here.parent.parent.parent  # <repo>/browser/browser_core/skills.py
    return [repo_root / "skills", Path.cwd() / "skills"]


def find_registry(path: str | Path | None = None) -> Path | None:
    """Locate registry.json without raising; None when it cannot be found."""
    if path is not None:
        p = Path(path)
        return p if p.is_file() else None
    for d in _candidate_dirs():
        p = d / REGISTRY_FILE
        if p.is_file():
            return p
    return None


def _parse_entry(raw: object) -> Skill | None:
    """Parse one registry entry; None when it is malformed (never raises)."""
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    tags = raw.get("tags") or []
    if not isinstance(tags, list):
        tags = []
    clean_tags = tuple(t.strip() for t in tags if isinstance(t, str) and t.strip())
    sha = raw.get("sha256")
    return Skill(
        name=name.strip(),
        description=str(raw.get("description") or ""),
        tags=clean_tags,
        version=str(raw.get("version") or ""),
        author=str(raw.get("author") or ""),
        url=str(raw.get("url") or ""),
        sha256=str(sha).lower() if isinstance(sha, str) else "",
    )


def load_registry(path: str | Path | None = None) -> list[Skill]:
    """Load the bundled skills. Malformed entries/JSON are skipped, never fatal."""
    reg = find_registry(path)
    if reg is None:
        return []
    try:
        data = json.loads(reg.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = data.get("skills") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return []
    skills: list[Skill] = []
    for raw in entries:
        skill = _parse_entry(raw)
        if skill is not None:
            skill.source_dir = reg.parent
            skills.append(skill)
    return skills


def list_skills(path: str | Path | None = None) -> list[Skill]:
    """All bundled skills, in registry order."""
    return load_registry(path)


def _keywords(text: str) -> set[str]:
    words = {w for w in _WORD.findall(text.lower()) if len(w) >= 3}
    return words - _STOPWORDS


def _singular(word: str) -> str:
    # Naive plural fold so "movie" matches the "movies" tag and vice versa.
    return word[:-1] if len(word) > 4 and word.endswith("s") else word


def _norm_words(words: set[str]) -> set[str]:
    out = set(words)
    out.update(_singular(w) for w in words)
    return out


def match_skill(text: str, skills: list[Skill] | None = None, limit: int = 3) -> list[Skill]:
    """Return up to ``limit`` skills whose name/tags/description match ``text``.

    Pure keyword scoring (name x3, tags x2, description x1); unrelated text
    scores zero and yields no matches. Deterministic — no model involved.
    """
    if not text or not text.strip():
        return []
    if skills is None:
        skills = list_skills()
    query = _norm_words(_keywords(text))
    if not query:
        return []
    scored: list[tuple[int, Skill]] = []
    for skill in skills:
        name_words = _norm_words(_keywords(skill.name.replace("-", " ")))
        tag_words = _norm_words(_keywords(" ".join(skill.tags)))
        desc_words = _norm_words(_keywords(skill.description))
        score = (
            3 * len(query & name_words) + 2 * len(query & tag_words) + 1 * len(query & desc_words)
        )
        if score > 0:
            scored.append((score, skill))
    scored.sort(key=lambda item: (-item[0], item[1].name))
    return [skill for _, skill in scored[: max(0, limit)]]


def load_skill_doc(skill: Skill, max_chars: int = DOC_BUDGET) -> str:
    """Read the skill's ``.md`` doc defensively.

    Returns "" when the doc is missing, unreadable, escapes the skills
    directory, or fails the registry sha256 check — the caller falls back to
    the skill's metadata instead of failing.
    """
    if not skill.url:
        return ""
    rel = Path(skill.url)
    if rel.is_absolute() or ".." in rel.parts:
        return ""
    base = skill.source_dir
    if base is None:
        for d in _candidate_dirs():
            if (d / rel).is_file():
                base = d
                break
    if base is None:
        return ""
    try:
        data = (base / rel).read_bytes()
    except OSError:
        return ""
    if skill.sha256 and hashlib.sha256(data).hexdigest() != skill.sha256:
        return ""
    return data.decode("utf-8", errors="replace")[:max_chars]


def skill_system_prompt(skill: Skill) -> str:
    """System-message context attached when the user picks a skill chip.

    The full doc when it loads cleanly; otherwise the skill's metadata, so a
    tampered or missing doc degrades gracefully instead of breaking the chat.
    """
    header = f"Active skill: {skill.name}"
    if skill.version:
        header += f" (v{skill.version})"
    if skill.description:
        header += f" — {skill.description}"
    doc = load_skill_doc(skill)
    if doc:
        return header + "\n\nFollow this skill's instructions for the user's request:\n\n" + doc
    fallback = header
    if skill.tags:
        fallback += "\nTags: " + ", ".join(skill.tags)
    return fallback + "\nUse this skill's domain expertise for the user's request."


def skill_chip_label(skill: Skill) -> str:
    """Short label rendered on the sidebar's suggestion chip."""
    return f"✨ {skill.name}"
