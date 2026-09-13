"""Night-5 tests: core/smart_context.py (was ~0% covered).

File scoring, ranking, compression (comment stripping, long-function
truncation, head+tail), token-budget context assembly, import-graph bonus,
and the score cache lifecycle — all on synthetic project trees.
"""

from __future__ import annotations

import os
import time

import pytest

from core.smart_context import FileScore, SmartContextEngine


@pytest.fixture()
def proj(tmp_path):
    pkg = tmp_path / "mypkg"
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
        "# database helpers\n\n\ndef get_user(name):\n    return {'name': name}\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        "# My Project\n\nHandles login and auth flows.\n", encoding="utf-8"
    )
    (tmp_path / "data.json").write_text('{"key": "value"}\n', encoding="utf-8")
    (tmp_path / "notes.txt").write_text("unrelated shopping list\n", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def engine(proj):
    return SmartContextEngine(root=str(proj), cache_ttl=60.0)


# ── scoring ──────────────────────────────────────────────────────────────


def test_score_file_relevant_beats_irrelevant(engine, proj):
    q = "fix login authentication bug"
    s_auth = engine.score_file(str(proj / "mypkg" / "auth.py"), q)
    s_json = engine.score_file(str(proj / "data.json"), q)
    assert s_auth > s_json
    assert 0.0 <= s_json <= 1.6


def test_score_file_cached_second_call(engine, proj):
    p = str(proj / "mypkg" / "auth.py")
    first = engine.score_file(p, "login bug")
    second = engine.score_file(p, "login bug")
    assert first == second
    # A different query scores independently
    other = engine.score_file(p, "database schema migration")
    assert isinstance(other, float)


def test_score_file_cache_invalidation_on_change(engine, proj):
    p = str(proj / "notes.txt")
    norm = engine._norm_path(p)
    before = engine.score_file(p, "shopping list groceries")
    assert before > 0
    fp_before = engine._cache[(norm, "shopping list groceries")].fingerprint
    time.sleep(0.01)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write("more groceries and shopping items\n")
    engine.score_file(p, "shopping list groceries")  # recompute after change
    # fingerprint (mtime+size) changed -> cache entry was recomputed, not stale
    fp_after = engine._cache[(norm, "shopping list groceries")].fingerprint
    assert fp_after != fp_before
    # force a visible change by appending query terms
    with open(p, "a", encoding="utf-8") as fh:
        fh.write("shopping " * 20 + "\n")
    boosted = engine.score_file(p, "shopping list groceries")
    assert boosted >= before


def test_score_missing_file_is_stable(engine, proj):
    # missing file: no keyword/path/recency signal, only the .py type weight
    score = engine.score_file(str(proj / "nope.py"), "anything")
    assert isinstance(score, float)
    assert score == pytest.approx(0.15)  # w_type * default .py weight
    assert engine.score_file(str(proj / "nope.py"), "anything") == score  # cached


def test_rank_files_sorted_descending(engine, proj):
    paths = [
        str(proj / "data.json"),
        str(proj / "mypkg" / "auth.py"),
        str(proj / "README.md"),
        str(proj / "mypkg" / "db.py"),
    ]
    ranked = engine.rank_files(paths, "fix login authentication bug")
    assert isinstance(ranked[0], FileScore)
    totals = [r.total for r in ranked]
    assert totals == sorted(totals, reverse=True)
    assert ranked[0].path.endswith("auth.py")


def test_import_distance_bonus_for_imported_module(engine, proj):
    # db.py is imported by auth.py (strong keyword match) -> import bonus > 0
    q = "fix login authentication bug"
    ranked = {
        r.path: r
        for r in engine.rank_files([str(proj / "mypkg" / "db.py"), str(proj / "data.json")], q)
    }
    db_score = ranked[str(proj / "mypkg" / "db.py")]
    assert db_score.import_distance > 0.0
    assert ranked[str(proj / "data.json")].import_distance == 0.0


def test_query_terms_stopwords_and_short_tokens():
    terms = SmartContextEngine._query_terms("Fix the login bug in auth!")
    assert "the" not in terms
    assert "in" not in terms
    assert "fix" in terms and "login" in terms and "bug" in terms and "auth" in terms


def test_path_score_matches_path_segments(engine, proj):
    score = engine._path_score(str(proj / "mypkg" / "auth.py"), {"auth"})
    assert score == 1.0
    assert engine._path_score(str(proj / "data.json"), {"auth"}) == 0.0


def test_recency_score_fresh_file_high(engine, proj):
    p = str(proj / "notes.txt")
    fresh = engine._recency_score(p)
    assert 0.9 < fresh <= 1.0
    assert engine._recency_score(str(proj / "missing.txt")) == 0.0


def test_keyword_score_empty_inputs():
    e = SmartContextEngine(root=".")
    assert e._keyword_score("a.py", "", {"x"}) == 0.0
    assert e._keyword_score("a.py", "content", set()) == 0.0


# ── compression ──────────────────────────────────────────────────────────


def test_compress_strips_comments_and_truncates_long_function(engine, proj):
    raw = (proj / "mypkg" / "auth.py").read_text(encoding="utf-8")
    out = engine.compress_content(str(proj / "mypkg" / "auth.py"), raw)
    assert "# login user" not in out
    assert "def login(username, password):" in out
    assert "lines elided" in out
    assert len(out) < len(raw)


def test_compress_head_tail_for_huge_file(engine, proj):
    big = "\n".join(f"line {i}" for i in range(500))
    out = engine.compress_content(str(proj / "big.py"), big)
    assert "line 0" in out
    assert "line 499" in out
    assert "elided" in out


def test_compress_non_code_untouched(engine, proj):
    raw = "# not a comment in markdown\n\n\ntext\n"
    out = engine.compress_content(str(proj / "README.md"), raw)
    assert "# not a comment in markdown" in out  # md has no line-comment syntax
    assert "\n\n\n" not in out  # blank runs still stripped


def test_strip_blank_runs():
    e = SmartContextEngine(root=".")
    assert e._strip_blank_runs("a\n\n\n\nb") == "a\nb"
    assert e._strip_blank_runs("a\nb") == "a\nb"


def test_truncate_to_budget_respects_budget(engine, proj):
    from core.context_manager import estimate_tokens

    content = "word " * 5000
    out = engine._truncate_to_budget(str(proj / "notes.txt"), content, 50)
    assert estimate_tokens(out) <= 60  # head+tail overhead allowed
    assert "truncated" in out or "elided" in out


# ── build_context ────────────────────────────────────────────────────────


def test_build_context_respects_token_budget(engine, proj):
    from core.context_manager import estimate_tokens

    ctx = engine.build_context("fix login authentication bug", max_tokens=2000)
    assert ctx
    total = sum(estimate_tokens(c, is_code=p.endswith(".py")) for p, c, _ in ctx)
    assert total <= 2000
    paths = [p for p, _, _ in ctx]
    assert any(p.endswith("auth.py") for p in paths)


def test_build_context_tiny_budget_still_returns_something(engine, proj):
    ctx = engine.build_context("login", max_tokens=10)
    assert isinstance(ctx, list)  # may be empty, must not crash


def test_build_context_scores_descending(engine, proj):
    ctx = engine.build_context("fix login authentication bug", max_tokens=8000)
    scores = [s for _, _, s in ctx]
    assert scores == sorted(scores, reverse=True)


def test_walk_project_skips_junk_dirs(engine, proj):
    junk = proj / ".git"
    junk.mkdir()
    (junk / "secret.py").write_text("x = 1\n", encoding="utf-8")
    walked = engine._walk_project()
    assert not any(".git" in p for p in walked)
    assert any(p.endswith("auth.py") for p in walked)


def test_norm_path_relative_and_absolute(engine, proj):
    rel = engine._norm_path("mypkg/auth.py")
    assert rel == os.path.normpath(os.path.join(str(proj), "mypkg", "auth.py"))
    abs_p = str(proj / "mypkg" / "auth.py")
    assert engine._norm_path(abs_p) == os.path.normpath(abs_p)


# ── cache lifecycle ──────────────────────────────────────────────────────


def test_clear_cache_drops_scores_and_graph(engine, proj):
    engine.score_file(str(proj / "mypkg" / "auth.py"), "login")
    engine._ensure_import_graph()
    assert engine._cache
    engine.clear_cache()
    assert engine._cache == {}
    assert engine._import_graph == {}


def test_prune_cache_removes_expired(engine, proj):
    engine.score_file(str(proj / "mypkg" / "auth.py"), "login")
    assert engine._cache
    engine.cache_ttl = 0.0
    removed = engine.prune_cache()
    assert removed >= 1
    assert engine._cache == {}
