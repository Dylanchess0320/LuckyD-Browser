"""Night-6 wave-2 tests: tools/file_tools.py.

Real filesystem via tmp_path; no network. Covers: sensitive-path refusal
(read/write/edit), Read ranges/truncation/errors, Write create/update and
checkpoint recording, Edit uniqueness/replace_all validation, Glob
ignore-dir filtering and 200-match cap, Grep output modes/regex errors.
"""

from __future__ import annotations

import sys
import types

import pytest

import tools.file_tools as ft
from tools.registry import registry


def _ok(result) -> bool:
    return not bool(getattr(result, "error", False))


def _tool(name):
    t = registry.get(name)
    assert t is not None, f"tool {name} not registered"
    return t


@pytest.fixture
def fake_checkpoint(monkeypatch):
    """Capture checkpoint.record_edit calls made by Write/Edit tools."""
    recorded = []
    mod = types.ModuleType("core.checkpoint")
    mod.get_checkpoint_manager = lambda: types.SimpleNamespace(
        record_edit=lambda *a: recorded.append(a)
    )
    pkg = types.ModuleType("core")
    pkg.checkpoint = mod
    monkeypatch.setitem(sys.modules, "core", pkg)
    monkeypatch.setitem(sys.modules, "core.checkpoint", mod)
    return recorded


# ── sensitive paths ────────────────────────────────────────────────────


