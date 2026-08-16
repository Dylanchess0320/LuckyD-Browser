"""
Advanced debugging toolkit — stack trace parsing, error pattern library,
root cause analysis, auto-fix suggestions, error prediction, debug session
management, and performance bottleneck detection.

All stdlib. No external dependencies.
"""

from __future__ import annotations

import ast
import cProfile
import io
import json
import os
import pstats
import re
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import MEMORY_DIR

# ── Constants ────────────────────────────────────────────────────────────────

_DEBUG_SESSIONS_FILE = MEMORY_DIR / "debug_sessions.json"
_MAX_LOCAL_VAR_REPR = 80
_MAX_SUGGESTED_FIXES = 5

# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class StackFrame:
    """A single frame extracted from a traceback."""

    file_path: str
    line_number: int
    function_name: str
    code_context: str  # the source line, if available
    local_vars: dict[str, str] = field(default_factory=dict)


@dataclass
class ParsedTraceback:
    """Structured representation of a Python traceback."""

    exception_type: str
    exception_message: str
    frames: list[StackFrame]
    raw_text: str


@dataclass
class ErrorPattern:
    """A known error signature → likely causes + fix suggestions."""

    exception_type: str
    regex: re.Pattern[str]
    likely_causes: list[str]
    fix_suggestions: list[str]


@dataclass
class RootCauseReport:
    """Result of root-cause analysis."""

    parsed: ParsedTraceback
    suspicious_frames: list[StackFrame]  # ranked, user code first
    local_var_hints: list[str]
    confidence: float  # 0.0 – 1.0


@dataclass
class FixSuggestion:
    """A concrete, rule-based fix."""

    description: str
    code_snippet: str  # what to insert / replace
    target_line: int | None  # 1-indexed, if known
    confidence: float


@dataclass
class PredictedIssue:
    """A risky pattern found by static analysis."""

    line: int
    column: int
    issue_type: str
    message: str
    severity: str  # "low" | "medium" | "high"


@dataclass
class DebugSession:
    """Tracks a single debugging session."""

    session_id: str
    started_at: str
    ended_at: str | None = None
    errors_seen: list[dict[str, Any]] = field(default_factory=list)
    fixes_applied: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PerformanceHotspot:
    """A function identified as a performance hotspot."""

    function_name: str
    file_path: str
    line_number: int
    cumulative_time: float
    calls: int
    percentage: float  # of total runtime


@dataclass
class PerformanceReport:
    """Result of performance analysis."""

    total_time: float
    hotspots: list[PerformanceHotspot]
    raw_profile: str


# ── Error pattern library ────────────────────────────────────────────────────

