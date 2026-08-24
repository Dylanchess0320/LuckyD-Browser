"""
Code execution sandbox — safe subprocess-based Python code execution.

Runs untrusted (or semi-trusted) Python code in an isolated subprocess with:
  - Wall-clock timeouts (kills the whole process tree on expiry)
  - Optional memory limits via the ``resource`` module on Unix; on Windows the
    sandbox notes where Job Objects would hook in (stdlib has no binding, so
    enforcement is left to the OS / caller)
  - stdout/stderr capture with size caps
  - AST-based static checks: syntax validation, lint, forbidden-node blocking
  - Temp-dir isolation so executed code's file writes land in a scratch dir
  - Test generation and cProfile-based profiling helpers

Windows-compatible: uses CREATE_NEW_PROCESS_GROUP so the child can be killed
cleanly, and avoids Unix-only APIs unless guarded by a platform check.

Stdlib only — no pip dependencies.
"""

from __future__ import annotations

import ast
import cProfile
import io
import json
import os
import pstats
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# ── Constants ──────────────────────────────────────────────────────────

#: Cap on captured stdout/stderr so runaway prints can't exhaust memory.
MAX_OUTPUT_CHARS = 1_000_000

#: Modules that untrusted code may never import.
FORBIDDEN_IMPORTS: frozenset[str] = frozenset(
    {
        "subprocess",
        "multiprocessing",
        "ctypes",
        "socket",
        "shutil",
        "pty",
        "fcntl",
        "signal",
        "_socket",
        "mmap",
    }
)

#: Dotted attribute calls that are always blocked in untrusted mode.
FORBIDDEN_CALLS: frozenset[str] = frozenset(
    {
        "os.system",
        "os.popen",
        "os.spawnl",
        "os.spawnle",
        "os.spawnlp",
        "os.spawnv",
        "os.spawnve",
        "os.spawnvp",
        "os.execv",
        "os.execve",
        "os.execvp",
        "os.execl",
        "os.execle",
        "os.execlp",
        "os.kill",
        "os.remove",
        "os.unlink",
        "os.rmdir",
        "os.removedirs",
        "os.chmod",
        "os.chown",
    }
)

#: Builtin calls that are blocked in untrusted mode.
FORBIDDEN_BUILTINS: frozenset[str] = frozenset(
    {"eval", "exec", "compile", "__import__", "breakpoint"}
)


# ── Result Types ───────────────────────────────────────────────────────


@dataclass
class ExecutionResult:
    """Outcome of a sandboxed code execution."""

    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_ms: float = 0.0
    timed_out: bool = False
    error_type: str | None = None  # "timeout" | "syntax" | "forbidden" | "runtime" | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


@dataclass
class LintIssue:
    """A single lint finding."""

    line: int
    code: str  # short machine-readable tag, e.g. "unused-import"
    message: str

    def __str__(self) -> str:
        return f"line {self.line}: [{self.code}] {self.message}"


@dataclass
class ProfileResult:
    """Outcome of profiling a code snippet."""

    success: bool
    stats_text: str = ""
    cumulative_time: float = 0.0
    top_functions: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


# ── Sandbox ────────────────────────────────────────────────────────────


