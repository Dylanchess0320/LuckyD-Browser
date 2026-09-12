"""Skills registry wiring: loader, matcher, and the AI-sidebar chip path."""

from __future__ import annotations

import json
from pathlib import Path

from browser.browser_core.skills import (
    Skill,
    find_registry,
    list_skills,
    load_registry,
    load_skill_doc,
    match_skill,
    skill_chip_label,
    skill_system_prompt,
)

EXPECTED = {"ai-news-brief", "chess", "graphify", "movie-picker", "top-picks"}


def test_registry_loads_all_bundled_skills() -> None:
    skills = list_skills()
    assert {s.name for s in skills} == EXPECTED
    for s in skills:
        assert s.description, s.name
        assert s.url.endswith(".md"), s.name
        assert s.source_dir is not None


def test_registry_missing_is_not_fatal(tmp_path: Path) -> None:
    assert load_registry(tmp_path / "nope.json") == []


def test_registry_malformed_json_is_not_fatal(tmp_path: Path) -> None:
    p = tmp_path / "registry.json"
    p.write_text("{not valid json", encoding="utf-8")
    assert load_registry(p) == []


def test_malformed_entries_are_skipped(tmp_path: Path) -> None:
    p = tmp_path / "registry.json"
    p.write_text(
        json.dumps(
            {
                "skills": [
                    {"name": "good", "description": "a fine skill", "tags": ["x"]},
                    {"description": "no name — skip me"},
                    {"name": "   "},
                    {"name": 42},
                    "just a string",
                    None,
                    {"name": "bad-tags", "tags": "not-a-list"},
                ]
            }
        ),
        encoding="utf-8",
    )
    skills = load_registry(p)
    names = {s.name for s in skills}
    assert names == {"good", "bad-tags"}
    assert next(s for s in skills if s.name == "bad-tags").tags == ()


def test_match_skill_obvious_queries() -> None:
    assert match_skill("analyze my chess game")[0].name == "chess"
    top = match_skill("recommend a movie for tonight")
    assert top and top[0].name == "movie-picker"
    assert match_skill("give me the ai news briefing")[0].name == "ai-news-brief"


def test_match_skill_unrelated_text_matches_nothing() -> None:
    assert match_skill("how do I change my desktop wallpaper") == []
    assert match_skill("") == []
    assert match_skill("   ") == []
    assert match_skill("the and for with") == []  # stopwords only


def test_match_skill_limit_and_determinism() -> None:
    skills = list_skills()
    assert len(match_skill("ai", skills, limit=1)) <= 1
    first = [s.name for s in match_skill("chess analysis", skills)]
    second = [s.name for s in match_skill("chess analysis", skills)]
    assert first == second


def test_skill_doc_loads_with_hash_check() -> None:
    chess = next(s for s in list_skills() if s.name == "chess")
    doc = load_skill_doc(chess)
    assert doc, "bundled chess.md should load"
    assert len(doc) <= 6000


def test_skill_doc_rejects_path_traversal() -> None:
    evil = Skill(name="evil", url="../../etc/passwd")
    assert load_skill_doc(evil) == ""
    evil_abs = Skill(name="evil", url="/etc/passwd")
    assert load_skill_doc(evil_abs) == ""


def test_skill_system_prompt_attaches_doc_or_falls_back() -> None:
    chess = next(s for s in list_skills() if s.name == "chess")
    prompt = skill_system_prompt(chess)
    assert "chess" in prompt.lower()
    assert len(prompt) > len(chess.description)  # doc attached, not just metadata
    # Missing doc degrades to metadata instead of raising.
    missing = Skill(name="ghost", description="boo", tags=("spooky",), url="nope.md")
    fallback = skill_system_prompt(missing)
    assert "ghost" in fallback and "spooky" in fallback


def test_skill_chip_label() -> None:
    assert skill_chip_label(Skill(name="chess")) == "✨ chess"


def test_sidebar_chip_routing_path_exists() -> None:
    """The chip wiring must be defined on AiSidebar.

    Static check (no Qt needed): the chip handlers exist on the class, the
    chip click routes through the existing ``_send`` path (no new execution
    pipeline), and input changes drive chip updates.
    """
    import ast

    src_path = Path(__file__).resolve().parent.parent / "browser" / "browser_ui" / "ai_sidebar.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "AiSidebar")
    methods = {
        n.name: n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for required in (
        "_update_skill_chips",
        "_use_skill",
        "_set_skill_chips_visible",
        "_send",
        "_start_chat",
        "ask",
    ):
        assert required in methods, required
    use_skill_src = ast.get_source_segment(src, methods["_use_skill"]) or ""
    assert "self._send()" in use_skill_src  # no parallel execution pipeline
    init_src = ast.get_source_segment(src, methods["__init__"]) or ""
    assert "_update_skill_chips" in init_src  # input drives chips
    assert "_pending_skill" in init_src
    start_chat_src = ast.get_source_segment(src, methods["_start_chat"]) or ""
    assert "_pending_skill" in start_chat_src  # skill context enters the chat


def test_find_registry_points_at_bundled_file() -> None:
    reg = find_registry()
    assert reg is not None and reg.is_file()
    assert reg.name == "registry.json"
