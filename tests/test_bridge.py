import pytest
from bridge import handle_request


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
    # Pass a valid request, but params is not a dict. This will crash at:
    # prompt = params.get("prompt", "")
    # because `params` is an int. This triggers the exception block inside try.
    req = {"method": "chat", "id": "789", "params": 42}
    res = await handle_request(req)
    assert res.get("type") == "error"
    assert "object has no attribute 'get'" in res.get("content", "")
    assert "traceback" in res
    assert res.get("id") == "789"
