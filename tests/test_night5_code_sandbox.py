"""Night-5 tests: core/code_sandbox.py (was ~0% covered).

Syntax validation, the forbidden-construct AST gate, lint rules, test-skeleton
generation, and execute()/execute_file() sandbox *decisions*. The subprocess
is always mocked — no untrusted code is ever executed here.
"""

from __future__ import annotations

import ast
import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

import core.code_sandbox as sandbox_mod
from core.code_sandbox import (
    MAX_OUTPUT_CHARS,
    CodeExecutionSandbox,
    ExecutionResult,
    LintIssue,
    _call_name,
    _cap,
    _elapsed_ms,
    _remove_tree,
)


@pytest.fixture()
def sandbox():
    return CodeExecutionSandbox(trusted=False)


@pytest.fixture()
def trusted_sandbox():
    return CodeExecutionSandbox(trusted=True)


# ── syntax validation ──────────────────────────────────────────────────


def test_validate_syntax_ok(sandbox):
    assert sandbox.validate_syntax("x = 1\nprint(x)\n") == (True, None)


def test_validate_syntax_error_reports_line(sandbox):
    ok, err = sandbox.validate_syntax("def broken(:\n")
    assert ok is False
    assert err is not None and "SyntaxError" in err and "line" in err


# ── forbidden-construct gate ───────────────────────────────────────────


@pytest.mark.parametrize(
    "code,fragment",
    [
        ("import subprocess\n", "subprocess"),
        ("import shutil as s\n", "shutil"),
        ("import os.path\n", None),  # os itself is not forbidden
        ("from subprocess import run\n", "subprocess"),
        ("import socket\n", "socket"),
        ("x = eval('1')\n", "eval"),
        ("x = exec('y=1')\n", "exec"),
        ("getattr(obj, 'x')\n", "getattr"),
        ("import os\nos.system('ls')\n", "os.system"),
        ("import os\nos.remove('f')\n", "os.remove"),
        ("().__class__\n", "__class__"),
        ("x.__subclasses__()\n", "__subclasses__"),
    ],
)
def test_find_forbidden(sandbox, code, fragment):
    hit = sandbox._find_forbidden(code)
    if fragment is None:
        assert hit is None
    else:
        assert hit is not None and fragment in hit


def test_find_forbidden_clean_code(sandbox):
    assert sandbox._find_forbidden("import math\nprint(math.sqrt(4))\n") is None


def test_find_forbidden_syntax_error_returns_none(sandbox):
    assert sandbox._find_forbidden("def broken(:\n") is None


def test_execute_blocks_forbidden_without_spawning(sandbox):
    with patch.object(sandbox_mod.subprocess, "run") as mock_run:
        result = sandbox.execute("import subprocess\n")
    assert result.success is False
    assert result.error_type == "forbidden"
    assert "subprocess" in result.stderr
    mock_run.assert_not_called()


def test_execute_syntax_error_without_spawning(sandbox):
    with patch.object(sandbox_mod.subprocess, "run") as mock_run:
        result = sandbox.execute("def broken(:\n")
    assert result.success is False
    assert result.error_type == "syntax"
    mock_run.assert_not_called()


def test_execute_trusted_skips_forbidden_gate(trusted_sandbox):
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok\n", stderr="")
    with patch.object(sandbox_mod.subprocess, "run", return_value=completed) as mock_run:
        result = trusted_sandbox.execute("import subprocess\nprint('x')\n")
    assert result.success is True
    mock_run.assert_called_once()


# ── execute() with mocked subprocess ────────────────────────────────────


def _completed(rc=0, stdout="out\n", stderr=""):
    return subprocess.CompletedProcess(args=["python"], returncode=rc, stdout=stdout, stderr=stderr)


def test_execute_success_result_shape(sandbox):
    with patch.object(sandbox_mod.subprocess, "run", return_value=_completed()) as mock_run:
        result = sandbox.execute("print('hello')\n")
    assert result.success is True
    assert result.stdout == "out\n"
    assert result.exit_code == 0
    assert result.error_type is None
    assert result.timed_out is False
    assert result.duration_ms >= 0
    # the runner script was written into an isolated temp dir
    _, kwargs = mock_run.call_args
    assert kwargs["cwd"].startswith(tempfile.gettempdir())


