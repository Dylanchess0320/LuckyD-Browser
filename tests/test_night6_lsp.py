"""Night-6 wave-2 tests: tools/lsp_tools.py.

jedi is NOT installed in this environment, so these tests cover:
  1. The real no-jedi error paths (every tool degrades to an error ToolOutput).
  2. _resolve_path behavior.
  3. Output formatting / parsing / rename-mutation logic with a stubbed
     jedi module (monkeypatched _get_jedi), asserting real behavior of the
     tool code: truncation limits, title/metadata shaping, bottom-to-top
     rename edits, workspace search filtering and skip-dirs.
"""

from __future__ import annotations

import sys
import types

import pytest

import tools.lsp_tools as lsp_mod
from tools.registry import registry


def _ok(result) -> bool:
    return not bool(getattr(result, "error", False))


def _tool(name):
    t = registry.get(name)
    assert t is not None, f"tool {name} not registered"
    return t


# ── no-jedi behavior (jedi genuinely missing here) ─────────────────────


class TestNoJedi:
    def test_get_jedi_raises_runtime_error(self):
        assert "jedi" not in sys.modules
        with pytest.raises(RuntimeError, match="jedi not installed"):
            lsp_mod._get_jedi()

    async def test_all_tools_error_without_jedi(self, tmp_path):
        f = tmp_path / "x.py"
        f.write_text("x = 1\n")
        cases = [
            ("LspDefinition", {"file_path": str(f), "line": 1}),
            ("LspReferences", {"file_path": str(f), "line": 1}),
            ("LspHover", {"file_path": str(f), "line": 1}),
            ("LspRename", {"file_path": str(f), "line": 1, "new_name": "y"}),
            ("LspDocumentSymbols", {"file_path": str(f)}),
            ("LspImplementation", {"file_path": str(f), "line": 1}),
            ("LspIncomingCalls", {"file_path": str(f), "line": 1}),
            ("LspOutgoingCalls", {"file_path": str(f), "line": 1}),
        ]
        for name, kw in cases:
            r = await _tool(name).execute(**kw)
            assert not _ok(r), name
            assert "jedi not installed" in r.text, name

    async def test_workspace_symbols_errors_without_jedi(self):
        r = await _tool("LspWorkspaceSymbols").execute(query="x")
        assert not _ok(r)
        assert "jedi" in r.text.lower()

    async def test_definition_missing_file(self, tmp_path):
        # _get_jedi() runs before the file-exists check, so without jedi the
        # import error wins even for a missing file.
        r = await _tool("LspDefinition").execute(file_path=str(tmp_path / "nope.py"), line=1)
        assert not _ok(r)
        assert "jedi not installed" in r.text


class TestResolvePath:
    def test_expands_user_and_resolves(self, tmp_path, monkeypatch):
        # POSIX expanduser reads $HOME; Windows reads %USERPROFILE% instead.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        p = lsp_mod._resolve_path("~/sub/../x.py")
        assert p == (tmp_path / "x.py").resolve()
        assert str(p).startswith(str(tmp_path))


# ── stubbed jedi ───────────────────────────────────────────────────────


class FakeName:
    def __init__(self, **kw):
        self._doc = kw.pop("docstring", "")
        for k, v in kw.items():
            setattr(self, k, v)

    def docstring(self):
        return self._doc

    def get_line_code(self):
        return ""


class FakeScript:
    """Configurable stand-in for jedi.Script."""

    goto_results = []
    references_results = []
    help_results = []
    names_results = []
    context_result = None

    def __init__(self, code=None, path=None):
        self.code = code
        self.path = path

    def goto(self, line=None, column=None):
        return list(type(self).goto_results)

    def get_references(self, line=None, column=None, include_builtins=True):
        return list(type(self).references_results)

    def help(self, line=None, column=None):
        return list(type(self).help_results)

    def get_names(self, all_scopes=False, definitions=False):
        return list(type(self).names_results)

    def get_context(self, line=None, column=None):
        return type(self).context_result


@pytest.fixture
def fake_jedi(monkeypatch):
    mod = types.ModuleType("jedi")
    mod.Script = FakeScript
    FakeScript.goto_results = []
    FakeScript.references_results = []
    FakeScript.help_results = []
    FakeScript.names_results = []
    FakeScript.context_result = None
    monkeypatch.setattr(lsp_mod, "_get_jedi", lambda: mod)
    return mod


