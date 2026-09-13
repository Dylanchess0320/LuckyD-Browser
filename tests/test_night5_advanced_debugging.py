"""Night-5 tests: core/advanced_debugging.py (was ~0% covered).

Traceback parsing, frame ranking, local-var hints, rule-based fix
suggestions, static error prediction, profile-output parsing, and debug
session persistence. No live code is profiled — only canned pstats text.
"""

from __future__ import annotations

import pytest

from core.advanced_debugging import AdvancedDebugging


@pytest.fixture()
def dbg(tmp_path):
    return AdvancedDebugging(persist_dir=tmp_path / "debug")


SAMPLE_TB = """Traceback (most recent call last):
  File "/home/user/project/main.py", line 42, in main
    user = users["alice"]
  File "/usr/lib/python3.12/site-packages/app/db.py", line 15, in get_user
    return self._cache[key]
KeyError: 'alice'
"""


# ── traceback parsing ────────────────────────────────────────────────────


def test_parse_traceback_extracts_exception_and_frames(dbg):
    report = dbg.analyze_traceback(SAMPLE_TB)
    parsed = report.parsed
    assert parsed.exception_type == "KeyError"
    assert parsed.exception_message == "'alice'"
    assert len(parsed.frames) == 2
    assert parsed.frames[0].file_path == "/home/user/project/main.py"
    assert parsed.frames[0].line_number == 42
    assert parsed.frames[0].function_name == "main"
    assert parsed.frames[0].code_context == 'user = users["alice"]'
    assert parsed.raw_text == SAMPLE_TB


def test_rank_frames_user_code_before_site_packages(dbg):
    report = dbg.analyze_traceback(SAMPLE_TB)
    top = report.suspicious_frames[0]
    assert top.file_path == "/home/user/project/main.py"
    assert report.suspicious_frames[-1].file_path.endswith("db.py")


def test_confidence_reflects_extracted_info(dbg):
    report = dbg.analyze_traceback(SAMPLE_TB)
    assert report.confidence == pytest.approx(0.8)  # type + message + frames
    empty = dbg.analyze_traceback("")
    assert empty.parsed.exception_type == "UnknownError"
    assert empty.parsed.frames == []
    assert empty.confidence == 0.0


def test_read_local_vars_from_real_file(dbg, tmp_path):
    src = tmp_path / "mod.py"
    src.write_text(
        "def handler():\n"
        "    user = None\n"
        "    retries = 3\n"
        "    name = get_name()\n"
        "    return user\n",
        encoding="utf-8",
    )
    tb = (
        "Traceback (most recent call last):\n"
        f'  File "{src}", line 3, in handler\n'
        "    name = get_name()\n"
        "AttributeError: 'NoneType' object has no attribute 'upper'\n"
    )
    report = dbg.analyze_traceback(tb)
    frame = report.parsed.frames[0]
    assert frame.local_vars["user"] == "None"
    assert frame.local_vars["retries"] == "3"
    assert frame.local_vars["name"] == "<dynamic>"
    assert any("`user` is None" in h for h in report.local_var_hints)


def test_read_local_vars_missing_file(dbg):
    tb = (
        "Traceback (most recent call last):\n"
        '  File "/no/such/file.py", line 1, in f\n'
        "    x = 1\n"
        "ValueError: bad\n"
    )
    report = dbg.analyze_traceback(tb)
    assert report.parsed.frames[0].local_vars == {}


def test_exception_without_message(dbg):
    report = dbg.analyze_traceback("Traceback (most recent call last):\nStopIteration\n")
    assert report.parsed.exception_type == "StopIteration"
    assert report.parsed.exception_message == ""


# ── fix suggestions ──────────────────────────────────────────────────────


def test_suggest_fixes_keyerror(dbg):
    fixes = dbg.suggest_fixes(SAMPLE_TB)
    assert fixes
    first = fixes[0]
    assert ".get(" in first.code_snippet
    assert "'alice'" in first.code_snippet
    assert first.target_line == 15  # last frame's line
    assert first.confidence == 0.8


def test_suggest_fixes_accepts_parsed(dbg):
    parsed = dbg._parse_traceback(SAMPLE_TB)
    assert [f.description for f in dbg.suggest_fixes(parsed)] == [
        f.description for f in dbg.suggest_fixes(SAMPLE_TB)
    ]


def test_suggest_fixes_module_not_found(dbg):
    tb = "Traceback (most recent call last):\nModuleNotFoundError: No module named 'yaml'\n"
    fixes = dbg.suggest_fixes(tb)
    assert any("pip install yaml" in f.code_snippet for f in fixes)


def test_suggest_fixes_index_error(dbg):
    tb = "Traceback (most recent call last):\nIndexError: list index out of range\n"
    fixes = dbg.suggest_fixes(tb)
    assert any("len(my_list)" in f.code_snippet for f in fixes)