def test_execute_runtime_failure(sandbox):
    with patch.object(
        sandbox_mod.subprocess, "run", return_value=_completed(rc=1, stderr="boom\n")
    ):
        result = sandbox.execute("print('x')\n")
    assert result.success is False
    assert result.error_type == "runtime"
    assert result.exit_code == 1
    assert result.stderr == "boom\n"


def test_execute_timeout(sandbox):
    exc = subprocess.TimeoutExpired(cmd=["python"], timeout=2.0, output="partial\n")
    with patch.object(sandbox_mod.subprocess, "run", side_effect=exc):
        result = sandbox.execute("print('x')\n", timeout=2.0)
    assert result.success is False
    assert result.timed_out is True
    assert result.error_type == "timeout"
    assert "timed out after 2.0s" in result.stderr
    assert result.stdout == "partial\n"


def test_execute_spawn_oserror(sandbox):
    with patch.object(sandbox_mod.subprocess, "run", side_effect=OSError("noexec")):
        result = sandbox.execute("print('x')\n")
    assert result.success is False
    assert result.error_type == "runtime"
    assert "Failed to spawn" in result.stderr


def test_execute_temp_dir_cleaned_up(sandbox):
    created: list[str] = []
    real_mkdtemp = tempfile.mkdtemp

    def spy_mkdtemp(*a, **k):
        d = real_mkdtemp(*a, **k)
        created.append(d)
        return d

    with (
        patch.object(sandbox_mod.tempfile, "mkdtemp", spy_mkdtemp),
        patch.object(sandbox_mod.subprocess, "run", return_value=_completed()),
    ):
        sandbox.execute("print('x')\n")
    assert created and not Path(created[0]).exists()


def test_execute_runner_wraps_code(sandbox):
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen["script"] = Path(cmd[2]).read_text(encoding="utf-8")
        return _completed()

    with patch.object(sandbox_mod.subprocess, "run", side_effect=fake_run):
        sandbox.execute("x = 1\n")
    assert "def _run():" in seen["script"]
    assert "x = 1" in seen["script"]
    assert "traceback.print_exc()" in seen["script"]


def test_build_env_drops_secrets(sandbox, monkeypatch):
    monkeypatch.setenv("SECRET_API_KEY", "s3cr3t")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = sandbox._build_env({"EXTRA": "1"})
    assert "SECRET_API_KEY" not in env
    assert env["PATH"] == "/usr/bin"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["EXTRA"] == "1"


# ── execute_file ─────────────────────────────────────────────────────────


def test_execute_file_missing(sandbox):
    result = sandbox.execute_file("/no/such/file.py")
    assert result.success is False
    assert result.error_type == "runtime"
    assert "File not found" in result.stderr


def test_execute_file_reads_and_delegates(sandbox, tmp_path):
    target = tmp_path / "prog.py"
    target.write_text("print('from file')\n", encoding="utf-8")
    with patch.object(
        CodeExecutionSandbox,
        "execute",
        return_value=ExecutionResult(success=True, stdout="from file\n"),
    ) as mock_exec:
        result = sandbox.execute_file(target)
    assert result.success is True
    mock_exec.assert_called_once()
    assert mock_exec.call_args[0][0] == "print('from file')\n"


# ── lint ─────────────────────────────────────────────────────────────────


def test_lint_clean(sandbox):
    assert sandbox.lint("import math\n\n\nprint(math.pi)\n") == []


def test_lint_syntax_error(sandbox):
    issues = sandbox.lint("def broken(:\n")
    assert len(issues) == 1
    assert issues[0].code == "syntax-error"


def test_lint_unused_import(sandbox):
    issues = sandbox.lint("import os\nimport math\nprint(math.pi)\n")
    assert [i.code for i in issues] == ["unused-import"]
    assert "os" in issues[0].message


def test_lint_bare_except(sandbox):
    issues = sandbox.lint("try:\n    pass\nexcept:\n    pass\n")
    assert any(i.code == "bare-except" for i in issues)


