"""Night-1 browser-core audit: skills.py (registry, matching, doc loading)
and research_page.py SwarmManager (run-id traversal guard, run storage)."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from browser_core.skills import (
    Skill,
    find_registry,
    list_skills,
    load_registry,
    load_skill_doc,
    match_skill,
    skill_chip_label,
    skill_system_prompt,
)


def _registry(tmp_path: Path, entries: list[dict]) -> Path:
    d = tmp_path / "skills"
    d.mkdir()
    (d / "registry.json").write_text(json.dumps({"skills": entries}), encoding="utf-8")
    return d / "registry.json"


def _entries():
    return [
        {
            "name": "flights",
            "description": "Book cheap flights fast",
            "tags": ["travel", "booking"],
        },
        {
            "name": "movies",
            "description": "Find movie showtimes",
            "tags": ["entertainment", "movies"],
        },
        "not-a-dict",
        {"name": "   "},  # blank name → malformed
        {"description": "no name at all"},
    ]


# ── registry loading ─────────────────────────────────────────────────


def test_load_registry_skips_malformed(tmp_path: Path) -> None:
    reg = _registry(tmp_path, _entries())
    skills = load_registry(reg)
    assert [s.name for s in skills] == ["flights", "movies"]
    assert skills[0].source_dir == reg.parent
    assert skills[0].tags == ("travel", "booking")


def test_load_registry_missing_or_broken_is_empty(tmp_path: Path) -> None:
    assert load_registry(tmp_path / "nope.json") == []
    bad = tmp_path / "bad.json"
    bad.write_text("{oops", encoding="utf-8")
    assert load_registry(bad) == []
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"skills": "nope"}), encoding="utf-8")
    assert load_registry(wrong) == []


def test_find_registry_none_when_absent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("browser_core.skills._candidate_dirs", lambda: [tmp_path / "x"])
    assert find_registry() is None
    assert find_registry(tmp_path / "missing.json") is None


def test_list_skills_uses_found_registry(tmp_path: Path, monkeypatch) -> None:
    _registry(tmp_path, _entries())
    monkeypatch.setattr("browser_core.skills._candidate_dirs", lambda: [tmp_path / "skills"])
    assert [s.name for s in list_skills()] == ["flights", "movies"]


# ── matching ─────────────────────────────────────────────────────────


def _skills():
    return [
        Skill(name="flights", description="book cheap flights", tags=("travel",)),
        Skill(name="movies", description="movie showtimes", tags=("entertainment",)),
    ]


def test_match_skill_name_beats_description() -> None:
    # "flights" hits the name (x3); "booking" would only hit a tag (x2)
    res = match_skill("i need flights booked", _skills())
    assert [s.name for s in res] == ["flights"]
    # description-only hit still matches
    assert [s.name for s in match_skill("showtimes near me", _skills())] == ["movies"]


def test_match_skill_empty_and_unrelated() -> None:
    assert match_skill("", _skills()) == []
    assert match_skill("   ", _skills()) == []
    assert match_skill("quantum chromodynamics homework", _skills()) == []
    assert match_skill("the and with you", _skills()) == []  # stopwords only


def test_match_skill_limit_and_plural_fold() -> None:
    res = match_skill("movies", _skills(), limit=1)
    assert [s.name for s in res] == ["movies"]
    assert match_skill("movie", _skills(), limit=0) == []  # limit 0 → no matches
    # naive plural fold: "movie" matches the "movies" name
    assert [s.name for s in match_skill("movie", _skills())] == ["movies"]


# ── doc loading ──────────────────────────────────────────────────────


def _skill_with_doc(tmp_path: Path, content: bytes, **kw) -> Skill:
    d = tmp_path / "skills"
    d.mkdir(exist_ok=True)
    (d / "doc.md").write_text("", encoding="utf-8")
    (d / "doc.md").write_bytes(content)
    sha = kw.pop("sha256", hashlib.sha256(content).hexdigest())
    return Skill(name="s", url="doc.md", sha256=sha, source_dir=d, **kw)


def test_load_skill_doc_ok_and_budget(tmp_path: Path) -> None:
    s = _skill_with_doc(tmp_path, b"hello doc")
    assert load_skill_doc(s) == "hello doc"
    assert len(load_skill_doc(s, max_chars=5)) == 5


def test_load_skill_doc_guards(tmp_path: Path) -> None:
    d = tmp_path / "skills"
    d.mkdir()
    assert load_skill_doc(Skill(name="a", url="", source_dir=d)) == ""  # no url
    assert load_skill_doc(Skill(name="b", url="/etc/passwd", source_dir=d)) == ""
    assert load_skill_doc(Skill(name="c", url="../escape.md", source_dir=d)) == ""
    assert load_skill_doc(Skill(name="d", url="missing.md", source_dir=d)) == ""
    # tampered doc fails the sha check
    s = _skill_with_doc(tmp_path, b"tampered", sha256="00" * 32)
    assert load_skill_doc(s) == ""


def test_skill_system_prompt_fallback(tmp_path: Path) -> None:
    d = tmp_path / "skills"
    d.mkdir()
    s = Skill(name="flights", description="book flights", tags=("travel",), source_dir=d)
    prompt = skill_system_prompt(s)
    assert "Active skill: flights" in prompt
    assert "Tags: travel" in prompt
    assert "Use this skill's domain expertise" in prompt
    # with a real doc, the doc is attached
    s2 = _skill_with_doc(tmp_path, b"STEP ONE")
    assert "STEP ONE" in skill_system_prompt(s2)
    assert skill_chip_label(s2) == "✨ s"
