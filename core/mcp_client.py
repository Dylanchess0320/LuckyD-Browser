"""
MCP (Model Context Protocol) stdio client — connects to MCP servers via
subprocess, discovers tools, and dispatches calls.

Compatible with the standard MCP config format (claude-desktop / goose / cline):

    mcpServers:
      server_name:
        command: npx
        args: [-y, @modelcontextprotocol/server-fs, /path]
        env: {KEY: val}

Discovered tools are registered as: mcp__<server_name>__<tool_name>
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config import PROJECT_DIR

# ── Config Loading ────────────────────────────────────────────────────

MCP_CONFIG_VAR = "CODING_AGENT_MCP_CONFIG"

MCP_CONFIG_PATHS: list[Path] = [
    PROJECT_DIR / "mcp_config.json",
    PROJECT_DIR / ".luckyd-code" / "mcp.json",
    Path.home() / ".luckyd" / "mcp_config.json",
]


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    timeout: int = 60  # seconds


@dataclass
class MCPToolSchema:
    """Schema for a single tool exposed by an MCP server."""

    name: str
    description: str = ""
    input_schema: dict = field(default_factory=lambda: {"type": "object", "properties": {}})


def _find_config_path() -> str | None:
    """Return the first MCP config file that exists."""
    env_path = os.environ.get(MCP_CONFIG_VAR)
    if env_path and Path(env_path).exists():
        return env_path
    for p in MCP_CONFIG_PATHS:
        if p.exists():
            return str(p)
    return None


def load_mcp_config(path: str | None = None) -> dict[str, MCPServerConfig]:
    """Load MCP server config in the standard claude-desktop format.

    Returns dict of server_name -> MCPServerConfig.
    """
    path = path or _find_config_path()
    if not path:
        return {}

    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, Exception):
        return {}

    servers_data = data.get("mcpServers") or data.get("mcp_servers") or data.get("servers") or {}
    if not isinstance(servers_data, dict):
        return {}

    servers: dict[str, MCPServerConfig] = {}
    for name, cfg in servers_data.items():
        if not isinstance(cfg, dict) or not cfg.get("command"):
            continue
        env = cfg.get("env", {}) or {}
        if isinstance(env, dict):
            resolved_env = {}
            for k, v in env.items():
                if isinstance(v, str):
                    v = os.path.expandvars(v)
                resolved_env[k] = v
        else:
            resolved_env = {}

        servers[name] = MCPServerConfig(
            name=name,
            command=cfg["command"],
            args=cfg.get("args", []),
            env=resolved_env,
            enabled=cfg.get("enabled", True),
            timeout=cfg.get("timeout", 60),
        )

    return servers


# ── STDIO Transport ────────────────────────────────────────────────────


class MCPStdioTransport:
    """Connects to an MCP server via stdio subprocess (JSON-RPC 2.0)."""

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._req_id = 0
        self._closed = False
        self._server_info: dict | None = None

    async def start(self) -> None:
        """Launch the subprocess and initialize the MCP session."""
        if self._process:
            return

        cmd = self.config.command
        args = self.config.args[:]

        # On Windows, commands like npx need .cmd resolution via cmd /c
        if sys.platform == "win32" and not cmd.lower().endswith((".exe", ".cmd", ".bat")):
            resolved = shutil.which(cmd)
            if resolved and resolved.lower().endswith(".cmd"):
                cmd = "cmd"
                args = ["/c", resolved, *args]

        env = os.environ.copy()
        for k, v in self.config.env.items():
            env[k] = str(v)

        self._process = await asyncio.create_subprocess_exec(
            cmd,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        assert self._process.stdout is not None
        assert self._process.stdin is not None
        self._reader = self._process.stdout
        self._writer = self._process.stdin

        # Background stderr logger + reader loop
        self._stderr_task = asyncio.ensure_future(self._log_stderr())
        self._reader_task = asyncio.ensure_future(self._reader_loop())

        # MCP handshake
        self._server_info = await self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "clientInfo": {"name": "luckyd-code", "version": "2.2.0"},
                "capabilities": {},
            },
        )
        await self._send_notification("notifications/initialized")

    async def _log_stderr(self) -> None:
        """Log stderr output from the MCP server process."""
        try:
            assert self._process is not None and self._process.stderr is not None
            async for line in self._process.stderr:
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    print(f"  [MCP:{self.config.name} stderr] {text}", file=sys.stderr)
        except Exception:
            pass

    async def _reader_loop(self) -> None:
        """Read and dispatch JSON-RPC responses from the server."""
        try:
            assert self._reader is not None
            while not self._closed:
                line = await self._reader.readline()
                if not line:
                    break
                line = line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg_id = msg.get("id")
                if msg_id is not None:
                    key = str(msg_id)
                    future = self._pending.pop(key, None)
                    if future and not future.done():
                        if "error" in msg:
                            err = msg["error"]
                            future.set_exception(
                                RuntimeError(f"MCP error {err.get('code')}: {err.get('message')}")
                            )
                        else:
                            future.set_result(msg.get("result"))
        except Exception:
            pass
        finally:
            for _fid, future in self._pending.items():
                if not future.done():
                    future.cancel()
            self._pending.clear()

    async def _request(self, method: str, params: dict | None = None) -> Any:
        """Send a JSON-RPC request and wait for the response."""
        if self._closed:
            raise RuntimeError("MCP transport is closed")
        self._req_id += 1
        req_id = str(self._req_id)
        payload = {"jsonrpc": "2.0", "id": self._req_id, "method": method}
        if params is not None:
            payload["params"] = params
        future: asyncio.Future = asyncio.Future()
        self._pending[req_id] = future
        raw = json.dumps(payload, ensure_ascii=False) + "\n"
        assert self._writer is not None
        self._writer.write(raw.encode("utf-8"))
        await self._writer.drain()
        try:
            return await asyncio.wait_for(future, timeout=self.config.timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise TimeoutError(
                f"MCP request '{method}' to '{self.config.name}' timed out "
                f"after {self.config.timeout}s"
            ) from None

    async def _send_notification(self, method: str, params: dict | None = None) -> None:
        if self._closed:
            return
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        raw = json.dumps(payload, ensure_ascii=False) + "\n"
        assert self._writer is not None
        self._writer.write(raw.encode("utf-8"))
        await self._writer.drain()

    async def list_tools(self) -> list[MCPToolSchema]:
        """Call tools/list and return discovered tool schemas."""
        result = await self._request("tools/list")
        if not result or not isinstance(result, dict):
            return []
        schemas = []
        for t in result.get("tools", []):
            schemas.append(
                MCPToolSchema(
                    name=t.get("name", "unknown"),
                    description=t.get("description", ""),
                    input_schema=t.get(
                        "inputSchema", t.get("parameters", {"type": "object", "properties": {}})
                    ),
                )
            )
        return schemas

    async def call_tool(self, name: str, arguments: dict | None = None) -> Any:
        """Call a tool on the MCP server."""
        params: dict = {"name": name}
        if arguments:
            params["arguments"] = arguments
        return await self._request("tools/call", params)

    async def close(self) -> None:
        """Shutdown the subprocess."""
        self._closed = True
        if self._reader_task:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader_task
        if self._writer:
            with contextlib.suppress(Exception):
                self._writer.close()
        if self._process:
            try:
                self._process.kill()
                await self._process.wait()
            except Exception:
                pass
        self._process = None
        self._reader = None
        self._writer = None


# ── Manager ────────────────────────────────────────────────────────────


class MCPManager:
    """Manages multiple MCP server connections — lifecycle, discovery, status."""

    def __init__(self, config_path: str | None = None):
        self.config_path = config_path
        self._servers: dict[str, MCPServerConfig] = {}
        self._clients: dict[str, MCPStdioTransport] = {}
        self._status: dict[str, str] = {}

    async def connect_all(self) -> int:
        """Start enabled MCP servers and discover their tools.

        Returns the number of successfully connected servers.
        """
        self._servers = load_mcp_config(self.config_path)
        if not self._servers:
            return 0

        connected = 0
        for name, srv_cfg in self._servers.items():
            if not srv_cfg.enabled:
                self._status[name] = "disabled"
                continue
            client = MCPStdioTransport(srv_cfg)
            try:
                await client.start()
                self._clients[name] = client
                self._status[name] = "connected"
                connected += 1
            except Exception as e:
                self._status[name] = f"error: {e}"
                print(f"  [MCP] Failed to connect '{name}': {e}")

        return connected

    async def discover_tools(self) -> dict[str, list[MCPToolSchema]]:
        """Discover tools from all connected servers."""
        tools: dict[str, list[MCPToolSchema]] = {}
        for name, client in self._clients.items():
            try:
                tools[name] = await client.list_tools()
            except Exception as e:
                self._status[name] = f"error: {e}"
                print(f"  [MCP] Failed to list tools from '{name}': {e}")
        return tools

    def get_client(self, server_name: str) -> MCPStdioTransport | None:
        return self._clients.get(server_name)

    def status_report(self) -> str:
        lines = []
        for name, cfg in self._servers.items():
            status = self._status.get(name, "unknown")
            lines.append(f"  {name}: {status}  ({cfg.command} {' '.join(cfg.args)})")
        if not self._servers:
            lines.append("  (no MCP servers configured)")
        return "\n".join(lines)

    @property
    def is_connected(self) -> bool:
        return bool(self._clients)

    async def close_all(self) -> None:
        for _name, client in self._clients.items():
            with contextlib.suppress(Exception):
                await client.close()
        self._clients.clear()
        self._status.clear()
