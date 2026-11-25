from unittest.mock import MagicMock

import pytest

import core.trust as trust_mod
from acp_server import ACP_VERSION, AGENT_NAME, AGENT_VERSION, AcpServer, _acp_approval
from core.types import HookContext, ToolApprovalRequest


@pytest.fixture()
def trust_tmp(monkeypatch, tmp_path):
    """Isolate TrustPolicy/audit state from the developer's real profile."""
    monkeypatch.setenv("LUCKYD_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(trust_mod, "_policy", None)
    monkeypatch.setattr(trust_mod, "_audit_log", None)
    yield tmp_path
    monkeypatch.setattr(trust_mod, "_policy", None)
    monkeypatch.setattr(trust_mod, "_audit_log", None)


def _approval_request(tool_name="Bash"):
    return ToolApprovalRequest(
        tool_name=tool_name,
        tool_args={"command": "echo hi"},
        call_id="call-1",
        session_id="acp",
        turn=0,
    )


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


def test_serve_forever_eof():
    import io

    # Test exiting on EOF
    server = AcpServer(stdin=io.StringIO(""), stdout=io.StringIO())
    assert server.serve_forever() == 0


def test_serve_forever_shutdown():
    import io
    import json

    # Test exiting on shutdown method
    req = json.dumps({"method": "shutdown", "id": 1})
    server = AcpServer(stdin=io.StringIO(f"{req}\n"), stdout=io.StringIO())
    server._initialized = True
    assert server.serve_forever() == 0


def test_serve_forever_empty_lines_and_malformed_json():
    import io
    import json

    # Test skipping empty lines and handling malformed JSON
    # followed by a valid shutdown to exit
    req = "\n  \ninvalid json\n" + json.dumps({"method": "shutdown", "id": 1}) + "\n"
    stdout = io.StringIO()
    server = AcpServer(stdin=io.StringIO(req), stdout=stdout)
    server._initialized = True

    assert server.serve_forever() == 0
    output = stdout.getvalue()
    # Expect a -32700 error for the malformed JSON
    assert '"code": -32700' in output


def test_serve_forever_crashed_handle():
    import io
    import json
    from unittest.mock import MagicMock

    req = json.dumps({"method": "ping", "id": 1}) + "\n"
    stdout = io.StringIO()
    server = AcpServer(stdin=io.StringIO(req), stdout=stdout)
    server._initialized = True
    server.handle = MagicMock(side_effect=Exception("Test crash"))  # type: ignore

    assert server.serve_forever() == 0
    output = stdout.getvalue()
    assert '"code": -32603' in output
    assert "internal error" in output


def test_serve_forever_write_failed():
    import io
    import json
    from unittest.mock import MagicMock

    req = json.dumps({"method": "ping", "id": 1}) + "\n"
    stdout = io.StringIO()
    server = AcpServer(stdin=io.StringIO(req), stdout=stdout)
    server._initialized = True
    server._emit = MagicMock(side_effect=Exception("Test write failure"))  # type: ignore

    assert server.serve_forever() == 1


# ── approval wiring (10.9.3) ─────────────────────────────────────────────


def test_acp_approval_denies_by_default(monkeypatch, trust_tmp):
    monkeypatch.delenv("LUCKYD_ACP_AUTO_APPROVE", raising=False)
    result = _acp_approval(_approval_request())
    assert result.approved is False
    assert "LUCKYD_ACP_AUTO_APPROVE" in result.reason
    assert "shell" in result.reason


def test_acp_approval_env_opt_in(monkeypatch, trust_tmp):
    monkeypatch.setenv("LUCKYD_ACP_AUTO_APPROVE", "1")
    result = _acp_approval(_approval_request())
    assert result.approved is True
    assert "LUCKYD_ACP_AUTO_APPROVE" in result.reason


def test_get_agent_registers_one_approval_hook(monkeypatch, trust_tmp):
    from core.approval_hook import ApprovalHook
    from core.hooks import get_hooks, reset_hooks

    class FakeAgent:
        conversation_id = "conv_acp_test"

    reset_hooks()
    try:
        # `import agent` must resolve to the repo-root shim (agent.py), not
        # browser/browser_core/agent.py. Other tests put the browser dir on
        # sys.path, so pin the repo root first and evict any cached
        # wrong-module entry; monkeypatch restores both after the test.
        import sys
        from pathlib import Path

        repo_root = str(Path(__file__).resolve().parent.parent)
        monkeypatch.syspath_prepend(repo_root)
        monkeypatch.delitem(sys.modules, "agent", raising=False)
        import agent as agent_mod

        monkeypatch.setattr(agent_mod, "CodingAgent", FakeAgent)
        server = AcpServer()
        assert server._get_agent() is not None
        assert server._get_agent() is not None  # cached, no duplicate hook

        hooks = [getattr(hook, "__self__", hook) for hook in get_hooks().before_tool]
        acp_hooks = [
            hook
            for hook in hooks
            if isinstance(hook, ApprovalHook) and hook.session_id == "conv_acp_test"
        ]
        assert len(acp_hooks) == 1
        assert acp_hooks[0].approval_callback is _acp_approval
    finally:
        reset_hooks()


def test_acp_hook_denies_dangerous_tool(monkeypatch, trust_tmp):
    from core.approval_hook import ApprovalHook

    monkeypatch.delenv("LUCKYD_ACP_AUTO_APPROVE", raising=False)
    hook = ApprovalHook(approval_callback=_acp_approval, session_id="acp")
    result = hook.before_tool(
        "Bash", {"command": "echo hi", "_id": "call-9"}, HookContext(turn=0, messages=[])
    )
    assert result is not None
    assert result["tool_call_id"] == "call-9"
    assert "denied" in result["content"].lower()
    assert "LUCKYD_ACP_AUTO_APPROVE" in result["content"]