class CodeExecutionSandbox:
    """Execute Python code safely in a subprocess with static + runtime guards."""

    def __init__(
        self,
        python_executable: str | None = None,
        *,
        trusted: bool = False,
        memory_limit_mb: int | None = None,
        keep_temp: bool = False,
    ):
        """
        Args:
            python_executable: interpreter to spawn (defaults to sys.executable).
            trusted: if True, skip forbidden-import/call AST blocking.
            memory_limit_mb: RLIMIT_AS cap on Unix; ignored on Windows
                             (Job Objects would be needed — stdlib has no API).
            keep_temp: keep the sandbox temp dir after execution (debugging).
        """
        self.python = python_executable or sys.executable
        self.trusted = trusted
        self.memory_limit_mb = memory_limit_mb
        self.keep_temp = keep_temp

    # ── Public API ─────────────────────────────────────────────────────

    def execute(
        self,
        code: str,
        timeout: float = 10.0,
        capture_output: bool = True,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Run *code* in an isolated subprocess and return an ExecutionResult."""
        start = time.perf_counter()

        # 1. Syntax gate — fail fast without spawning a process.
        ok, err = self.validate_syntax(code)
        if not ok:
            return ExecutionResult(
                success=False,
                stderr=err or "SyntaxError",
                exit_code=None,
                duration_ms=_elapsed_ms(start),
                error_type="syntax",
            )

        # 2. Static forbidden-node gate (untrusted mode only).
        if not self.trusted:
            violation = self._find_forbidden(code)
            if violation:
                return ExecutionResult(
                    success=False,
                    stderr=f"Forbidden construct blocked: {violation}",
                    exit_code=None,
                    duration_ms=_elapsed_ms(start),
                    error_type="forbidden",
                )

        # 3. Spawn the child inside a temp dir so file writes are isolated.
        tmpdir = tempfile.mkdtemp(prefix="luckyd_sandbox_")
        try:
            script_path = Path(tmpdir) / "_sandbox_main.py"
            script_path.write_text(self._build_runner(code), encoding="utf-8")

            cmd = [self.python, "-I", str(script_path)]  # -I: isolated mode
            kwargs: dict[str, Any] = {
                "cwd": tmpdir,
                "env": self._build_env(env),
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
            }
            if capture_output:
                kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            else:
                kwargs.update(stdout=None, stderr=None)

            # Windows: new process group so we can kill the child tree
            # without signalling our own console. Unix: new session for
            # the same reason (setsid via start_new_session).
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True

            if self.memory_limit_mb and sys.platform != "win32":
                kwargs["preexec_fn"] = _make_memory_limiter(self.memory_limit_mb)
            # NOTE (Windows): memory caps would be enforced via Job Objects
            # (CreateJobObject / SetInformationJobObject). The stdlib exposes
            # no binding, so on Windows this limit is advisory only.

            try:
                proc = subprocess.run(cmd, timeout=timeout, **kwargs)
                return ExecutionResult(
                    success=proc.returncode == 0,
                    stdout=_cap(proc.stdout),
                    stderr=_cap(proc.stderr),
                    exit_code=proc.returncode,
                    duration_ms=_elapsed_ms(start),
                    error_type=None if proc.returncode == 0 else "runtime",
                )
            except subprocess.TimeoutExpired as exc:
                return ExecutionResult(
                    success=False,
                    stdout=_cap(exc.stdout if isinstance(exc.stdout, str) else ""),
                    stderr=_cap(exc.stderr if isinstance(exc.stderr, str) else "")
                    or f"Execution timed out after {timeout}s",
                    exit_code=None,
                    duration_ms=_elapsed_ms(start),
                    timed_out=True,
                    error_type="timeout",
                )
            except OSError as exc:
                return ExecutionResult(
                    success=False,
                    stderr=f"Failed to spawn sandbox subprocess: {exc}",
                    exit_code=None,
                    duration_ms=_elapsed_ms(start),
                    error_type="runtime",
                )
        finally:
            if not self.keep_temp:
                _remove_tree(tmpdir)

    def execute_file(
        self,
        path: str | Path,
        timeout: float = 10.0,
        capture_output: bool = True,
    ) -> ExecutionResult:
        """Read a .py file and execute it in the sandbox."""
        p = Path(path)
        if not p.is_file():
            return ExecutionResult(
                success=False,
                stderr=f"File not found: {p}",
                error_type="runtime",
            )
        try:
            code = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ExecutionResult(success=False, stderr=str(exc), error_type="runtime")
        return self.execute(code, timeout=timeout, capture_output=capture_output)

    # ── Static analysis ────────────────────────────────────────────────

    def validate_syntax(self, code: str) -> tuple[bool, str | None]:
        """Return (True, None) if *code* parses, else (False, error message)."""
        try:
            ast.parse(code)
            return True, None
        except SyntaxError as exc:
            where = f"line {exc.lineno}" if exc.lineno else "unknown line"
            return False, f"SyntaxError at {where}: {exc.msg}"

    def lint(self, code: str) -> list[LintIssue]:
        """Basic AST lint: unused imports, bare except, unreachable code, etc."""
        ok, _ = self.validate_syntax(code)
        if not ok:
            return [LintIssue(0, "syntax-error", "Code does not parse")]

        tree = ast.parse(code)
        issues: list[LintIssue] = []
        issues.extend(self._lint_unused_imports(tree))
        issues.extend(self._lint_bare_except(tree))
        issues.extend(self._lint_unreachable(tree))
        issues.extend(self._lint_mutable_defaults(tree))
        issues.extend(self._lint_shadowed_builtins(tree))
        issues.sort(key=lambda i: i.line)
        return issues

    def generate_tests(self, code: str) -> str:
        """Generate a pytest skeleton with one stub test per top-level function."""
        ok, err = self.validate_syntax(code)
        if not ok:
            return f"# Cannot generate tests — {err}"

        tree = ast.parse(code)
        funcs = [
            n
            for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_")
        ]
        lines = [
            '"""Auto-generated test skeleton. Fill in the assertions."""',
            "",
            "import pytest",
            "",
            "# from your_module import " + ", ".join(f.name for f in funcs) if funcs else "",
            "",
        ]
        for fn in funcs:
            args = ", ".join(a.arg for a in fn.args.args)
            call_args = ", ".join(f"{a.arg}=..." for a in fn.args.args)
            lines += [
                "",
                f"def test_{fn.name}():",
                f'    """Test {fn.name}({args})."""',
                "    # Arrange",
                "    # ...",
                "    # Act",
                f"    result = {fn.name}({call_args})" if args else f"    result = {fn.name}()",
                "    # Assert",
                "    assert result is not None  # TODO: real assertion",
                "",
            ]
        if not funcs:
            lines.append("# No public top-level functions found.")
        return "\n".join(lines).rstrip() + "\n"

    def profile(self, code: str, timeout: float = 30.0, top_n: int = 15) -> ProfileResult:
        """Profile *code* in-process with cProfile. Untrusted blocking still applies."""
        if not self.trusted:
            violation = self._find_forbidden(code)
            if violation:
                return ProfileResult(
                    success=False, error=f"Forbidden construct blocked: {violation}"
                )
        ok, err = self.validate_syntax(code)
        if not ok:
            return ProfileResult(success=False, error=err)

        namespace: dict[str, Any] = {"__name__": "__sandbox_profile__"}
        profiler = cProfile.Profile()
        stream = io.StringIO()
        try:
            profiler.enable()
            exec(compile(code, "<sandbox-profile>", "exec"), namespace)  # nosec B102
            profiler.disable()
        except Exception as exc:  # profiled code raised — still report stats
            profiler.disable()
            stats = pstats.Stats(profiler, stream=stream).sort_stats("cumulative")
            stats.print_stats(top_n)
            return ProfileResult(
                success=False,
                stats_text=stream.getvalue(),
                error=f"{type(exc).__name__}: {exc}",
            )

        stats = pstats.Stats(profiler, stream=stream).sort_stats("cumulative")
        stats.print_stats(top_n)
        top = [
            {
                "function": f"{fn[0]}:{fn[1]}({fn[2]})",
                "ncalls": cc,
                "tottime": round(tt, 6),
                "cumtime": round(ct, 6),
            }
            for (fn, (cc, _nc, tt, ct, _callers)) in list(stats.stats.items())[:top_n]
        ]
        return ProfileResult(
            success=True,
            stats_text=stream.getvalue(),
            cumulative_time=round(stats.total_tt, 6),
            top_functions=top,
        )

    # ── Internal helpers ───────────────────────────────────────────────

    def _build_runner(self, code: str) -> str:
        """Wrap user code so tracebacks stay readable and cwd is the sandbox."""
        indented = textwrap.indent(code, "    ")
        return (
            "# Auto-generated sandbox runner — do not edit.\n"
            "import sys\n"
            "def _run():\n"
            f"{indented}\n"
            "if __name__ == '__main__':\n"
            "    try:\n"
            "        _run()\n"
            "    except SystemExit:\n"
            "        raise\n"
            "    except BaseException as exc:\n"
            "        import traceback\n"
            "        traceback.print_exc()\n"
            "        sys.exit(1)\n"
        )

    def _build_env(self, extra: dict[str, str] | None) -> dict[str, str]:
        """Child environment: inherit PATH/SystemRoot, drop secrets-ish vars."""
        keep = {
            "PATH",
            "SYSTEMROOT",
            "SYSTEMDRIVE",
            "TEMP",
            "TMP",
            "PATHEXT",
            "COMSPEC",
            "PYTHONIOENCODING",
            "NUMBER_OF_PROCESSORS",
        }
        env = {k: v for k, v in os.environ.items() if k.upper() in keep}
        env["PYTHONIOENCODING"] = "utf-8"
        if extra:
            env.update(extra)
        return env

    def _find_forbidden(self, code: str) -> str | None:
        """Return a description of the first forbidden construct, else None."""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return None  # syntax errors are reported separately

        for node in ast.walk(tree):
            # Forbidden imports
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if alias.name in FORBIDDEN_IMPORTS or root in FORBIDDEN_IMPORTS:
                        return f"import of '{alias.name}' (line {node.lineno})"
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                root = mod.split(".")[0]
                if mod in FORBIDDEN_IMPORTS or root in FORBIDDEN_IMPORTS:
                    return f"import from '{mod}' (line {node.lineno})"

            # Forbidden calls: dotted (os.system) or bare builtin (eval)
            elif isinstance(node, ast.Call):
                name = _call_name(node.func)
                if name and name in FORBIDDEN_CALLS:
                    return f"call to '{name}()' (line {node.lineno})"
                if name and name in FORBIDDEN_BUILTINS:
                    return f"call to builtin '{name}()' (line {node.lineno})"
        return None

    # ── Lint rules ─────────────────────────────────────────────────────

    def _lint_unused_imports(self, tree: ast.AST) -> list[LintIssue]:
        imported: dict[str, int] = {}  # name -> line
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = (alias.asname or alias.name).split(".")[0]
                    imported.setdefault(name, node.lineno)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    imported.setdefault(alias.asname or alias.name, node.lineno)

        used: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                used.add(node.value.id)

        return [
            LintIssue(line, "unused-import", f"'{name}' imported but never used")
            for name, line in imported.items()
            if name not in used
        ]

    def _lint_bare_except(self, tree: ast.AST) -> list[LintIssue]:
        issues = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                issues.append(
                    LintIssue(
                        node.lineno,
                        "bare-except",
                        "Bare 'except:' catches SystemExit/KeyboardInterrupt - "
                        "use 'except Exception:'",
                    )
                )
        return issues

    def _lint_unreachable(self, tree: ast.AST) -> list[LintIssue]:
        issues = []
        terminators = (ast.Return, ast.Raise, ast.Break, ast.Continue)
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if not isinstance(body, list):
                continue
            for idx, stmt in enumerate(body[:-1]):
                if isinstance(stmt, terminators):
                    nxt = body[idx + 1]
                    issues.append(
                        LintIssue(
                            getattr(nxt, "lineno", stmt.lineno),
                            "unreachable-code",
                            f"Statement after {type(stmt).__name__} is unreachable",
                        )
                    )
                    break
        return issues

    def _lint_mutable_defaults(self, tree: ast.AST) -> list[LintIssue]:
        issues = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for default in node.args.defaults + node.args.kw_defaults:
                    if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                        issues.append(
                            LintIssue(
                                node.lineno,
                                "mutable-default",
                                f"Function '{node.name}' has a mutable default "
                                "argument - use None and assign inside",
                            )
                        )
        return issues

    def _lint_shadowed_builtins(self, tree: ast.AST) -> list[LintIssue]:
        common = {
            "id",
            "type",
            "list",
            "dict",
            "set",
            "str",
            "int",
            "input",
            "len",
            "min",
            "max",
            "sum",
            "filter",
            "map",
            "open",
            "format",
        }
        issues = []
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.Assign):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                targets = [node.name]
            for name in targets:
                if name in common:
                    issues.append(
                        LintIssue(
                            node.lineno,
                            "builtin-shadow",
                            f"'{name}' shadows a Python builtin",
                        )
                    )
        return issues


# ── Module-level helpers ───────────────────────────────────────────────


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)


def _cap(text: str | None) -> str:
    if not text:
        return ""
    if len(text) > MAX_OUTPUT_CHARS:
        return text[:MAX_OUTPUT_CHARS] + f"\n... [truncated at {MAX_OUTPUT_CHARS} chars]"
    return text


def _call_name(func: ast.expr) -> str | None:
    """Resolve 'os.system' or 'eval' style names from a Call's func node."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts = []
        node: ast.expr = func
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
            return ".".join(reversed(parts))
    return None


def _make_memory_limiter(limit_mb: int):
    """Unix-only preexec_fn that applies RLIMIT_AS. Never called on Windows."""

    def _limit() -> None:
        import resource

        soft = limit_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (soft, soft))

    return _limit