def test_lint_unreachable_code(sandbox):
    issues = sandbox.lint("def f():\n    return 1\n    x = 2\n")
    assert any(i.code == "unreachable-code" for i in issues)


def test_lint_mutable_default(sandbox):
    issues = sandbox.lint("def f(items=[]):\n    return items\n")
    assert any(i.code == "mutable-default" for i in issues)


def test_lint_shadowed_builtin(sandbox):
    issues = sandbox.lint("list = [1, 2]\n")
    assert any(i.code == "builtin-shadow" for i in issues)
    assert "'list' shadows a Python builtin" in issues[0].message


def test_lint_sorted_by_line(sandbox):
    issues = sandbox.lint("import os\ndef f(items=[]):\n    return items\n")
    lines = [i.line for i in issues]
    assert lines == sorted(lines)


def test_lint_issue_str():
    issue = LintIssue(line=3, code="bare-except", message="don't")
    assert str(issue) == "line 3: [bare-except] don't"


# ── generate_tests ───────────────────────────────────────────────────────


def test_generate_tests_skeleton(sandbox):
    code = "def add(a, b):\n    return a + b\n\n\nasync def fetch(url):\n    return url\n\n\ndef _private():\n    pass\n"
    skeleton = sandbox.generate_tests(code)
    assert "def test_add():" in skeleton
    assert "result = add(a=..., b=...)" in skeleton
    assert "def test_fetch():" in skeleton
    assert "test__private" not in skeleton
    assert "import pytest" in skeleton


def test_generate_tests_no_functions(sandbox):
    skeleton = sandbox.generate_tests("x = 1\n")
    assert "No public top-level functions found." in skeleton


def test_generate_tests_syntax_error(sandbox):
    skeleton = sandbox.generate_tests("def broken(:\n")
    assert skeleton.startswith("# Cannot generate tests")


# ── profile ──────────────────────────────────────────────────────────────


def test_profile_untrusted_allowed(sandbox):
    result = sandbox.profile("x = 1\n")
    assert result.success is True
    assert result.stats_text


def test_profile_untrusted_blocked(sandbox):
    result = sandbox.profile("import os\nos.system('echo pwned')")
    assert result.success is False
    assert "Forbidden" in result.error


def test_profile_trusted_runs_own_snippet(trusted_sandbox):
    # trusted mode execs in-process; the snippet is a constant authored here
    result = trusted_sandbox.profile("total = sum(range(100))\n")
    assert result.success is True
    assert result.cumulative_time >= 0
    assert result.stats_text  # pstats output captured
    assert isinstance(result.top_functions, list)


def test_profile_trusted_syntax_error(trusted_sandbox):
    result = trusted_sandbox.profile("def broken(:\n")
    assert result.success is False
    assert result.error is not None


def test_profile_trusted_runtime_error_still_reports_stats(trusted_sandbox):
    result = trusted_sandbox.profile("raise ValueError('kaput')\n")
    assert result.success is False
    assert "ValueError" in result.error


# ── small helpers ────────────────────────────────────────────────────────


def test_execution_result_serialization():
    r = ExecutionResult(success=True, stdout="hi", exit_code=0)
    d = r.to_dict()
    assert d["success"] is True and d["stdout"] == "hi"
    assert json.loads(r.to_json())["exit_code"] == 0


def test_cap_truncates_huge_output():
    big = "x" * (MAX_OUTPUT_CHARS + 10)
    out = _cap(big)
    assert out.startswith("x" * MAX_OUTPUT_CHARS)
    assert out.endswith(f"... [truncated at {MAX_OUTPUT_CHARS} chars]")
    assert _cap("") == ""
    assert _cap(None) == ""
    assert _cap("short") == "short"


def test_call_name_resolution():
    tree = ast.parse("os.system('x')\neval('y')\nobj.attr.deep('z')\n")
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    names = {_call_name(c.func) for c in calls}
    assert names == {"os.system", "eval", "obj.attr.deep"}


def test_elapsed_ms_nonnegative():
    import time

    assert _elapsed_ms(time.perf_counter()) >= 0