def _write(tmp_path, name="mod.py", content="x = 1\n"):
    f = tmp_path / name
    f.write_text(content)
    return f


class TestDefinition:
    async def test_no_results(self, tmp_path, fake_jedi):
        f = _write(tmp_path)
        r = await _tool("LspDefinition").execute(file_path=str(f), line=1)
        assert _ok(r)
        assert r.text == "No definition found."

    async def test_formats_results(self, tmp_path, fake_jedi):
        f = _write(tmp_path)
        FakeScript.goto_results = [
            FakeName(
                full_name="mod.CONST",
                module_path=str(f),
                line=3,
                docstring="d" * 500,
            )
        ]
        r = await _tool("LspDefinition").execute(file_path=str(f), line=1)
        assert _ok(r)
        assert f"mod.CONST → {f}:3" in r.text
        assert ("d" * 200) in r.text  # docstring truncated to 200
        assert ("d" * 201) not in r.text
        assert r.title == "Definition: mod.CONST"
        assert r.metadata == {"count": 1, "symbol": "mod.CONST"}

    async def test_none_module_path_falls_back_to_queried_file(self, tmp_path, fake_jedi):
        f = _write(tmp_path)
        FakeScript.goto_results = [FakeName(full_name="x", module_path=None, line=1, docstring="")]
        r = await _tool("LspDefinition").execute(file_path=str(f), line=1)
        assert f"→ {f}:1" in r.text

    async def test_jedi_exception_becomes_error(self, tmp_path, fake_jedi, monkeypatch):
        f = _write(tmp_path)

        def boom(*a, **k):
            raise ValueError("jedi exploded")

        monkeypatch.setattr(FakeScript, "goto", boom)
        r = await _tool("LspDefinition").execute(file_path=str(f), line=1)
        assert not _ok(r)
        assert "LSP error: jedi exploded" in r.text


class TestReferences:
    async def test_no_results(self, tmp_path, fake_jedi):
        f = _write(tmp_path)
        r = await _tool("LspReferences").execute(file_path=str(f), line=1)
        assert _ok(r)
        assert "No references found." in r.text

    async def test_truncates_at_30(self, tmp_path, fake_jedi):
        f = _write(tmp_path)
        FakeScript.references_results = [
            FakeName(module_path=str(f), line=i + 1, column=0, code=f"code line {i}")
            for i in range(35)
        ]
        r = await _tool("LspReferences").execute(file_path=str(f), line=1)
        assert _ok(r)
        assert r.title == "35 References"
        assert r.metadata == {"count": 35}
        assert "... and 5 more" in r.text
        assert "code line 34" not in r.text  # beyond the 30 shown

    async def test_code_truncated_to_120(self, tmp_path, fake_jedi):
        f = _write(tmp_path)
        FakeScript.references_results = [
            FakeName(module_path=str(f), line=1, column=2, code="z" * 200)
        ]
        r = await _tool("LspReferences").execute(file_path=str(f), line=1)
        assert ("z" * 120) in r.text
        assert ("z" * 121) not in r.text


class TestHover:
    async def test_no_results(self, tmp_path, fake_jedi):
        f = _write(tmp_path)
        r = await _tool("LspHover").execute(file_path=str(f), line=1)
        assert _ok(r)
        assert "No type info found." in r.text

    async def test_formats_type_info(self, tmp_path, fake_jedi):
        f = _write(tmp_path)
        FakeScript.help_results = [
            FakeName(name="myfunc", type="function", docstring="docs here " * 100)
        ]
        r = await _tool("LspHover").execute(file_path=str(f), line=1)
        assert _ok(r)
        assert "Name: myfunc" in r.text
        assert "Type: function" in r.text
        assert r.title == "Type Info: myfunc"
        assert r.metadata == {"name": "myfunc", "type": "function"}
        assert len(r.text) < 600  # docstring truncated to 500