class TestSensitivePaths:
    @pytest.mark.parametrize(
        "rel",
        [
            ".ssh/config",
            ".env",
            ".aws/credentials",
            "sub/id_rsa",
            ".netrc",
            ".gnupg/pubring.kbx",
            "x/credentials",
        ],
    )
    def test_is_sensitive(self, rel):
        from pathlib import Path

        assert ft._is_sensitive_path(Path("/tmp") / rel)

    def test_normal_path_ok(self):
        from pathlib import Path

        assert not ft._is_sensitive_path(Path("/tmp/project/main.py"))

    async def test_read_refused(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("SECRET=1")
        r = await _tool("Read").execute(file_path=str(f))
        assert not _ok(r)
        assert "Refusing to read credential/system path" in r.text

    async def test_write_refused(self, tmp_path):
        r = await _tool("Write").execute(file_path=str(tmp_path / ".ssh" / "config"), content="x")
        assert not _ok(r)
        assert "Refusing to write to credential/system path" in r.text

    async def test_edit_refused(self, tmp_path):
        r = await _tool("Edit").execute(
            file_path=str(tmp_path / ".env"), old_string="a", new_string="b"
        )
        assert not _ok(r)
        assert "Refusing to edit credential/system path" in r.text


# ── Read ───────────────────────────────────────────────────────────────


class TestRead:
    async def test_missing_and_directory(self, tmp_path):
        r = await _tool("Read").execute(file_path=str(tmp_path / "nope.txt"))
        assert not _ok(r)
        assert "File not found" in r.text
        r = await _tool("Read").execute(file_path=str(tmp_path))
        assert not _ok(r)
        assert "Path is a directory" in r.text

    async def test_line_numbers_and_range(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("\n".join(f"line{i}" for i in range(10)) + "\n")
        r = await _tool("Read").execute(file_path=str(f), offset=2, limit=3)
        assert _ok(r)
        assert r.text.splitlines() == [
            "   2 | line2",
            "   3 | line3",
            "   4 | line4",
        ]
        assert r.metadata["total_lines"] == 10
        assert r.metadata["shown_range"] == [2, 5]
        assert r.title == "a.txt (10 lines, showing 2-5)"

    async def test_negative_offset_clamped(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("a\nb\n")
        r = await _tool("Read").execute(file_path=str(f), offset=-5, limit=1)
        assert _ok(r)
        assert r.text == "   0 | a"

    async def test_offset_past_eof(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("a\n")
        r = await _tool("Read").execute(file_path=str(f), offset=99)
        assert _ok(r)
        assert r.text == ""

    async def test_truncation(self, tmp_path):
        f = tmp_path / "big.txt"
        f.write_text("\n".join("x" * 40 for _ in range(1200)))
        r = await _tool("Read").execute(file_path=str(f))
        assert _ok(r)
        assert "... [truncated," in r.text
        assert "more lines]" in r.text


# ── Write ──────────────────────────────────────────────────────────────


class TestWrite:
    async def test_create_and_update(self, tmp_path, fake_checkpoint):
        f = tmp_path / "sub" / "new.txt"  # parents auto-created
        r = await _tool("Write").execute(file_path=str(f), content="hello")
        assert _ok(r)
        assert "Created" in r.text
        assert r.metadata["created"] is True
        assert r.metadata["size"] == 5
        assert f.read_text() == "hello"
        assert fake_checkpoint[0][0] == str(f.resolve())
        assert fake_checkpoint[0][1] == ""  # no old content

        r = await _tool("Write").execute(file_path=str(f), content="hello2")
        assert "Updated" in r.text
        assert r.metadata["created"] is False
        assert fake_checkpoint[1][1] == "hello"  # old content snapshotted

    async def test_write_without_checkpoint_module(self, tmp_path, monkeypatch):
        # core.checkpoint absent -> the try/except swallows, write still works
        monkeypatch.delitem(sys.modules, "core.checkpoint", raising=False)
        monkeypatch.delitem(sys.modules, "core", raising=False)
        f = tmp_path / "x.txt"
        r = await _tool("Write").execute(file_path=str(f), content="z")
        assert _ok(r)


# ── Edit ───────────────────────────────────────────────────────────────


class TestEdit:
    async def test_single_replace(self, tmp_path, fake_checkpoint):
        f = tmp_path / "e.txt"
        f.write_text("foo bar foo")
        # ambiguous without replace_all
        r = await _tool("Edit").execute(file_path=str(f), old_string="foo", new_string="baz")
        assert not _ok(r)
        assert "found 2 times" in r.text

        r = await _tool("Edit").execute(file_path=str(f), old_string="foo bar", new_string="qux")
        assert _ok(r)
        assert f.read_text() == "qux foo"
        assert r.metadata["replacements"] == 1
        assert fake_checkpoint and fake_checkpoint[0][1] == "foo bar foo"

    async def test_replace_all(self, tmp_path):
        f = tmp_path / "e.txt"
        f.write_text("aXbXc")
        r = await _tool("Edit").execute(
            file_path=str(f), old_string="X", new_string="Y", replace_all=True
        )
        assert _ok(r)
        assert f.read_text() == "aYbYc"
        assert "2 occurrence(s)" in r.text

    async def test_empty_old_string_rejected(self, tmp_path):
        f = tmp_path / "e.txt"
        f.write_text("abc")
        r = await _tool("Edit").execute(
            file_path=str(f), old_string="", new_string="z", replace_all=True
        )
        assert not _ok(r)
        assert "must not be empty" in r.text
        assert f.read_text() == "abc"  # untouched

    async def test_not_found(self, tmp_path):
        f = tmp_path / "e.txt"
        f.write_text("abc")
        r = await _tool("Edit").execute(file_path=str(f), old_string="zzz", new_string="z")
        assert not _ok(r)
        assert "old_string not found" in r.text

    async def test_missing_file(self, tmp_path):
        r = await _tool("Edit").execute(
            file_path=str(tmp_path / "nope.txt"), old_string="a", new_string="b"
        )
        assert not _ok(r)
        assert "File not found" in r.text


# ── Glob ───────────────────────────────────────────────────────────────


class TestGlob:
    async def test_finds_and_filters_ignored_dirs(self, tmp_path):
        (tmp_path / "a.py").write_text("x")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.py").write_text("x")
        (tmp_path / ".venv").mkdir()
        (tmp_path / ".venv" / "c.py").write_text("x")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "d.py").write_text("x")
        r = await _tool("Glob").execute(pattern="**/*.py", path=str(tmp_path))
        assert _ok(r)
        assert "a.py" in r.text and "b.py" in r.text
        assert "c.py" not in r.text and "d.py" not in r.text
        assert r.metadata["count"] == 2
        assert r.title == "2 matches for '**/*.py'"

    async def test_caps_at_200(self, tmp_path):
        for i in range(210):
            (tmp_path / f"f{i:03d}.txt").write_text("x")
        r = await _tool("Glob").execute(pattern="*.txt", path=str(tmp_path))
        assert _ok(r)
        assert r.metadata["count"] == 210
        assert "... and 10 more matches" in r.text


# ── Grep ───────────────────────────────────────────────────────────────


class TestGrep:
    @pytest.fixture
    def tree(self, tmp_path):
        (tmp_path / "one.py").write_text("alpha\nbeta needle gamma\n")
        (tmp_path / "two.py").write_text("needle here\nplain\n")
        (tmp_path / "three.txt").write_text("needle in txt\n")
        (tmp_path / ".venv").mkdir()
        (tmp_path / ".venv" / "skip.py").write_text("needle skipped\n")
        return tmp_path

    async def test_content_mode(self, tree):
        r = await _tool("Grep").execute(pattern="needle", path=str(tree))
        assert _ok(r)
        assert "one.py" in r.text and "two.py" in r.text and "three.txt" in r.text
        assert "skip.py" not in r.text  # .venv filtered
        assert "2:" in r.text  # 1-based line number shown
        assert r.metadata == {
            "pattern": "needle",
            "match_count": 3,
            "files_count": 3,
        }
        assert r.title == "3 matches in 3 files"

    async def test_glob_filter(self, tree):
        r = await _tool("Grep").execute(pattern="needle", path=str(tree), glob="*.py")
        assert _ok(r)
        assert r.metadata["files_count"] == 2

    async def test_files_with_matches(self, tree):
        r = await _tool("Grep").execute(
            pattern="needle", path=str(tree), output_mode="files_with_matches"
        )
        assert _ok(r)
        assert "one.py" in r.text
        assert "2:" not in r.text  # no line numbers in this mode

    async def test_count_mode(self, tree):
        r = await _tool("Grep").execute(pattern="needle", path=str(tree), output_mode="count")
        assert _ok(r)
        assert "1 matches" in r.text

    async def test_no_matches(self, tree):
        r = await _tool("Grep").execute(pattern="zzz_nope", path=str(tree))
        assert _ok(r)
        assert r.text == "No matches for 'zzz_nope'"
        assert r.title == "0 matches"

    async def test_invalid_regex(self, tree):
        r = await _tool("Grep").execute(pattern="([", path=str(tree))
        assert not _ok(r)
        assert "Invalid regex" in r.text

    async def test_single_file_path(self, tree):
        r = await _tool("Grep").execute(pattern="needle", path=str(tree / "one.py"))
        assert _ok(r)
        assert r.metadata["files_count"] == 1

    async def test_per_file_line_cap(self, tmp_path):
        f = tmp_path / "many.py"
        f.write_text("\n".join(f"needle {i}" for i in range(30)))
        r = await _tool("Grep").execute(pattern="needle", path=str(tmp_path))
        assert _ok(r)
        assert "... and 10 more matches" in r.text