def test_remove_tree_best_effort(tmp_path):
    target = tmp_path / "gone"
    target.mkdir()
    (target / "f.txt").write_text("x", encoding="utf-8")
    _remove_tree(str(target))
    assert not target.exists()
    _remove_tree(str(tmp_path / "never-existed"))  # must not raise


def _assertion_for(skeleton: str, func: str) -> str:
    """The generated assertion line (stripped) inside ``test_<func>``."""
    lines = skeleton.splitlines()
    i = lines.index(f"def test_{func}():")
    for line in lines[i + 1 :]:
        if line.strip().startswith("assert "):
            return line.strip()
        if line.startswith("def "):
            break
    raise AssertionError(f"no assertion generated for {func}")


def test_generate_tests_assertions(sandbox):
    code = """
def test_bool() -> bool:
    return True

def test_int() -> int:
    return 1

def test_float() -> float:
    return 1.5

def test_str() -> str:
    return "a"

def test_list() -> list[int]:
    return [1]

def test_dict() -> dict[str, int]:
    return {"a": 1}

def test_set() -> set[str]:
    return {"a"}

def test_tuple() -> tuple[int, int]:
    return (1, 2)

def test_none() -> None:
    return None

def test_bytes() -> bytes:
    return b"a"

def test_unannotated():
    return 1

def test_custom() -> MyClass:
    return MyClass()

def test_generic() -> MyGeneric[int]:
    return MyGeneric()

def test_attr() -> models.User:
    return models.User()
"""
    skeleton = sandbox.generate_tests(code)

    assert _assertion_for(skeleton, "test_bool") == "assert isinstance(result, bool)"
    assert _assertion_for(skeleton, "test_int") == "assert isinstance(result, int)"
    assert _assertion_for(skeleton, "test_float") == "assert isinstance(result, (int, float))"
    assert _assertion_for(skeleton, "test_str") == "assert isinstance(result, str)"
    assert (
        _assertion_for(skeleton, "test_list")
        == "assert isinstance(result, list) and all(isinstance(x, int) for x in result)"
    )
    assert _assertion_for(skeleton, "test_dict") == (
        "assert isinstance(result, dict) and all(isinstance(k, str) and isinstance(v, int) "
        "for k, v in result.items())"
    )
    assert (
        _assertion_for(skeleton, "test_set")
        == "assert isinstance(result, set) and all(isinstance(x, str) for x in result)"
    )
    assert _assertion_for(skeleton, "test_tuple") == (
        "assert isinstance(result, tuple) and len(result) == 2 "
        "and isinstance(result[0], int) and isinstance(result[1], int)"
    )
    assert _assertion_for(skeleton, "test_none") == "assert result is None"
    assert _assertion_for(skeleton, "test_bytes") == "assert isinstance(result, bytes)"
    assert _assertion_for(skeleton, "test_unannotated") == "assert isinstance(result, int)"
    assert _assertion_for(skeleton, "test_custom") == "assert isinstance(result, MyClass)"
    assert _assertion_for(skeleton, "test_generic") == "assert isinstance(result, MyGeneric)"
    assert _assertion_for(skeleton, "test_attr") == "assert isinstance(result, models.User)"
    assert "TODO" not in skeleton


def test_infer_assertion_generic_dict(sandbox):
    code = """
def test_dict_both() -> dict[str, int]:
    return {"a": 1}

def test_dict_key_only() -> dict[str, Any]:
    return {"a": [1]}

def test_dict_val_only() -> dict[Any, list]:
    return {1: []}

def test_dict_any_any() -> dict[Any, Any]:
    return {"a": 1}

def test_dict_bare() -> dict:
    return {"a": 1}

def test_dict_typing() -> Dict[str, List[int]]:
    return {"a": [1]}
"""
    skeleton = sandbox.generate_tests(code)

    assert _assertion_for(skeleton, "test_dict_both") == (
        "assert isinstance(result, dict) and all(isinstance(k, str) and isinstance(v, int) "
        "for k, v in result.items())"
    )
    assert _assertion_for(skeleton, "test_dict_key_only") == (
        "assert isinstance(result, dict) and all(isinstance(k, str) for k in result.keys())"
    )
    assert _assertion_for(skeleton, "test_dict_val_only") == (
        "assert isinstance(result, dict) and all(isinstance(v, list) for v in result.values())"
    )
    assert _assertion_for(skeleton, "test_dict_any_any") == "assert isinstance(result, dict)"
    assert _assertion_for(skeleton, "test_dict_bare") == "assert isinstance(result, dict)"
    assert _assertion_for(skeleton, "test_dict_typing") == (
        "assert isinstance(result, dict) and all(isinstance(k, str) and isinstance(v, list) "
        "and all(isinstance(x1, int) for x1 in v) for k, v in result.items())"
    )