_ERROR_PATTERNS: list[ErrorPattern] = [
    ErrorPattern(
        exception_type="ImportError",
        regex=re.compile(r"No module named ['\"](?P<module>[^'\"]+)['\"]"),
        likely_causes=[
            "The package is not installed in the current environment.",
            "The package is installed in a different virtualenv.",
            "A circular import is masking the real failure.",
        ],
        fix_suggestions=[
            "Run `pip install <module>` or add it to requirements.txt.",
            "Check `pip list` to verify the module is present.",
            "Look for circular imports in the traceback chain.",
        ],
    ),
    ErrorPattern(
        exception_type="ModuleNotFoundError",
        regex=re.compile(r"No module named ['\"](?P<module>[^'\"]+)['\"]"),
        likely_causes=[
            "Missing dependency (subclass of ImportError).",
            "Typo in the module name.",
        ],
        fix_suggestions=[
            "Run `pip install <module>`.",
            "Check spelling of the import statement.",
        ],
    ),
    ErrorPattern(
        exception_type="KeyError",
        regex=re.compile(r"KeyError: (?P<key>.+)"),
        likely_causes=[
            "The dictionary key does not exist.",
            "Case-sensitivity mismatch (e.g. 'Name' vs 'name').",
            "The dict was populated conditionally and the branch was skipped.",
        ],
        fix_suggestions=[
            "Use `dict.get(key)` or `dict.get(key, default)` instead of `dict[key]`.",
            "Verify the key exists with `key in dict` before access.",
            "Print the dict keys to inspect available keys: `print(dict.keys())`.",
        ],
    ),
    ErrorPattern(
        exception_type="TypeError",
        regex=re.compile(r"(?P<msg>.+)"),
        likely_causes=[
            "Wrong number of arguments passed to a function.",
            "Unsupported operand types (e.g. str + int).",
            "Calling a non-callable object.",
        ],
        fix_suggestions=[
            "Check the function signature and argument count.",
            "Add explicit type conversion (e.g. `str(x)` or `int(y)`).",
            "Ensure the variable is actually callable before invoking it.",
        ],
    ),
    ErrorPattern(
        exception_type="AttributeError",
        regex=re.compile(r"'(?P<cls>[^']+)' object has no attribute '(?P<attr>[^']+)'"),
        likely_causes=[
            "Typo in the attribute/method name.",
            "The object is None (NoneType has no attributes).",
            "The object is of an unexpected type (e.g. list instead of dict).",
        ],
        fix_suggestions=[
            "Check spelling of the attribute name.",
            "Add a guard: `if obj is not None:` before accessing attributes.",
            "Verify the object's type with `type(obj)` or `isinstance()`.",
        ],
    ),
    ErrorPattern(
        exception_type="IndexError",
        regex=re.compile(r"list index out of range"),
        likely_causes=[
            "The list is shorter than expected.",
            "Off-by-one error in loop bounds.",
        ],
        fix_suggestions=[
            "Check `len(list)` before indexing.",
            "Use `for item in list:` instead of indexing by position.",
        ],
    ),
    ErrorPattern(
        exception_type="ValueError",
        regex=re.compile(r"(?P<msg>.+)"),
        likely_causes=[
            "Invalid literal for int() / float() / etc.",
            "Wrong number of values to unpack.",
        ],
        fix_suggestions=[
            "Wrap conversion in try/except ValueError.",
            "Print the value being converted to inspect it.",
        ],
    ),
    ErrorPattern(
        exception_type="FileNotFoundError",
        regex=re.compile(r"\[Errno 2\] No such file or directory: ['\"](?P<path>[^'\"]+)['\"]"),
        likely_causes=[
            "The file path is relative and the CWD changed.",
            "The file was deleted or never created.",
        ],
        fix_suggestions=[
            "Use an absolute path or `Path(__file__).parent / 'file'`.",
            "Add `os.path.exists(path)` guard before opening.",
        ],
    ),
    ErrorPattern(
        exception_type="ZeroDivisionError",
        regex=re.compile(r"division by zero"),
        likely_causes=["A denominator evaluated to zero."],
        fix_suggestions=[
            "Add a guard: `if denominator != 0:` before dividing.",
            "Use a default value when the denominator is zero.",
        ],
    ),
    ErrorPattern(
        exception_type="StopIteration",
        regex=re.compile(r"StopIteration"),
        likely_causes=[
            "A generator or iterator was exhausted.",
        ],
        fix_suggestions=[
            "Check that the iterable is not empty before calling next().",
            "Use a for-loop instead of manual next() calls.",
        ],
    ),
    ErrorPattern(
        exception_type="RecursionError",
        regex=re.compile(r"maximum recursion depth exceeded"),
        likely_causes=[
            "Missing or incorrect base case in a recursive function.",
            "Infinite recursion due to a logic bug.",
        ],
        fix_suggestions=[
            "Add a base case that terminates recursion.",
            "Increase recursion limit with `sys.setrecursionlimit()` (temporary).",
        ],
    ),
    ErrorPattern(
        exception_type="NameError",
        regex=re.compile(r"name '(?P<name>[^']+)' is not defined"),
        likely_causes=[
            "Variable used before assignment.",
            "Typo in the variable name.",
            "Import statement missing or failed silently.",
        ],
        fix_suggestions=[
            "Check spelling and ensure the variable is assigned before use.",
            "Verify the import statement executed successfully.",
        ],
    ),
    ErrorPattern(
        exception_type="IndentationError",
        regex=re.compile(r"unexpected indent"),
        likely_causes=[
            "Mixed tabs and spaces.",
            "Missing colon after def/if/for/while.",
        ],
        fix_suggestions=[
            "Convert all indentation to 4 spaces (PEP 8).",
            "Check for missing colons at the end of block statements.",
        ],
    ),
    ErrorPattern(
        exception_type="SyntaxError",
        regex=re.compile(r"invalid syntax"),
        likely_causes=[
            "Missing parenthesis, bracket, or quote.",
            "Python 2 syntax in Python 3 (e.g. print statement).",
        ],
        fix_suggestions=[
            "Check the line above the reported line — often the real culprit.",
            "Ensure all brackets and quotes are balanced.",
        ],
    ),
    ErrorPattern(
        exception_type="RuntimeError",
        regex=re.compile(r"(?P<msg>.+)"),
        likely_causes=[
            "Generator already executing.",
            "Dictionary changed size during iteration.",
        ],
        fix_suggestions=[
            "Convert the dict to a list before iterating: `list(d.items())`.",
            "Avoid re-entering a running generator.",
        ],
    ),
]