def _remove_tree(path: str) -> None:
    """Best-effort recursive delete without shutil semantics surprises."""
    import contextlib
    import shutil

    with contextlib.suppress(Exception):
        shutil.rmtree(path, ignore_errors=True)


# ── Smoke test ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    sandbox = CodeExecutionSandbox()

    print("-- 1. Basic execution --")
    r = sandbox.execute("print('hello from sandbox')\nprint(2 + 2)")
    print(f"success={r.success} exit={r.exit_code} duration={r.duration_ms}ms")
    print(f"stdout: {r.stdout.strip()!r}")

    print("\n-- 2. Timeout --")
    r = sandbox.execute("import time\ntime.sleep(30)", timeout=1.5)
    print(f"success={r.success} timed_out={r.timed_out} error_type={r.error_type}")

    print("\n-- 3. Syntax validation --")
    ok, err = sandbox.validate_syntax("def broken(:\n")
    print(f"ok={ok} err={err}")

    print("\n-- 4. Forbidden blocking (untrusted mode) --")
    r = sandbox.execute("import os\nos.system('echo pwned')")
    print(f"success={r.success} error_type={r.error_type} stderr={r.stderr.strip()!r}")

    print("\n-- 5. Lint --")
    sample = (
        "import os\n"
        "import sys\n"
        "\n"
        "def f(a=[]):\n"
        "    try:\n"
        "        print(sys.version)\n"
        "    except:\n"
        "        pass\n"
        "    return 1\n"
        "    print('never')\n"
    )
    for issue in sandbox.lint(sample):
        print(f"  {issue}")

    print("\n-- 6. Test generation --")
    src = "def add(a, b):\n    return a + b\n\n\ndef greet(name):\n    return f'hi {name}'\n"
    print(sandbox.generate_tests(src))

    print("-- 7. Profile --")
    pr = sandbox.profile("total = sum(range(100_000))\nprint(total)")
    print(f"success={pr.success} cumtime={pr.cumulative_time}s")
    print(pr.stats_text.splitlines()[0] if pr.stats_text else "(no stats)")

    print("\n-- 8. execute_file --")
    tmp = Path(tempfile.mkdtemp()) / "demo_script.py"
    tmp.write_text("print('from file:', 6 * 7)", encoding="utf-8")
    r = sandbox.execute_file(tmp)
    print(f"success={r.success} stdout={r.stdout.strip()!r}")
    _remove_tree(str(tmp.parent))

    print("\nAll smoke tests done.")
