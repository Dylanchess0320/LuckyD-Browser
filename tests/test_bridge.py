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
