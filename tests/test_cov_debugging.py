"""Coverage push for core/advanced_debugging.py — visitor edge branches,
traceback parsing edges, _read_local_vars paths, _build_snippet branches,
analyze_performance, and session persistence edges."""

from __future__ import annotations

import json

import pytest

from core.advanced_debugging import (
    AdvancedDebugging,
    FixSuggestion,
    ParsedTraceback,
)


@pytest.fixture
def dbg(tmp_path):
    return AdvancedDebugging(persist_dir=tmp_path / "dbgmem")


# ── AST visitor edge branches ────────────────────────────────────────────


class TestVisitorEdges:
    def test_typed_except_not_flagged(self, dbg):
        issues = dbg.predict_errors("try:\n    pass\nexcept ValueError:\n    pass\n")
        assert [i for i in issues if i.issue_type == "bare_except"] == []

    def test_chained_comparison_not_flagged(self, dbg):
        issues = dbg.predict_errors("a = 1\nb = 2\nc = 3\nif a < b < c:\n    pass\n")
        assert [i for i in issues if i.issue_type == "none_comparison"] == []

    def test_compare_against_non_none_not_flagged(self, dbg):
        issues = dbg.predict_errors("x = 1\nif x == 1:\n    pass\n")
        assert [i for i in issues if i.issue_type == "none_comparison"] == []

    def test_subscript_nodes_do_not_crash(self, dbg):
        issues = dbg.predict_errors('d = {}\nv = d["k"]\nw = make()[0]\n')
        assert isinstance(issues, list)


# ── traceback parsing edges ─────────────────────────────────────────────


def _tb(*lines: str) -> str:
    return "\n".join(lines)


class TestParseEdges:
    def test_truncated_traceback_no_exception_line(self, dbg):
        # Ends with a File line: the reversed scan never finds an exception
        # line, exercising the loop-continue branch.
        text = _tb(
            "Traceback (most recent call last):",
            '  File "a.py", line 1, in <module>',
        )
        parsed = dbg._parse_traceback(text)
        assert parsed.exception_type == "UnknownError"
        assert len(parsed.frames) == 1

    def test_frame_without_code_context(self, dbg):
        text = _tb(
            "Traceback (most recent call last):",
            '  File "a.py", line 1, in <module>',
            '  File "b.py", line 2, in f',
            "ValueError: bad",
        )
        parsed = dbg._parse_traceback(text)
        assert len(parsed.frames) == 2
        assert parsed.frames[0].code_context == ""
        assert parsed.frames[1].code_context == "ValueError: bad"

    def test_read_local_vars_syntax_error_file(self, dbg, tmp_path):
        bad = tmp_path / "broken.py"
        bad.write_text("def broken(:\n", encoding="utf-8")
        text = _tb(
            "Traceback (most recent call last):",
            f'  File "{bad}", line 1, in broken',
            "SyntaxError: invalid syntax",
        )
        parsed = dbg._parse_traceback(text)
        assert parsed.frames[0].local_vars == {}

    def test_read_local_vars_multi_target_assign(self, dbg, tmp_path):
        # `obj.attr` is not a Name target: the isinstance check fails and the
        # target loop continues, covering the loop-back branch.
        src = tmp_path / "multi.py"
        src.write_text("def f():\n    obj.attr = other = 1\n    return other\n", encoding="utf-8")
        text = _tb(
            "Traceback (most recent call last):",
            f'  File "{src}", line 3, in f',
            "    return other",
            "ValueError: bad",
        )
        parsed = dbg._parse_traceback(text)
        assert parsed.frames[0].local_vars == {"other": "1"}

    def test_read_local_vars_module_level_line(self, dbg, tmp_path):
        src = tmp_path / "modlevel.py"
        src.write_text("X = 1\nprint(X)\n", encoding="utf-8")
        text = _tb(
            "Traceback (most recent call last):",
            f'  File "{src}", line 2, in <module>',
            "    print(X)",
            "ValueError: bad",
        )
        parsed = dbg._parse_traceback(text)
        assert parsed.frames[0].local_vars == {}

    def test_rank_frames_stdlib_between(self, dbg):
        from core.advanced_debugging import StackFrame

        frames = [
            StackFrame("/usr/lib/python3.12/site-packages/app/db.py", 1, "f", ""),
            StackFrame("/usr/lib/python3.12/os.py", 10, "g", ""),
            StackFrame("/home/dylan/proj/main.py", 5, "h", ""),
        ]
        ranked = dbg._rank_frames(frames)
        assert [f.function_name for f in ranked] == ["h", "g", "f"]


# ── suggest_fixes / _build_snippet branches ──────────────────────────────


