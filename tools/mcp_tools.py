"""MCP tool adapter — wraps MCP remote tools into the local tool registry."""

from __future__ import annotations

import contextlib
import json
from typing import Any

from core.mcp_client import MCPManager, MCPToolSchema
from tools.base import ToolBase, ToolOutput
from tools.registry import registry


class MCPToolAdapter(ToolBase):
    """A tool that delegates execution to an MCP server."""

    def __init__(
        self,
        server_name: str,
        schema: MCPToolSchema,
        manager: MCPManager,
    ):
        self._server_name = server_name
        self._schema = schema
        self._manager = manager
        self.name = f"mcp__{server_name}__{schema.name}"
        self.description = f"[MCP: {server_name}] {schema.description or schema.name}"
        self.aliases = [f"mcp_{server_name}_{schema.name}"]
        self.permission_level = "NORMAL"

        # Convert MCP inputSchema to our JSON Schema format
        params = {}
        if schema.input_schema and isinstance(schema.input_schema, dict):
            for pname, pdef in schema.input_schema.get("properties", {}).items():
                if isinstance(pdef, dict):
                    entry = {
                        "type": pdef.get("type", "string"),
                        "description": pdef.get("description", ""),
                    }
                    if pname in (schema.input_schema.get("required", [])):
                        entry["required"] = True
                    params[pname] = entry
        self.parameters = params

    async def execute(self, **kwargs) -> ToolOutput:
        """Execute the tool on the remote MCP server."""
        client = self._manager.get_client(self._server_name)
        if not client:
            return ToolOutput(
                text=f"Error: MCP server '{self._server_name}' is not connected",
                error=True,
            )
        try:
            result = await client.call_tool(self._schema.name, kwargs)
            return self._format_result(result)
        except Exception as e:
            return ToolOutput(
                text=f"Error calling MCP tool '{self._schema.name}': {e}",
                error=True,
            )

    def _format_result(self, result: Any) -> ToolOutput:
        """Format MCP tool result (content blocks) into ToolOutput."""
        if result is None:
            return ToolOutput(text="(no result)")
        if isinstance(result, str):
            return ToolOutput(text=result)
        if isinstance(result, dict):
            # MCP content blocks: [{"type": "text", "text": "..."}]
            blocks = result.get("content", [])
            if isinstance(blocks, list):
                texts = []
                for block in blocks:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            texts.append(block.get("text", ""))
                        elif block.get("type") == "resource":
                            texts.append(json.dumps(block.get("resource", {}), indent=2))
                    elif isinstance(block, str):
                        texts.append(block)
                return ToolOutput(text="\n".join(texts))
            return ToolOutput(text=str(result))
        return ToolOutput(text=str(result))


class MCPListTool(ToolBase):
    """List all connected MCP servers and their tools."""

    name = "MCPList"
    description = "List all MCP servers, their status, and available remote tools."
    permission_level = "ALWAYS_ALLOW"
    parameters = {
        "server": {
            "type": "string",
            "description": "Optional server name filter (shows all if empty)",
            "required": False,
        }
    }

    def __init__(self, manager: MCPManager):
        self._manager = manager

    async def execute(self, server: str = "") -> ToolOutput:
        if server:
            client = self._manager.get_client(server)
            if not client:
                return ToolOutput(text=f"MCP server '{server}' not found", error=True)
            tools = await client.list_tools()
            lines = [f"MCP Server: {server}  ({len(tools)} tools)"]
            for t in tools:
                lines.append(f"  - mcp__{server}__{t.name}: {t.description}")
            return ToolOutput(text="\n".join(lines))
        else:
            status = self._manager.status_report()
            return ToolOutput(text=f"MCP Servers:\n{status}")


async def register_mcp_tools(manager: MCPManager) -> int:
    """Discover and register all MCP tools into the registry.

    Coroutine: the only caller (main.run_repl) already runs inside an event
    loop, so discovery is awaited directly. Driving a nested loop with
    run_until_complete() here raised "event loop is already running" and the
    failure was swallowed, silently registering zero tools.

    Returns the number of registered tools.
    """
    try:
        tools_by_server = await manager.discover_tools()
    except Exception:
        return 0

    count = 0
    for server_name, schemas in tools_by_server.items():
        for schema in schemas:
            adapter = MCPToolAdapter(server_name, schema, manager)
            try:
                registry.register(adapter)
                count += 1
            except Exception:
                pass

    # Also register the management tool
    list_tool = MCPListTool(manager)
    with contextlib.suppress(Exception):
        registry.register(list_tool)

    return count