def test_infer_assertion_unions_and_specials(sandbox):
    code = """
def test_optional() -> Optional[int]:
    return None

def test_pep604() -> int | str | None:
    return "x"

def test_union_compound() -> Union[list[int], str]:
    return "x"

def test_literal() -> Literal["a", "b"]:
    return "a"

def test_literal_one() -> Literal[3]:
    return 3

def test_forward_ref() -> "MyClass":
    return MyClass()

def test_callable() -> Callable[[int], int]:
    return abs

def test_varlen_tuple() -> tuple[int, ...]:
    return (1, 2)

def test_empty_tuple() -> tuple[()]:
    return ()

def test_any() -> Any:
    return 1

def test_typevar() -> T:
    return 1

def test_optional_any() -> Optional[Any]:
    return None
"""
    skeleton = sandbox.generate_tests(code)

    assert (
        _assertion_for(skeleton, "test_optional")
        == "assert result is None or isinstance(result, int)"
    )
    assert (
        _assertion_for(skeleton, "test_pep604")
        == "assert result is None or isinstance(result, (int, str))"
    )
    assert _assertion_for(skeleton, "test_union_compound") == (
        "assert isinstance(result, str) or "
        "(isinstance(result, list) and all(isinstance(x, int) for x in result))"
    )
    assert _assertion_for(skeleton, "test_literal") == "assert result in ('a', 'b')"
    assert _assertion_for(skeleton, "test_literal_one") == "assert result == 3"
    assert _assertion_for(skeleton, "test_forward_ref") == "assert isinstance(result, MyClass)"
    assert _assertion_for(skeleton, "test_callable") == "assert callable(result)"
    assert _assertion_for(skeleton, "test_varlen_tuple") == (
        "assert isinstance(result, tuple) and all(isinstance(x, int) for x in result)"
    )
    assert _assertion_for(skeleton, "test_empty_tuple") == "assert result == ()"
    for name in ("test_any", "test_typevar", "test_optional_any"):
        assert (
            _assertion_for(skeleton, name) == "assert result is not None  # TODO: real assertion"
        )


def test_infer_assertion_from_body(sandbox):
    """Unannotated functions: infer assertion from their own return statements."""
    code = """
def test_int():
    return 1

def test_list():
    return [1, 2]

def test_none():
    return None

def test_no_return():
    pass

def test_bool_compare(a, b):
    return a > b

def test_builtin_call(x):
    return len(x)

def test_mixed(flag):
    if flag:
        return True
    return "no"

def test_fall_through(flag):
    if flag:
        return 1

def test_try_both_paths():
    try:
        return 1
    except Exception:
        return 2

def test_opaque(x):
    return x

def test_nested_scope_ignored():
    def inner():
        return "nested"
    return [1]

def test_generator():
    yield 1
"""
    skeleton = sandbox.generate_tests(code)

    assert _assertion_for(skeleton, "test_int") == "assert isinstance(result, int)"
    assert _assertion_for(skeleton, "test_list") == "assert isinstance(result, list)"
    assert _assertion_for(skeleton, "test_none") == "assert result is None"
    assert _assertion_for(skeleton, "test_no_return") == "assert result is None"
    assert _assertion_for(skeleton, "test_bool_compare") == "assert isinstance(result, bool)"
    assert _assertion_for(skeleton, "test_builtin_call") == "assert isinstance(result, int)"
    assert _assertion_for(skeleton, "test_mixed") == "assert isinstance(result, (bool, str))"
    assert (
        _assertion_for(skeleton, "test_fall_through")
        == "assert result is None or isinstance(result, int)"
    )
    assert _assertion_for(skeleton, "test_try_both_paths") == "assert isinstance(result, int)"
    assert (
        _assertion_for(skeleton, "test_opaque")
        == "assert result is not None  # TODO: real assertion"
    )
    assert (
        _assertion_for(skeleton, "test_nested_scope_ignored") == "assert isinstance(result, list)"
    )
    assert _assertion_for(skeleton, "test_generator") == 'assert hasattr(result, "__next__")'