class TestRename:
    async def test_no_refs_errors(self, tmp_path, fake_jedi):
        f = _write(tmp_path)
        r = await _tool("LspRename").execute(file_path=str(f), line=1, new_name="y")
        assert not _ok(r)
        assert "No references found to rename." in r.text

    async def test_renames_all_occurrences_in_file(self, tmp_path, fake_jedi):
        content = "def oldname():\n    return 1\n\n\nx = oldname()\ny = oldname()\n"
        f = _write(tmp_path, content=content)
        # jedi columns are 0-based starts of the name
        FakeScript.references_results = [
            FakeName(name="oldname", module_path=str(f), line=1, column=4),
            FakeName(name="oldname", module_path=str(f), line=5, column=4),
            FakeName(name="oldname", module_path=str(f), line=6, column=4),
        ]
        r = await _tool("LspRename").execute(
            file_path=str(f), line=1, character=4, new_name="newname"
        )
        assert _ok(r)
        assert r.metadata == {
            "old_name": "oldname",
            "new_name": "newname",
            "changes": 3,
            "files": 1,
        }
        assert "Renamed 'oldname' → 'newname' in 3 location(s)" in r.text
        new_content = f.read_text()
        assert "oldname" not in new_content
        assert new_content.count("newname") == 3
        assert "def newname():" in new_content
        assert "x = newname()" in new_content

    async def test_refs_without_module_path_skipped(self, tmp_path, fake_jedi):
        f = _write(tmp_path, content="val = 1\nprint(val)\n")
        FakeScript.references_results = [
            FakeName(name="val", module_path=None, line=1, column=0),
            FakeName(name="val", module_path=str(f), line=1, column=0),
            FakeName(name="val", module_path=str(f), line=2, column=6),
        ]
        r = await _tool("LspRename").execute(file_path=str(f), line=1, new_name="renamed")
        assert _ok(r)
        assert r.metadata["changes"] == 2
        assert r.metadata["files"] == 1


class TestDocumentSymbols:
    async def test_outline_format(self, tmp_path, fake_jedi):
        f = _write(tmp_path)
        FakeScript.names_results = [
            FakeName(name="Foo", type="class", full_name="mod.Foo", line=1),
            FakeName(name="bar", type="function", full_name="mod.Foo.bar", line=2),
        ]
        r = await _tool("LspDocumentSymbols").execute(file_path=str(f))
        assert _ok(r)
        assert "class: Foo (line 1)" in r.text
        # indent = 2 spaces per dot in full_name
        assert "    function: bar (line 2)" in r.text
        assert r.title == f"Symbols in {f.name} (2 symbols)"
        assert r.metadata == {"count": 2}

    async def test_caps_at_100(self, tmp_path, fake_jedi):
        f = _write(tmp_path)
        FakeScript.names_results = [
            FakeName(name=f"n{i}", type="function", full_name=f"m.n{i}", line=i) for i in range(120)
        ]
        r = await _tool("LspDocumentSymbols").execute(file_path=str(f))
        assert r.metadata["count"] == 120
        assert r.text.count("\n") == 99  # only first 100 lines shown


class TestWorkspaceSymbols:
    async def test_search_filters_and_skips_dirs(self, tmp_path, fake_jedi, monkeypatch):
        real = tmp_path / "real.py"
        real.write_text("def target_func():\n    pass\n")
        (tmp_path / ".venv").mkdir()
        (tmp_path / ".venv" / "skip.py").write_text("def target_func():\n    pass\n")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "skip2.py").write_text("x = 1\n")

        def script_factory(code=None, path=None):
            from pathlib import Path as _Path

            class S:
                def get_names(self, all_scopes=False):
                    # only the file NAME may signal a skipped dir: the tmp dir
                    # itself is named after this test ("..._skips_...").
                    if _Path(str(path)).name.startswith("skip"):
                        raise AssertionError("skipped dir was scanned")
                    return [
                        FakeName(name="target_func", type="function", line=1),
                        FakeName(name="other_thing", type="function", line=2),
                    ]

            return S()

        fake_mod = types.ModuleType("jedi")
        fake_mod.Script = script_factory
        monkeypatch.setitem(sys.modules, "jedi", fake_mod)
        monkeypatch.chdir(tmp_path)

        r = await _tool("LspWorkspaceSymbols").execute(query="target")
        assert _ok(r)
        assert "target_func" in r.text
        assert "other_thing" not in r.text  # filtered by query
        assert r.metadata["query"] == "target"

        r = await _tool("LspWorkspaceSymbols").execute(query="")
        assert "other_thing" in r.text  # empty query matches all

    async def test_no_matches_message(self, tmp_path, fake_jedi, monkeypatch):
        (tmp_path / "a.py").write_text("x = 1\n")

        def script_factory(code=None, path=None):
            class S:
                def get_names(self, all_scopes=False):
                    return [FakeName(name="zzz", type="function", line=1)]

            return S()

        fake_mod = types.ModuleType("jedi")
        fake_mod.Script = script_factory
        monkeypatch.setitem(sys.modules, "jedi", fake_mod)
        monkeypatch.chdir(tmp_path)

        r = await _tool("LspWorkspaceSymbols").execute(query="qqq-no-match")
        assert _ok(r)
        assert "No symbols found matching 'qqq-no-match'" in r.text


