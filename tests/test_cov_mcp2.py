"""Coverage tests for tools/mcp_tools.py.

Covers MCPToolAdapter schema conversion, execution (no client / success /
transport error), _format_result content-block variants, MCPListTool
(single-server / missing / all-servers), and register_mcp_tools (discovery
failure, success, registration failure). Uses a hand-written fake MCP client
and a real MCPManager with injected clients — no network, no subprocesses.
"""

from __future__ import annotations

import pytest

from core.mcp_client import MCPManager, MCPToolSchema
from tools import mcp_tools as mt
from tools.mcp_tools import MCPListTool, MCPToolAdapter, register_mcp_tools
from tools.registry import registry


class _FakeClient:
    """Hand-written fake MCP transport client."""

    def __init__(self, tools=(), result=None, exc=None):
        self._tools = list(tools)
        self._result = result
        self._exc = exc
        self.calls: list[tuple] = []

    async def list_tools(self):
        return self._tools

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        if self._exc is not None:
            raise self._exc
        return self._result


def _manager_with(client=None) -> MCPManager:
    manager = MCPManager()
    if client is not None:
        manager._clients["srv"] = client
    return manager


def _schema(**kwargs) -> MCPToolSchema:
    return MCPToolSchema(name="read", description="read a file", **kwargs)


@pytest.fixture()
def clean_registry():
    before_tools = set(registry._tools)
    before_aliases = set(registry._aliases)
    yield
    for key in set(registry._tools) - before_tools:
        del registry._tools[key]
    for key in set(registry._aliases) - before_aliases:
        del registry._aliases[key]


# ── MCPToolAdapter.__init__ ───────────────────────────────────────────────


def test_adapter_converts_schema_parameters():
    schema = _schema(
        input_schema={
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string", "description": "file path"},
                "limit": {"type": "integer"},
                "junk": "not-a-dict",
            },
        }
    )
    adapter = MCPToolAdapter("srv", schema, _manager_with())
    assert adapter.name == "mcp__srv__read"
    assert adapter.description == "[MCP: srv] read a file"
    assert adapter.aliases == ["mcp_srv_read"]
    assert adapter.permission_level == "NORMAL"
    assert adapter.parameters["path"] == {
        "type": "string",
        "description": "file path",
        "required": True,
    }
    assert adapter.parameters["limit"] == {"type": "integer", "description": ""}
    assert "junk" not in adapter.parameters


def test_adapter_empty_and_nondict_input_schema():
    adapter = MCPToolAdapter("srv", _schema(input_schema={}), _manager_with())
    assert adapter.parameters == {}
    adapter = MCPToolAdapter("srv", _schema(input_schema="nope"), _manager_with())
    assert adapter.parameters == {}


def test_adapter_description_falls_back_to_name():
    adapter = MCPToolAdapter("srv", MCPToolSchema(name="ping"), _manager_with())
    assert adapter.description == "[MCP: srv] ping"


# ── MCPToolAdapter.execute ────────────────────────────────────────────────


async def test_adapter_execute_without_client():
    adapter = MCPToolAdapter("srv", _schema(), _manager_with())
    out = await adapter.execute(path="/x")
    assert out.error
    assert "not connected" in out.text


async def test_adapter_execute_delegates_to_client():
    client = _FakeClient(result={"content": [{"type": "text", "text": "file bytes"}]})
    adapter = MCPToolAdapter("srv", _schema(), _manager_with(client))
    out = await adapter.execute(path="/x", limit=3)
    assert not out.error
    assert out.text == "file bytes"
    assert client.calls == [("read", {"path": "/x", "limit": 3})]


async def test_adapter_execute_transport_error():
    client = _FakeClient(exc=RuntimeError("down"))
    adapter = MCPToolAdapter("srv", _schema(), _manager_with(client))
    out = await adapter.execute()
    assert out.error
    assert "Error calling MCP tool 'read': down" in out.text


# ── _format_result ────────────────────────────────────────────────────────


def test_format_result_variants():
    adapter = MCPToolAdapter("srv", _schema(), _manager_with())
    assert adapter._format_result(None).text == "(no result)"
    assert adapter._format_result("plain").text == "plain"
    assert adapter._format_result(42).text == "42"
    assert adapter._format_result(["a"]).text == "['a']"


def test_format_result_content_blocks():
    adapter = MCPToolAdapter("srv", _schema(), _manager_with())
    result = {
        "content": [
            {"type": "text", "text": "hello"},
            {"type": "resource", "resource": {"uri": "file:///x"}},
            "bare string",
            {"type": "image", "data": "zzz"},
            123,
        ]
    }
    out = adapter._format_result(result)
    assert "hello" in out.text
    assert "bare string" in out.text
    assert '"uri": "file:///x"' in out.text
    assert "123" not in out.text  # non-dict, non-str blocks are skipped


def test_format_result_content_not_a_list():
    adapter = MCPToolAdapter("srv", _schema(), _manager_with())
    result = {"content": "oops"}
    assert adapter._format_result(result).text == str(result)


def test_format_result_dict_without_content():
    adapter = MCPToolAdapter("srv", _schema(), _manager_with())
    assert adapter._format_result({"isError": False}).text == ""


# ── MCPListTool ───────────────────────────────────────────────────────────


async def test_list_tool_single_server():
    client = _FakeClient(tools=[_schema(), MCPToolSchema(name="write", description="w")])
    out = await MCPListTool(_manager_with(client)).execute(server="srv")
    assert not out.error
    assert "MCP Server: srv  (2 tools)" in out.text
    assert "  - mcp__srv__read: read a file" in out.text
    assert "  - mcp__srv__write: w" in out.text


async def test_list_tool_unknown_server():
    out = await MCPListTool(_manager_with()).execute(server="ghost")
    assert out.error
    assert "MCP server 'ghost' not found" in out.text


async def test_list_tool_all_servers_empty():
    out = await MCPListTool(_manager_with()).execute()
    assert not out.error
    assert out.text.startswith("MCP Servers:\n")
    assert "(no MCP servers configured)" in out.text


# ── register_mcp_tools ────────────────────────────────────────────────────


async def test_register_mcp_tools_discovery_failure_returns_zero(monkeypatch):
    manager = _manager_with()

    async def boom():
        raise RuntimeError("nope")

    monkeypatch.setattr(manager, "discover_tools", boom)
    assert await register_mcp_tools(manager) == 0


async def test_register_mcp_tools_registers_adapters_and_list_tool(clean_registry):
    client = _FakeClient(tools=[_schema()])
    manager = _manager_with(client)
    count = await register_mcp_tools(manager)
    assert count == 1  # only adapters are counted, not the MCPListTool
    adapter = registry.get("mcp__srv__read")
    assert isinstance(adapter, MCPToolAdapter)
    assert registry.get("mcp_srv_read") is adapter  # alias resolves
    assert isinstance(registry.get("mcplist"), MCPListTool)


async def test_register_mcp_tools_registration_failure_suppressed(clean_registry, monkeypatch):
    def boom(tool):
        raise RuntimeError("registry down")

    monkeypatch.setattr(mt.registry, "register", boom)
    client = _FakeClient(tools=[_schema()])
    manager = _manager_with(client)
    assert await register_mcp_tools(manager) == 0