class TestSuggestFixBranches:
    def test_importerror_without_module_match_falls_through(self, dbg):
        text = _tb(
            "Traceback (most recent call last):",
            '  File "a.py", line 1, in <module>',
            "    from x import y",
            "ImportError: cannot import name 'y' from 'x'",
        )
        fixes = dbg.suggest_fixes(text)
        assert len(fixes) == 1
        assert fixes[0].confidence == 0.3
        assert "try/except" in fixes[0].description

    def test_attribute_error_snippet(self, dbg):
        text = _tb(
            "Traceback (most recent call last):",
            '  File "a.py", line 1, in <module>',
            "    obj.bar",
            "AttributeError: 'Foo' object has no attribute 'bar'",
        )
        fixes = dbg.suggest_fixes(text)
        assert any("if obj is not None" in f.code_snippet for f in fixes)

    def test_type_error_snippet(self, dbg):
        text = _tb(
            "Traceback (most recent call last):",
            '  File "a.py", line 1, in <module>',
            "    x + y",
            "TypeError: unsupported operand type(s)",
        )
        fixes = dbg.suggest_fixes(text)
        assert any("explicit conversion" in f.code_snippet for f in fixes)

    def test_file_not_found_snippet(self, dbg):
        # NOTE: suggest_fixes is called with a ParsedTraceback because the
        # traceback parser skips any line starting with "File" — including
        # the "FileNotFoundError: ..." exception line itself — so a raw
        # string never yields exception_type == "FileNotFoundError".
        parsed = ParsedTraceback(
            exception_type="FileNotFoundError",
            exception_message="[Errno 2] No such file or directory: 'data.csv'",
            frames=[],
            raw_text="",
        )
        fixes = dbg.suggest_fixes(parsed)
        assert any("Path(__file__).parent" in f.code_snippet for f in fixes)

    def test_unmatched_exception_type_snippet_fallback(self, dbg):
        text = _tb(
            "Traceback (most recent call last):",
            '  File "a.py", line 1, in <module>',
            "    1 / 0",
            "ZeroDivisionError: division by zero",
        )
        fixes = dbg.suggest_fixes(text)
        assert fixes
        assert fixes[0].code_snippet == "# Review the error line and add appropriate guards."


# ── analyze_performance ──────────────────────────────────────────────────


class TestAnalyzePerformance:
    def test_profiles_simple_code(self, dbg):
        report = dbg.analyze_performance("total = sum(range(200))")
        assert report.total_time >= 0.0
        assert report.hotspots  # at least one function parsed
        assert isinstance(report.raw_profile, str)
        assert report.raw_profile  # pstats printed something

    def test_profiles_code_that_raises(self, dbg):
        report = dbg.analyze_performance("raise ValueError('boom')")
        assert report.total_time >= 0.0
        assert isinstance(report.hotspots, list)


# ── session management edges ─────────────────────────────────────────────


class TestSessionEdges:
    def test_record_fix_auto_starts_session(self, dbg):
        fix = FixSuggestion(description="d", code_snippet="s", target_line=1, confidence=0.5)
        dbg.record_fix(fix, applied=True)
        assert dbg._current_session is not None
        assert len(dbg._current_session.fixes_applied) == 1
        assert dbg._current_session.fixes_applied[0]["applied"] is True

    def test_record_error_auto_starts_session(self, dbg):
        parsed = ParsedTraceback("ValueError", "bad", [], "raw")
        dbg.record_error(parsed, notes="n")
        assert dbg._current_session is not None
        assert dbg._current_session.errors_seen[0]["exception_type"] == "ValueError"

    def test_persist_session_with_corrupt_existing_file(self, dbg, tmp_path):
        session_file = dbg._session_file
        session_file.write_text("{not valid json", encoding="utf-8")
        dbg.start_session("s1")
        ended = dbg.end_session()
        assert ended is not None and ended.session_id == "s1"
        data = json.loads(session_file.read_text(encoding="utf-8"))
        assert isinstance(data, list) and len(data) == 1
        assert data[0]["session_id"] == "s1"

    def test_persist_session_appends_to_existing(self, dbg):
        dbg.start_session("s1")
        dbg.end_session()
        dbg.start_session("s2")
        dbg.end_session()
        sessions = dbg.load_sessions()
        assert [s["session_id"] for s in sessions] == ["s1", "s2"]

    def test_load_sessions_missing_file(self, tmp_path):
        fresh = AdvancedDebugging(persist_dir=tmp_path / "empty")
        assert fresh.load_sessions() == []

    def test_end_session_without_start_returns_none(self, dbg):
        assert dbg.end_session() is None