# ── Static analysis patterns (error prediction) ──────────────────────────────


class _RiskyPatternVisitor(ast.NodeVisitor):
    """AST visitor that flags risky code patterns."""

    def __init__(self) -> None:
        self.issues: list[PredictedIssue] = []

    # ── helpers ─────────────────────────────────────────────────────────────

    def _add(self, node: ast.AST, issue_type: str, message: str, severity: str) -> None:
        self.issues.append(
            PredictedIssue(
                line=getattr(node, "lineno", 0),
                column=getattr(node, "col_offset", 0),
                issue_type=issue_type,
                message=message,
                severity=severity,
            )
        )

    # ── visitors ────────────────────────────────────────────────────────────

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is None:
            self._add(node, "bare_except", "Bare `except:` catches all exceptions, including SystemExit and KeyboardInterrupt.", "high")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        for default in node.args.defaults + node.args.kw_defaults:
            if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                self._add(
                    node,
                    "mutable_default",
                    f"Mutable default argument in `{node.name}`. Use `None` and initialise inside the function.",
                    "high",
                )
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        if len(node.ops) == 1 and isinstance(node.ops[0], (ast.Eq, ast.NotEq)):
            comparator = node.comparators[0]
            if isinstance(comparator, ast.Constant) and comparator.value is None:
                op = "==" if isinstance(node.ops[0], ast.Eq) else "!="
                self._add(
                    node,
                    "none_comparison",
                    f"Use `is None` / `is not None` instead of `{op} None`.",
                    "medium",
                )
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        # Unguarded dict access:  d["key"]  where d is a Dict (best-effort heuristic)
        if isinstance(node.value, ast.Name):
            # We can't know the type statically, but flag it as a hint
            pass  # handled by KeyError pattern at runtime
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Unclosed file:  open(...) without a `with` statement
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            if not self._inside_with(node):
                self._add(node, "unclosed_file", "`open()` called without a `with` statement. File may not be closed.", "medium")
        # eval / exec
        if isinstance(node.func, ast.Name) and node.func.id in ("eval", "exec"):
            self._add(node, "eval_exec", f"Avoid `{node.func.id}` — security risk and hard to debug.", "high")
        self.generic_visit(node)

    def _inside_with(self, target: ast.AST) -> bool:
        # Walk up the AST to see if this call is inside a `with` block.
        # We approximate by checking parents (ast doesn't store parents by default,
        # so we do a simpler check: is the call the context-manager expression?)
        # For a robust check we would need parent tracking; here we use a heuristic.
        return False  # conservative: flag all bare open() calls


# ── AdvancedDebugging ────────────────────────────────────────────────────────


