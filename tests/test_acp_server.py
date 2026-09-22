import pytest
from unittest.mock import MagicMock
from acp_server import AcpServer, AGENT_NAME, AGENT_VERSION, ACP_VERSION


def test_handle_invalid_request_not_dict():
    server = AcpServer()
    result = server.handle("not a dict")  # type: ignore
    assert result == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32600, "message": "invalid request: expected an object"},
    }


def test_handle_params_not_dict():
    server = AcpServer()
    server._initialized = True
    # Pass params as string, handle should convert to {}
    result = server.handle({"method": "ping", "params": "not a dict", "id": 1})  # type: ignore
    assert result == {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}


def test_handle_initialize():
    server = AcpServer()
    result = server.handle({"method": "initialize", "id": 1})
    assert server._initialized is True
    assert result == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "agent": {"name": AGENT_NAME, "version": AGENT_VERSION},
            "acpVersion": ACP_VERSION,
            "capabilities": {"prompt": True, "goal": True, "steer": True},
        },
    }


def test_handle_shutdown():
    server = AcpServer()
    server._initialized = True
    result = server.handle({"method": "shutdown", "id": 2})
    assert result == {"jsonrpc": "2.0", "id": 2, "result": {"ok": True}}


def test_handle_uninitialized():
    server = AcpServer()
    assert server._initialized is False
    # Attempting to call a non-ping, non-shutdown, non-initialize method without initialize
    result = server.handle({"method": "prompt", "id": 3})
    assert result == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {"code": -32002, "message": "server not initialized (send initialize first)"},
    }


def test_handle_ping_when_uninitialized():
    server = AcpServer()
    assert server._initialized is False
    result = server.handle({"method": "ping", "id": 4})
    assert result == {"jsonrpc": "2.0", "id": 4, "result": {"ok": True}}


def test_handle_prompt():
    server = AcpServer()
    server._initialized = True
    server._on_prompt = MagicMock(return_value="mock_prompt_result")  # type: ignore
    result = server.handle({"method": "prompt", "params": {"text": "hello"}, "id": 5})
    assert result == "mock_prompt_result"
    server._on_prompt.assert_called_once_with({"text": "hello"}, 5)


def test_handle_goal():
    server = AcpServer()
    server._initialized = True
    server._on_goal = MagicMock(return_value="mock_goal_result")  # type: ignore
    result = server.handle({"method": "goal", "params": {"action": "status"}, "id": 6})
    assert result == "mock_goal_result"
    server._on_goal.assert_called_once_with({"action": "status"}, 6)


def test_handle_steer():
    server = AcpServer()
    server._initialized = True
    server._on_steer = MagicMock(return_value="mock_steer_result")  # type: ignore
    result = server.handle({"method": "steer", "params": {"text": "go"}, "id": 7})
    assert result == "mock_steer_result"
    server._on_steer.assert_called_once_with({"text": "go"}, 7)


def test_handle_unknown_method():
    server = AcpServer()
    server._initialized = True
    result = server.handle({"method": "unknown_meth", "id": 8})
    assert result == {
        "jsonrpc": "2.0",
        "id": 8,
        "error": {"code": -32601, "message": "unknown method: 'unknown_meth'"},
    }