def test_suggest_fixes_fallback_for_unknown(dbg):
    tb = "Traceback (most recent call last):\nWeirdError: something odd\n"
    fixes = dbg.suggest_fixes(tb)
    assert len(fixes) == 1
    assert fixes[0].confidence == 0.3
    assert "traceback.print_exc()" in fixes[0].code_snippet


# ── static error prediction ──────────────────────────────────────────────


def test_predict_errors_flags_risky_patterns(dbg):
    code = (
        "def bad(a=[]):\n"
        "    try:\n"
        "        f = open('x.txt')\n"
        "    except:\n"
        "        pass\n"
        "    if a == None:\n"
        "        eval('1')\n"
    )
    issues = dbg.predict_errors(code)
    types = {i.issue_type for i in issues}
    assert {
        "bare_except",
        "mutable_default",
        "none_comparison",
        "unclosed_file",
        "eval_exec",
    } <= types
    assert all(i.severity in ("high", "medium", "low") for i in issues)
    # sorted by (line, column)
    assert [(i.line, i.column) for i in issues] == sorted((i.line, i.column) for i in issues)


def test_predict_errors_syntax_error(dbg):
    issues = dbg.predict_errors("def broken(:\n")
    assert len(issues) == 1
    assert issues[0].issue_type == "syntax_error"
    assert issues[0].severity == "high"


def test_predict_errors_clean_code(dbg):
    assert dbg.predict_errors("def ok(a=None):\n    return a\n") == []


# ── profile output parsing ───────────────────────────────────────────────


CANNED_PSTATS = """\
         7 function calls in 0.010 seconds

   Ordered by: cumulative time

   ncalls  tottime  percall  cumtime  percall filename:lineno(function)
        1    0.001    0.001    0.010    0.010 /app/main.py:10(slow)
        3    0.002    0.001    0.006    0.002 /app/main.py:20(fast)
        1    0.000    0.000    0.000    0.000 {built-in method builtins.sum}
"""


def test_parse_profile_output(dbg):
    hotspots = dbg._parse_profile_output(CANNED_PSTATS, total_time=0.010)
    assert len(hotspots) == 3
    # sorted by cumulative_time desc
    assert [h.function_name for h in hotspots] == ["slow", "fast", "{built-in method builtins.sum}"]
    slow = hotspots[0]
    assert slow.file_path == "/app/main.py"
    assert slow.line_number == 10
    assert slow.calls == 1
    assert slow.percentage == pytest.approx(100.0)
    assert hotspots[1].percentage == pytest.approx(60.0)
    builtin = hotspots[2]
    assert builtin.file_path == "<unknown>"


def test_parse_profile_output_zero_total_time(dbg):
    hotspots = dbg._parse_profile_output(CANNED_PSTATS, total_time=0.0)
    assert all(h.percentage == 0.0 for h in hotspots)


def test_timeit_returns_average(dbg):
    avg = dbg.timeit(lambda: None, runs=100)
    assert avg >= 0.0


# ── sessions ─────────────────────────────────────────────────────────────


def test_session_lifecycle(dbg):
    s = dbg.start_session("sess-1")
    assert s.session_id == "sess-1"
    assert s.errors_seen == [] and s.fixes_applied == []

    parsed = dbg._parse_traceback(SAMPLE_TB)
    dbg.record_error(parsed, notes="repro'd locally")
    fixes = dbg.suggest_fixes(parsed)
    dbg.record_fix(fixes[0], applied=True)

    ended = dbg.end_session()
    assert ended is not None
    assert ended.ended_at is not None
    assert len(ended.errors_seen) == 1
    assert ended.errors_seen[0]["exception_type"] == "KeyError"
    assert ended.errors_seen[0]["notes"] == "repro'd locally"
    assert ended.fixes_applied[0]["applied"] is True

    sessions = dbg.load_sessions()
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "sess-1"

    assert dbg.end_session() is None  # nothing active


def test_record_error_auto_starts_session(dbg):
    parsed = dbg._parse_traceback(SAMPLE_TB)
    dbg.record_error(parsed)
    assert dbg._current_session is not None
    dbg.end_session()


def test_load_sessions_corrupt_file(dbg):
    dbg._session_file.write_text("{corrupt", encoding="utf-8")
    assert dbg.load_sessions() == []


def test_start_session_auto_id(dbg):
    s = dbg.start_session()
    assert s.session_id.startswith("debug_")
    dbg.end_session()


# ── quick_diagnose ───────────────────────────────────────────────────────


def test_quick_diagnose_shape(dbg):
    out = dbg.quick_diagnose(SAMPLE_TB)
    assert out["exception"] == "KeyError: 'alice'"
    assert out["top_suspicious_frame"] == "/home/user/project/main.py:42"
    assert isinstance(out["local_var_hints"], list)
    assert out["fixes"]
    assert set(out["fixes"][0]) == {"description", "snippet", "confidence"}
    assert 0.0 <= out["confidence"] <= 1.0


def test_quick_diagnose_no_frames(dbg):
    out = dbg.quick_diagnose("WeirdError: x\n")
    assert out["top_suspicious_frame"] is None
