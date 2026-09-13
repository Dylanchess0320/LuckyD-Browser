"""Night-5 tests: core/mcp_client.py (was ~0% covered).

Config loading (files, env var, malformed input, env expansion), the stdio
transport's JSON-RPC request/response dispatch, error/timeout paths, tool
discovery and tool calls — all with a faked subprocess (no real processes
are ever spawned) — plus the manager lifecycle with a stubbed transport.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque

import pytest

import core.mcp_client as mcp
from core.mcp_client import (
    MCPManager,
    MCPServerConfig,
    MCPStdioTransport,
    MCPToolSchema,
    _find_config_path,
    load_mcp_config,
)

# ── config loading ─────────────────────────────────────────────────────


def _write_config(tmp_path, payload) -> str:
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return str(p)


def test_load_mcp_config_basic(tmp_path):
    path = _write_config(
        tmp_path,
        {"mcpServers": {"fs": {"command": "npx", "args": ["-y", "srv"], "env": {"A": "b"}}}},
    )
    servers = load_mcp_config(path)
    assert set(servers) == {"fs"}
    cfg = servers["fs"]
    assert cfg.command == "npx"
    assert cfg.args == ["-y", "srv"]
    assert cfg.env == {"A": "b"}
    assert cfg.enabled is True
    assert cfg.timeout == 60


def test_load_mcp_config_skips_missing_command(tmp_path):
    path = _write_config(tmp_path, {"mcpServers": {"bad": {"args": []}, "ok": {"command": "x"}}})
    servers = load_mcp_config(path)
    assert set(servers) == {"ok"}


def test_load_mcp_config_malformed_and_missing(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_mcp_config(str(bad)) == {}
    assert load_mcp_config(str(tmp_path / "nope.json")) == {}
    empty = _write_config(tmp_path, {"mcpServers": "not-a-dict"})
    assert load_mcp_config(empty) == {}


def test_load_mcp_config_env_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("MYDIR", "/data/dir")
    path = _write_config(
        tmp_path,
        {"mcpServers": {"s": {"command": "x", "env": {"P": "$MYDIR/sub", "N": 5}}}},
    )
    servers = load_mcp_config(path)
    assert servers["s"].env["P"] == "/data/dir/sub"
    assert servers["s"].env["N"] == 5  # non-strings pass through


def test_load_mcp_config_alt_keys_and_flags(tmp_path):
    path = _write_config(
        tmp_path,
        {"servers": {"s": {"command": "x", "enabled": False, "timeout": 10}}},
    )
    servers = load_mcp_config(path)
    assert servers["s"].enabled is False
    assert servers["s"].timeout == 10


def test_find_config_path_env_precedence(tmp_path, monkeypatch):
    p = tmp_path / "custom.json"
    p.write_text("{}", encoding="utf-8")
    monkeypatch.setenv(mcp.MCP_CONFIG_VAR, str(p))
    assert _find_config_path() == str(p)
    # env var pointing at a missing file -> falls through to the search paths
    monkeypatch.setenv(mcp.MCP_CONFIG_VAR, str(tmp_path / "missing.json"))
    real = tmp_path / "real.json"
    real.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mcp, "MCP_CONFIG_PATHS", [tmp_path / "nope.json", real])
    assert _find_config_path() == str(real)
    # nothing exists anywhere -> None (deterministic, no real machine paths)
    monkeypatch.setattr(mcp, "MCP_CONFIG_PATHS", [tmp_path / "nope.json"])
    assert _find_config_path() is None


def test_load_mcp_config_via_env_var(tmp_path, monkeypatch):
    path = _write_config(tmp_path, {"mcpServers": {"s": {"command": "x"}}})
    monkeypatch.setenv(mcp.MCP_CONFIG_VAR, path)
    assert set(load_mcp_config(None)) == {"s"}


# ── fake subprocess ────────────────────────────────────────────────────


class FakeReader:
    """Blocks like a real subprocess stdout until lines are fed or EOF."""

    def __init__(self, lines: tuple[bytes, ...] = ()):
        self._lines: deque[bytes] = deque(lines)
        self._event = asyncio.Event()
        self._eof = False

    def feed(self, line: bytes):
        self._lines.append(line)
        self._event.set()

    def eof(self):
        self._eof = True
        self._event.set()

    async def readline(self) -> bytes:
        while not self._lines:
            if self._eof:
                return b""
            self._event.clear()
            await self._event.wait()
        return self._lines.popleft()


class FakeWriter:
    def __init__(self, on_write=None):
        self.written: list[bytes] = []
        self.closed = False
        self._on_write = on_write

    def write(self, data: bytes):
        self.written.append(data)
        if self._on_write is not None:
            self._on_write(data)

    async def drain(self):
        pass

    def close(self):
        self.closed = True


class FakeStderr:
    def __aiter__(self):
        async def gen():
            return
            yield  # pragma: no cover

        return gen()


class FakeProcess:
    """Scripted MCP server: feeds canned responses when a request is written."""

    def __init__(self, script: dict[str, list[bytes]]):
        self.stdout = FakeReader()
        self._script = {m: deque(rs) for m, rs in script.items()}
        self.stdin = FakeWriter(self._on_write)
        self.stderr = FakeStderr()
        self.killed = False

    def _on_write(self, data: bytes):
        try:
            payload = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        method = payload.get("method")
        queue = self._script.get(method)
        # Feed every queued line for this method: a real server's reply may
        # arrive as several physical lines (garbage, blanks, then JSON).
        while queue:
            self.stdout.feed(queue.popleft())

    def kill(self):
        self.killed = True

    async def wait(self):
        return 0


def _rpc_result(req_id: int, result) -> bytes:
    return (json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}) + "\n").encode()


def _rpc_error(req_id: int, code: int, message: str) -> bytes:
    return (
        json.dumps({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})
        + "\n"
    ).encode()


@pytest.fixture()
def fake_spawn(monkeypatch):
    procs: list[FakeProcess] = []

    async def _spawn(cmd, *args, **kwargs):
        proc = FakeProcess.spawn_next.pop(0)
        procs.append(proc)
        return proc

    _spawn.procs = procs
    FakeProcess.spawn_next: list[FakeProcess] = []
    monkeypatch.setattr(mcp.asyncio, "create_subprocess_exec", _spawn)
    return _spawn


def _transport(script: dict[str, list[bytes]], timeout=60, name="test-srv") -> MCPStdioTransport:
    FakeProcess.spawn_next.append(FakeProcess(script))
    return MCPStdioTransport(MCPServerConfig(name=name, command="fake", timeout=timeout))


def _init_script(
    extra: dict[str, list[bytes]] | None = None, server_info=None
) -> dict[str, list[bytes]]:
    script = {"initialize": [_rpc_result(1, server_info if server_info is not None else {})]}
    if extra:
        script.update(extra)
    return script


def _sent_payloads(proc: FakeProcess) -> list[dict]:
    return [json.loads(w.decode()) for w in proc.stdin.written]


# ── transport lifecycle ────────────────────────────────────────────────


async def test_transport_start_handshake(fake_spawn):
    t = _transport(_init_script(server_info={"serverInfo": {"name": "demo"}}))
    await t.start()
    proc = fake_spawn.procs[0]
    payloads = _sent_payloads(proc)
    assert payloads[0]["method"] == "initialize"
    assert payloads[0]["id"] == 1
    assert payloads[0]["params"]["protocolVersion"] == "2024-11-05"
    assert payloads[1]["method"] == "notifications/initialized"
    assert "id" not in payloads[1]  # notification has no id
    assert t._server_info == {"serverInfo": {"name": "demo"}}
    await t.close()
    assert proc.killed is True


async def test_transport_start_idempotent(fake_spawn):
    t = _transport(_init_script())
    await t.start()
    await t.start()  # second call is a no-op
    assert len(fake_spawn.procs) == 1
    await t.close()


async def test_transport_list_tools(fake_spawn):
    t = _transport(
        _init_script(
            {
                "tools/list": [
                    _rpc_result(
                        2,
                        {
                            "tools": [
                                {
                                    "name": "read_file",
                                    "description": "read it",
                                    "inputSchema": {
                                        "type": "object",
                                        "properties": {"p": {}},
                                    },
                                },
                                {"name": "legacy", "parameters": {"type": "object"}},
                            ]
                        },
                    )
                ]
            }
        )
    )
    await t.start()
    tools = await t.list_tools()
    assert [s.name for s in tools] == ["read_file", "legacy"]
    assert tools[0].description == "read it"
    assert tools[0].input_schema["properties"] == {"p": {}}
    assert tools[1].input_schema == {"type": "object"}  # parameters fallback
    await t.close()


async def test_transport_list_tools_empty_result(fake_spawn):
    t = _transport(_init_script({"tools/list": [_rpc_result(2, None)]}))
    await t.start()
    assert await t.list_tools() == []
    await t.close()


async def test_transport_call_tool(fake_spawn):
    t = _transport(
        _init_script({"tools/call": [_rpc_result(2, {"content": [{"text": "file data"}]})]})
    )
    await t.start()
    result = await t.call_tool("read_file", {"path": "a.txt"})
    assert result == {"content": [{"text": "file data"}]}
    payloads = _sent_payloads(fake_spawn.procs[0])
    call = payloads[2]
    assert call["method"] == "tools/call"
    assert call["params"] == {"name": "read_file", "arguments": {"path": "a.txt"}}
    await t.close()


async def test_transport_call_tool_no_arguments(fake_spawn):
    t = _transport(_init_script({"tools/call": [_rpc_result(2, "ok")]}))
    await t.start()
    await t.call_tool("ping")
    payloads = _sent_payloads(fake_spawn.procs[0])
    assert payloads[2]["params"] == {"name": "ping"}
    await t.close()


async def test_transport_jsonrpc_error_raises(fake_spawn):
    t = _transport(_init_script({"tools/call": [_rpc_error(2, -32602, "bad params")]}))
    await t.start()
    with pytest.raises(RuntimeError, match="MCP error -32602: bad params"):
        await t.call_tool("nope")
    await t.close()


async def test_transport_request_timeout(fake_spawn):
    t = MCPStdioTransport(MCPServerConfig(name="t", command="fake", timeout=1))
    proc = FakeProcess({})  # never feeds any response lines
    t._process = proc
    t._reader = proc.stdout
    t._writer = proc.stdin
    t._reader_task = asyncio.ensure_future(t._reader_loop())
    with pytest.raises(TimeoutError, match="timed out"):
        await t._request("tools/list")
    await t.close()


async def test_transport_request_after_close_raises(fake_spawn):
    t = _transport(_init_script())
    await t.start()
    await t.close()
    with pytest.raises(RuntimeError, match="closed"):
        await t._request("tools/list")
    # notifications after close are silent no-ops
    await t._send_notification("x")
    assert len(_sent_payloads(fake_spawn.procs[0])) == 2


async def test_transport_reader_ignores_garbage_lines(fake_spawn):
    t = _transport(
        _init_script({"tools/list": [b"this is not json\n", b"\n", _rpc_result(2, {"tools": []})]})
    )
    await t.start()
    assert await t.list_tools() == []
    await t.close()


def test_tool_schema_defaults():
    s = MCPToolSchema(name="x")
    assert s.description == ""
    assert s.input_schema == {"type": "object", "properties": {}}


# ── manager ────────────────────────────────────────────────────────────


class FakeTransport:
    instances: list[FakeTransport] = []

    def __init__(self, cfg: MCPServerConfig):
        self.cfg = cfg
        self.closed = False
        self.tools = [MCPToolSchema(name=f"{cfg.name}-tool")]
        FakeTransport.instances.append(self)

    async def start(self):
        if self.cfg.name == "broken":
            raise OSError("spawn failed")

    async def list_tools(self):
        return self.tools

    async def close(self):
        self.closed = True


@pytest.fixture()
def stub_transport(monkeypatch):
    FakeTransport.instances.clear()
    monkeypatch.setattr(mcp, "MCPStdioTransport", FakeTransport)
    return FakeTransport


async def test_manager_connect_all(tmp_path, stub_transport):
    path = _write_config(
        tmp_path,
        {
            "mcpServers": {
                "good": {"command": "x"},
                "off": {"command": "y", "enabled": False},
                "broken": {"command": "z"},
            }
        },
    )
    mgr = MCPManager(config_path=path)
    assert await mgr.connect_all() == 1
    assert mgr.is_connected is True
    assert mgr.get_client("good") is not None
    assert mgr.get_client("off") is None
    report = mgr.status_report()
    assert "good: connected" in report
    assert "off: disabled" in report
    assert "broken: error:" in report


async def test_manager_no_servers(tmp_path):
    path = _write_config(tmp_path, {"mcpServers": {}})
    mgr = MCPManager(config_path=path)
    assert await mgr.connect_all() == 0
    assert mgr.is_connected is False
    assert "(no MCP servers configured)" in mgr.status_report()
    assert await mgr.discover_tools() == {}
    await mgr.close_all()  # no-op, must not raise


async def test_manager_discover_and_close(tmp_path, stub_transport):
    path = _write_config(tmp_path, {"mcpServers": {"good": {"command": "x"}}})
    mgr = MCPManager(config_path=path)
    await mgr.connect_all()
    tools = await mgr.discover_tools()
    assert set(tools) == {"good"}
    assert tools["good"][0].name == "good-tool"
    await mgr.close_all()
    assert mgr.is_connected is False
    assert FakeTransport.instances[0].closed is True