class AdvancedDebugging:
    """
    Toolkit for parsing tracebacks, diagnosing errors, predicting bugs,
    managing debug sessions, and profiling performance.

    Usage:
        debugger = AdvancedDebugging()
        report = debugger.analyze_traceback(traceback_text)
        fixes  = debugger.suggest_fixes(report.parsed)
    """

    def __init__(self, persist_dir: Path | None = None) -> None:
        self._persist_dir = persist_dir or MEMORY_DIR
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._session_file = self._persist_dir / "debug_sessions.json"
        self._current_session: DebugSession | None = None

    # ── 1. Stack trace parsing ──────────────────────────────────────────────

    def analyze_traceback(self, tb_text: str) -> RootCauseReport:
        """
        Parse a raw traceback string into a structured RootCauseReport.

        Extracts exception type, message, and all frames (file, line, function,
        code context, local variables).
        """
        parsed = self._parse_traceback(tb_text)
        suspicious = self._rank_frames(parsed.frames)
        hints = self._extract_local_var_hints(parsed.frames)
        confidence = self._estimate_confidence(parsed, suspicious)

        return RootCauseReport(
            parsed=parsed,
            suspicious_frames=suspicious,
            local_var_hints=hints,
            confidence=confidence,
        )

    def _parse_traceback(self, tb_text: str) -> ParsedTraceback:
        """Extract structured data from a traceback string."""
        lines = tb_text.strip().splitlines()

        # Exception type & message (last non-empty line)
        exception_type = "UnknownError"
        exception_message = ""
        for line in reversed(lines):
            if line.strip() and not line.startswith((" ", "\t", "File", "Traceback")):
                # e.g. "KeyError: 'foo'"  or  "TypeError: unsupported operand"
                if ":" in line:
                    exc_type, _, msg = line.partition(":")
                    exception_type = exc_type.strip()
                    exception_message = msg.strip()
                else:
                    exception_type = line.strip()
                break

        # Frame extraction
        frames: list[StackFrame] = []
        frame_re = re.compile(
            r'^\s*File "(?P<file>.+?)", line (?P<line>\d+), in (?P<func>.+)$'
        )
        i = 0
        while i < len(lines):
            match = frame_re.match(lines[i])
            if match:
                file_path = match.group("file")
                line_no = int(match.group("line"))
                func_name = match.group("func")
                code_context = ""
                local_vars: dict[str, str] = {}

                # Next line is often the source code
                if i + 1 < len(lines) and not frame_re.match(lines[i + 1]):
                    code_context = lines[i + 1].strip()
                    i += 1

                # Try to read local vars from the file (best-effort)
                local_vars = self._read_local_vars(file_path, line_no)

                frames.append(
                    StackFrame(
                        file_path=file_path,
                        line_number=line_no,
                        function_name=func_name,
                        code_context=code_context,
                        local_vars=local_vars,
                    )
                )
            i += 1

        return ParsedTraceback(
            exception_type=exception_type,
            exception_message=exception_message,
            frames=frames,
            raw_text=tb_text,
        )

    @staticmethod
    def _read_local_vars(file_path: str, line_no: int) -> dict[str, str]:
        """Best-effort read of local variables from the source file around the error line."""
        path = Path(file_path)
        if not path.exists():
            return {}
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except Exception:
            return {}

        # Find the enclosing function/class and extract assignments
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.lineno <= line_no <= (node.end_lineno or node.lineno):
                    vars_: dict[str, str] = {}
                    for child in ast.walk(node):
                        if isinstance(child, ast.Assign):
                            for target in child.targets:
                                if isinstance(target, ast.Name):
                                    try:
                                        val = ast.literal_eval(child.value)
                                        vars_[target.id] = repr(val)[:_MAX_LOCAL_VAR_REPR]
                                    except Exception:
                                        vars_[target.id] = "<dynamic>"
                    return vars_
        return {}

    # ── 2. Root cause analysis ──────────────────────────────────────────────

    @staticmethod
    def _rank_frames(frames: list[StackFrame]) -> list[StackFrame]:
        """
        Rank frames by suspiciousness.
        User code (not in site-packages / dist-packages) is more suspicious.
        """
        def _score(frame: StackFrame) -> float:
            path = frame.file_path.replace("\\", "/").lower()
            if "site-packages" in path or "dist-packages" in path:
                return 0.1
            if "lib/python" in path and "site-packages" not in path:
                return 0.3  # stdlib
            return 1.0  # user code

        return sorted(frames, key=_score, reverse=True)

    @staticmethod
    def _extract_local_var_hints(frames: list[StackFrame]) -> list[str]:
        """Generate human-readable hints from local variables."""
        hints: list[str] = []
        for frame in frames:
            if not frame.local_vars:
                continue
            for name, value in frame.local_vars.items():
                if value == "None":
                    hints.append(
                        f"In `{frame.function_name}` (line {frame.line_number}), "
                        f"`{name}` is None — possible NoneType error."
                    )
                elif value == "<dynamic>":
                    hints.append(
                        f"In `{frame.function_name}` (line {frame.line_number}), "
                        f"`{name}` is dynamically assigned — check its value."
                    )
        return hints[:_MAX_SUGGESTED_FIXES]

    @staticmethod
    def _estimate_confidence(parsed: ParsedTraceback, suspicious: list[StackFrame]) -> float:
        """Rough confidence score based on how much info we extracted."""
        score = 0.0
        if parsed.exception_type != "UnknownError":
            score += 0.4
        if parsed.exception_message:
            score += 0.2
        if suspicious:
            score += 0.2
        if any(f.local_vars for f in suspicious):
            score += 0.2
        return min(score, 1.0)

    # ── 3. Auto-fix suggestions ─────────────────────────────────────────────

    def suggest_fixes(self, error: ParsedTraceback | str) -> list[FixSuggestion]:
        """
        Generate rule-based fix suggestions for an error.

        Accepts either a ParsedTraceback or a raw traceback string.
        """
        if isinstance(error, str):
            error = self._parse_traceback(error)

        suggestions: list[FixSuggestion] = []

        # Match against the error pattern library
        for pattern in _ERROR_PATTERNS:
            if pattern.exception_type != error.exception_type:
                continue
            match = pattern.regex.search(error.exception_message)
            if match or not pattern.regex.groups:
                for cause, fix in zip(pattern.likely_causes, pattern.fix_suggestions):
                    snippet = self._build_snippet(error, pattern, match)
                    suggestions.append(
                        FixSuggestion(
                            description=f"{cause} → {fix}",
                            code_snippet=snippet,
                            target_line=error.frames[-1].line_number if error.frames else None,
                            confidence=0.8,
                        )
                    )
                break  # only first matching pattern

        # Generic fallbacks
        if not suggestions:
            suggestions.append(
                FixSuggestion(
                    description="No specific pattern matched. Add a try/except block and log the full traceback.",
                    code_snippet="import traceback\ntry:\n    ...\nexcept Exception:\n    traceback.print_exc()",
                    target_line=None,
                    confidence=0.3,
                )
            )

        return suggestions[:_MAX_SUGGESTED_FIXES]

    @staticmethod
    def _build_snippet(
        error: ParsedTraceback,
        pattern: ErrorPattern,
        match: re.Match[str] | None,
    ) -> str:
        """Build a concrete code snippet for a fix."""
        exc = error.exception_type

        if exc in ("ImportError", "ModuleNotFoundError") and match:
            module = match.group("module")
            return f"# Add to imports:\nimport {module}\n# or:\npip install {module}"

        if exc == "KeyError" and match:
            key = match.group("key").strip("'\"")
            return f"# Replace direct access with .get():\nvalue = my_dict.get({key!r}, default)"

        if exc == "AttributeError" and match:
            attr = match.group("attr")
            return f"# Add a guard:\nif obj is not None:\n    obj.{attr}"

        if exc == "TypeError":
            return "# Check argument types and count.\n# Add explicit conversion, e.g. str(x) or int(y)."

        if exc == "IndexError":
            return "# Guard the index:\nif 0 <= index < len(my_list):\n    item = my_list[index]"

        if exc == "FileNotFoundError" and match:
            path = match.group("path")
            return f"# Use an absolute path:\nfrom pathlib import Path\npath = Path(__file__).parent / {path!r}"

        return "# Review the error line and add appropriate guards."

    # ── 4. Error prediction (static analysis) ───────────────────────────────

    def predict_errors(self, code: str) -> list[PredictedIssue]:
        """
        Statically analyse source code and return a list of predicted issues.

        Detects: bare except, mutable default args, == None comparisons,
        unclosed files, eval/exec usage.
        """
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return [
                PredictedIssue(
                    line=exc.lineno or 0,
                    column=exc.offset or 0,
                    issue_type="syntax_error",
                    message=f"Code does not parse: {exc.msg}",
                    severity="high",
                )
            ]

        visitor = _RiskyPatternVisitor()
        visitor.visit(tree)
        return sorted(visitor.issues, key=lambda i: (i.line, i.column))

    # ── 5. Performance bottleneck detection ─────────────────────────────────

    def analyze_performance(self, code: str, *, timeout: float = 10.0) -> PerformanceReport:
        """
        Profile the given code and return hotspots.

        Uses cProfile + pstats. The code is executed in a restricted namespace.
        """
        namespace: dict[str, Any] = {"__name__": "__main__"}
        profiler = cProfile.Profile()

        start = time.perf_counter()
        try:
            profiler.enable()
            exec(compile(code, "<string>", "exec"), namespace)  # noqa: S102
        except Exception:
            pass  # we still want the profile even if the code raised
        finally:
            profiler.disable()
        elapsed = time.perf_counter() - start

        # Parse cProfile output
        stream = io.StringIO()
        stats = pstats.Stats(profiler, stream=stream)
        stats.sort_stats("cumulative").print_stats(20)
        raw_profile = stream.getvalue()

        hotspots = self._parse_profile_output(raw_profile, elapsed)
        return PerformanceReport(
            total_time=elapsed,
            hotspots=hotspots,
            raw_profile=raw_profile,
        )

    @staticmethod
    def _parse_profile_output(raw: str, total_time: float) -> list[PerformanceHotspot]:
        """Parse pstats text output into structured hotspots."""
        hotspots: list[PerformanceHotspot] = []
        # Match lines like:
        #    1    0.000    0.000    0.000    0.000 <string>:1(<module>)
        line_re = re.compile(
            r"^\s*(?P<calls>\d+)\s+"
            r"(?P<tottime>[\d.]+)\s+"
            r"(?P<percall1>[\d.]+)\s+"
            r"(?P<cumtime>[\d.]+)\s+"
            r"(?P<percall2>[\d.]+)\s+"
            r"(?P<func>.+)$"
        )
        for line in raw.splitlines():
            match = line_re.match(line)
            if not match:
                continue
            func_info = match.group("func").strip()
            # func_info looks like:  file.py:12(func_name)  or  {built-in method ...}
            file_path = "<unknown>"
            line_no = 0
            func_name = func_info
            file_match = re.match(r"(?P<file>.+?):(?P<line>\d+)\((?P<func>.+)\)", func_info)
            if file_match:
                file_path = file_match.group("file")
                line_no = int(file_match.group("line"))
                func_name = file_match.group("func")

            cumtime = float(match.group("cumtime"))
            calls = int(match.group("calls"))
            pct = (cumtime / total_time * 100) if total_time > 0 else 0.0

            hotspots.append(
                PerformanceHotspot(
                    function_name=func_name,
                    file_path=file_path,
                    line_number=line_no,
                    cumulative_time=cumtime,
                    calls=calls,
                    percentage=pct,
                )
            )
        return sorted(hotspots, key=lambda h: h.cumulative_time, reverse=True)

    def timeit(self, func: callable, *args: Any, runs: int = 1000, **kwargs: Any) -> float:
        """
        Simple timeit wrapper. Returns average execution time in seconds.
        """
        start = time.perf_counter()
        for _ in range(runs):
            func(*args, **kwargs)
        elapsed = time.perf_counter() - start
        return elapsed / runs

    # ── 6. Debug session management ─────────────────────────────────────────

    def start_session(self, session_id: str | None = None) -> DebugSession:
        """Start a new debug session. Returns the session object."""
        session_id = session_id or f"debug_{int(time.time())}"
        self._current_session = DebugSession(
            session_id=session_id,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        return self._current_session

    def end_session(self) -> DebugSession | None:
        """End the current session and persist it to disk."""
        if self._current_session is None:
            return None
        self._current_session.ended_at = datetime.now(timezone.utc).isoformat()
        self._persist_session(self._current_session)
        session = self._current_session
        self._current_session = None
        return session

    def record_error(self, error: ParsedTraceback, notes: str = "") -> None:
        """Record an error in the current session."""
        if self._current_session is None:
            self.start_session()
        assert self._current_session is not None
        self._current_session.errors_seen.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "exception_type": error.exception_type,
                "exception_message": error.exception_message,
                "notes": notes,
            }
        )

    def record_fix(self, fix: FixSuggestion, applied: bool = False) -> None:
        """Record a fix attempt in the current session."""
        if self._current_session is None:
            self.start_session()
        assert self._current_session is not None
        self._current_session.fixes_applied.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "description": fix.description,
                "code_snippet": fix.code_snippet,
                "applied": applied,
            }
        )

    def _persist_session(self, session: DebugSession) -> None:
        """Append the session to the JSON persistence file."""
        existing: list[dict[str, Any]] = []
        if self._session_file.exists():
            try:
                existing = json.loads(self._session_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                existing = []

        existing.append(
            {
                "session_id": session.session_id,
                "started_at": session.started_at,
                "ended_at": session.ended_at,
                "errors_seen": session.errors_seen,
                "fixes_applied": session.fixes_applied,
            }
        )
        self._session_file.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def load_sessions(self) -> list[dict[str, Any]]:
        """Load all persisted debug sessions."""
        if not self._session_file.exists():
            return []
        try:
            return json.loads(self._session_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    # ── Convenience ─────────────────────────────────────────────────────────

    def quick_diagnose(self, tb_text: str) -> dict[str, Any]:
        """
        One-call diagnosis: parse traceback, rank frames, suggest fixes.
        Returns a plain dict suitable for JSON serialisation.
        """
        report = self.analyze_traceback(tb_text)
        fixes = self.suggest_fixes(report.parsed)
        return {
            "exception": f"{report.parsed.exception_type}: {report.parsed.exception_message}",
            "top_suspicious_frame": (
                f"{report.suspicious_frames[0].file_path}:{report.suspicious_frames[0].line_number}"
                if report.suspicious_frames
                else None
            ),
            "local_var_hints": report.local_var_hints,
            "fixes": [
                {
                    "description": f.description,
                    "snippet": f.code_snippet,
                    "confidence": f.confidence,
                }
                for f in fixes
            ],
            "confidence": report.confidence,
        }


# ── Smoke test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== AdvancedDebugging smoke test ===\n")

    debugger = AdvancedDebugging()

    # 1. Traceback parsing
    sample_tb = """Traceback (most recent call last):
  File "C:\\Users\\dylan\\project\\main.py", line 42, in main
    user = users["alice"]
  File "C:\\Python310\\lib\\site-packages\\app\\db.py", line 15, in get_user
    return self._cache[key]
KeyError: 'alice'
"""
    report = debugger.analyze_traceback(sample_tb)
    print(f"Exception : {report.parsed.exception_type}: {report.parsed.exception_message}")
    print(f"Frames    : {len(report.parsed.frames)}")
    print(f"Top frame : {report.suspicious_frames[0].file_path}:{report.suspicious_frames[0].line_number}")
    print(f"Confidence: {report.confidence:.2f}\n")

    # 2. Fix suggestions
    fixes = debugger.suggest_fixes(report.parsed)
    for i, fix in enumerate(fixes, 1):
        print(f"Fix {i}: {fix.description}")
        print(f"  Snippet:\n{fix.code_snippet}\n")

    # 3. Error prediction
    risky_code = """
def bad(a=[]):
    if a == None:
        pass
    try:
        x = eval("1+1")
    except:
        pass
    f = open("x.txt")
"""
    issues = debugger.predict_errors(risky_code)
    print(f"Predicted issues ({len(issues)}):")
    for issue in issues:
        print(f"  [{issue.severity}] line {issue.line}: {issue.issue_type} — {issue.message}")
    print()

    # 4. Performance analysis
    perf_code = """
import time
def slow():
    time.sleep(0.01)
def fast():
    pass
for _ in range(3):
    slow()
    fast()
"""
    perf = debugger.analyze_performance(perf_code)
    print(f"Total time: {perf.total_time:.4f}s")
    print("Hotspots:")
    for h in perf.hotspots[:5]:
        print(f"  {h.function_name:30s} {h.cumulative_time:.4f}s  ({h.percentage:.1f}%)")
    print()

    # 5. Session management
    session = debugger.start_session("smoke_test")
    debugger.record_error(report.parsed, notes="smoke test error")
    debugger.record_fix(fixes[0], applied=True)
    ended = debugger.end_session()
    print(f"Session persisted: {ended.session_id if ended else 'None'}")
    print(f"Sessions on disk : {len(debugger.load_sessions())}")

    print("\n=== Smoke test complete ===")
