"""Tests for the tools/ package."""

from __future__ import annotations

import json

import pytest


def _is_err(result) -> bool:
    return bool(getattr(result, "error", False))


@pytest.fixture
def tmp_project(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hello')\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Test Project\n", encoding="utf-8")
    (tmp_path / "data.json").write_text(json.dumps({"key": "value"}), encoding="utf-8")
    return tmp_path


class TestToolRegistry:
    def test_tools_registered(self):
        import tools.bash_tool
        import tools.file_tools
        import tools.utility_tools
        from tools.registry import registry

        tools = registry.list_tools()
        assert "read" in tools
        assert "write" in tools
        assert "bash" in tools

    def test_get_tool(self):
        from tools.registry import registry

        tool = registry.get("read")
        assert tool is not None
        assert tool.name.lower() == "read"

    def test_openai_schema_format(self):
        from tools.registry import registry

        schemas = registry.openai_tools()
        assert len(schemas) > 0
        for s in schemas:
            assert s["type"] == "function"
            assert "name" in s["function"]


class TestReadTool:
    @pytest.mark.asyncio
    async def test_read_file(self, tmp_project):
        from tools.registry import registry

        tool = registry.get("read")
        result = await tool.execute(file_path=str(tmp_project / "src" / "main.py"))
        assert "hello" in result.text
        assert not _is_err(result)

    @pytest.mark.asyncio
    async def test_read_missing(self, tmp_project):
        from tools.registry import registry

        tool = registry.get("read")
        result = await tool.execute(file_path=str(tmp_project / "nonexistent.py"))
        assert _is_err(result)


class TestWriteTool:
    @pytest.mark.asyncio
    async def test_write_file(self, tmp_project):
        from tools.registry import registry

        tool = registry.get("write")
        target = tmp_project / "new_file.py"
        result = await tool.execute(file_path=str(target), content="x = 42\n")
        assert not _is_err(result)
        assert target.read_text(encoding="utf-8") == "x = 42\n"


class TestGlobTool:
    @pytest.mark.asyncio
    async def test_glob_py(self, tmp_project):
        from tools.registry import registry

        tool = registry.get("glob")
        result = await tool.execute(pattern="*.py", path=str(tmp_project))
        assert not _is_err(result)


class TestBashTool:
    @pytest.mark.asyncio
    async def test_simple_command(self):
        from tools.registry import registry

        tool = registry.get("bash")
        result = await tool.execute(command="echo hello")
        assert not _is_err(result)
        assert "hello" in result.text


class TestUtilityTools:
    @pytest.mark.asyncio
    async def test_read_exists(self, tmp_project):
        from tools.registry import registry

        tool = registry.get("read")
        result = await tool.execute(file_path=str(tmp_project / "README.md"))
        assert not _is_err(result)