def test_infer_assertion_custom_types(sandbox):
    """Custom, generic, and attribute annotations: isinstance against the named type."""
    code = """
def test_custom() -> MyClass:
    return MyClass()

def test_generic() -> MyGeneric[int]:
    return MyGeneric()

def test_attr() -> models.User:
    return models.User()
"""
    skeleton = sandbox.generate_tests(code)

    assert _assertion_for(skeleton, "test_custom") == "assert isinstance(result, MyClass)"
    assert _assertion_for(skeleton, "test_generic") == "assert isinstance(result, MyGeneric)"
    assert _assertion_for(skeleton, "test_attr") == "assert isinstance(result, models.User)"


def test_generate_tests_async_functions(sandbox):
    code = """
async def fetch() -> int:
    return 1

async def stream():
    yield 1

def plain() -> int:
    return 1
"""
    skeleton = sandbox.generate_tests(code)

    assert "import asyncio" in skeleton
    assert "    result = asyncio.run(fetch())" in skeleton
    assert _assertion_for(skeleton, "fetch") == "assert isinstance(result, int)"
    assert "    result = stream()" in skeleton
    assert _assertion_for(skeleton, "stream") == 'assert hasattr(result, "__anext__")'
    assert "    result = plain()" in skeleton


def test_generate_tests_no_asyncio_import_without_coroutines(sandbox):
    skeleton = sandbox.generate_tests("def f() -> int:\n    return 1\n")
    assert "import asyncio" not in skeleton


def test_generated_assertions_pass_against_real_implementations(sandbox):
    """Every generated assertion is valid Python and holds for a correct implementation."""
    import asyncio

    code = """
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, Union


class MyClass:
    pass


def t_bool() -> bool:
    return False

def t_float() -> float:
    return 3

def t_list() -> list[int]:
    return [1, 2]

def t_nested() -> list[dict[str, list[int]]]:
    return [{"a": [1, 2]}, {}]

def t_dict_typing() -> Dict[str, Optional[int]]:
    return {"a": None, "b": 2}

def t_tuple() -> tuple[int, str]:
    return (1, "a")

def t_tuple_typing() -> Tuple[int, ...]:
    return ()

def t_optional() -> Optional[int]:
    return None

def t_union() -> Union[List[int], str]:
    return [3]

def t_pep604() -> int | None:
    return 0

def t_literal() -> Literal["a", "b"]:
    return "b"

def t_callable() -> Callable[[int], int]:
    return abs

def t_custom() -> MyClass:
    return MyClass()

def t_forward() -> "MyClass":
    return MyClass()

def t_body_mixed(flag=False):
    if flag:
        return 1
    return "x"

def t_body_fall_through(flag=False):
    if flag:
        return [1]

def t_generator():
    yield 1

async def t_async() -> dict[str, int]:
    return {"a": 1}

async def t_async_gen():
    yield 1
"""
    namespace: dict = {}
    exec(compile(code, "<impl>", "exec"), namespace)
    namespace["asyncio"] = asyncio

    skeleton = sandbox.generate_tests(code)
    lines = [line.strip() for line in skeleton.splitlines()]
    pairs = [
        (lines[i], lines[i + 2])
        for i in range(len(lines) - 2)
        if lines[i].startswith("result = ") and lines[i + 2].startswith("assert ")
    ]
    assert len(pairs) == 19
    for act, assertion in pairs:
        exec(f"{act}\n{assertion}", namespace)