class TestImplementation:
    async def test_no_goto_results(self, tmp_path, fake_jedi):
        f = _write(tmp_path)
        r = await _tool("LspImplementation").execute(file_path=str(f), line=1)
        assert _ok(r)
        assert "No implementations found." in r.text

    async def test_finds_impls_in_class_bodies(self, tmp_path, fake_jedi, monkeypatch):
        src = tmp_path / "shapes.py"
        src.write_text(
            "class Circle:\n    def area(self):\n        return 1\n\n\n"
            "class Square:\n    def area(self):\n        return 2\n"
        )
        (tmp_path / "unrelated.py").write_text("def area():\n    pass\n")
        FakeScript.goto_results = [FakeName(name="area")]
        monkeypatch.chdir(tmp_path)
        r = await _tool("LspImplementation").execute(file_path=str(src), line=2)
        assert _ok(r)
        assert "Implementations of 'area':" in r.text
        assert "shapes.py" in r.text
        assert "unrelated.py" not in r.text  # module-level def is not in a class
        assert r.metadata["count"] == 2

    async def test_no_impls_found_message(self, tmp_path, fake_jedi, monkeypatch):
        src = tmp_path / "s.py"
        src.write_text("x = 1\n")
        FakeScript.goto_results = [FakeName(name="unique_method_xyz")]
        monkeypatch.chdir(tmp_path)
        r = await _tool("LspImplementation").execute(file_path=str(src), line=1)
        assert _ok(r)
        assert "No implementations found for 'unique_method_xyz'." in r.text


class TestIncomingCalls:
    async def test_no_results(self, tmp_path, fake_jedi):
        f = _write(tmp_path)
        r = await _tool("LspIncomingCalls").execute(file_path=str(f), line=1)
        assert _ok(r)
        assert "No callers found." in r.text

    async def test_formats_callers(self, tmp_path, fake_jedi):
        f = _write(tmp_path)
        FakeScript.goto_results = [FakeName(name="helper")]
        FakeScript.references_results = [
            FakeName(module_path=str(f), line=9, column=4, code="x" * 200)
        ]
        r = await _tool("LspIncomingCalls").execute(file_path=str(f), line=1)
        assert _ok(r)
        assert "Callers of 'helper':" in r.text
        assert r.title == "Callers of helper"
        assert r.metadata == {"count": 1}
        assert ("x" * 120) in r.text  # code truncated to 120 chars
        assert ("x" * 121) not in r.text


class TestOutgoingCalls:
    async def test_not_inside_function(self, tmp_path, fake_jedi):
        f = _write(tmp_path)
        FakeScript.context_result = None
        r = await _tool("LspOutgoingCalls").execute(file_path=str(f), line=1)
        assert _ok(r)
        assert "Not inside a function." in r.text

    async def test_not_a_function_context(self, tmp_path, fake_jedi):
        f = _write(tmp_path)
        FakeScript.context_result = FakeName(name="C", type="class", line=1)
        r = await _tool("LspOutgoingCalls").execute(file_path=str(f), line=1)
        assert "Not inside a function." in r.text

    async def test_lists_callees(self, tmp_path, fake_jedi):
        f = _write(tmp_path)
        FakeScript.context_result = FakeName(name="outer", type="function", line=10)
        FakeScript.names_results = [
            FakeName(name="inner", type="function", full_name="mod.inner", line=20),
            FakeName(name="far", type="function", full_name="mod.far", line=500),
            FakeName(name="CONST", type="statement", full_name="mod.CONST", line=30),
        ]
        r = await _tool("LspOutgoingCalls").execute(file_path=str(f), line=12)
        assert _ok(r)
        assert "Callees from 'outer':" in r.text
        assert "mod.inner" in r.text
        assert "mod.far" not in r.text  # outside the rough 100-line window
        assert "CONST" not in r.text  # not a function
