import io
import json
from unittest.mock import patch

import pytest

from bridge import READY_MESSAGE, handle_request, main


@pytest.mark.asyncio
async def test_main_invalid_json(capsys):
    invalid_json_input = "not valid json\n"

    with patch("sys.stdin", io.StringIO(invalid_json_input)):
        await main()

    captured = capsys.readouterr()
    stdout = captured.out.strip().split("\n")

    assert len(stdout) == 2

    ready_msg = json.loads(stdout[0])
    assert ready_msg["type"] == "ready"
    assert ready_msg["content"] == READY_MESSAGE

    error_msg = json.loads(stdout[1])
    assert error_msg["type"] == "error"
    assert "Invalid JSON" in error_msg["content"]


@pytest.mark.asyncio
async def test_handle_request_unsupported_method():
    req = {"method": "unsupported_method_xyz", "id": "123"}
    res = await handle_request(req)
    assert res.get("type") == "error"
    assert "Unknown method: unsupported_method_xyz" in res.get("content", "")
    assert res.get("id") == "123"


@pytest.mark.asyncio
async def test_handle_request_exception(monkeypatch):
    import bridge

    async def mock_run_agent(*args, **kwargs):
        raise ValueError("Simulated ValueError")

    monkeypatch.setattr(bridge, "run_agent", mock_run_agent)
    req = {"method": "chat", "id": "456", "params": {"prompt": "test"}}
    res = await handle_request(req)
    assert res.get("type") == "error"
    assert "Simulated ValueError" in res.get("content", "")
    assert "traceback" in res
    assert res.get("id") == "456"


@pytest.mark.asyncio
async def test_handle_request_invalid_params_type():
    # params is not a dict, so params.get() raises inside the try block
    # and is converted to an error response with traceback.
    req = {"method": "chat", "id": "789", "params": 42}
    res = await handle_request(req)
    assert res.get("type") == "error"
    assert "traceback" in res
    assert res.get("id") == "789"


@pytest.mark.asyncio
async def test_run_agent_stream_success(monkeypatch):
    import bridge

    monkeypatch.setattr(bridge, "get_config", lambda: {"api_key": "test", "base_url": None})
    monkeypatch.setattr(bridge, "resolve_model", lambda **kwargs: "mock-model")

    class MockAgent:
        def __init__(self, model):
            self.model = model

        async def stream(self, prompt):
            yield "chunk1"
            yield "chunk2"

    monkeypatch.setattr(bridge, "CodingAgent", MockAgent)

    chunks = []
    async for chunk in bridge.run_agent_stream("test prompt"):
        chunks.append(json.loads(chunk))

    assert len(chunks) == 2
    assert chunks[0] == {"type": "chunk", "content": "chunk1"}
    assert chunks[1] == {"type": "chunk", "content": "chunk2"}


@pytest.mark.asyncio
async def test_run_agent_stream_error(monkeypatch):
    import bridge

    monkeypatch.setattr(bridge, "get_config", lambda: {"api_key": "test", "base_url": None})
    monkeypatch.setattr(bridge, "resolve_model", lambda **kwargs: "mock-model")

    class MockAgent:
        def __init__(self, model):
            self.model = model

        async def stream(self, prompt):
            yield "chunk1"
            raise ValueError("Test error")

    monkeypatch.setattr(bridge, "CodingAgent", MockAgent)

    chunks = []
    async for chunk in bridge.run_agent_stream("test prompt"):
        chunks.append(json.loads(chunk))

    assert len(chunks) == 2
    assert chunks[0] == {"type": "chunk", "content": "chunk1"}
    assert chunks[1]["type"] == "error"
    assert chunks[1]["content"] == "Test error"
    assert "traceback" in chunks[1]
