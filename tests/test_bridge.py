import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bridge import handle_request, main, run_agent, run_agent_stream


@pytest.mark.asyncio
@patch("bridge.get_config")
@patch("bridge.resolve_model")
@patch("bridge.CodingAgent")
async def test_run_agent_success(mock_agent_cls, mock_resolve_model, mock_get_config):
    mock_get_config.return_value = {"api_key": "fake_key", "base_url": "fake_url"}
    mock_resolve_model.return_value = "resolved_model"

    mock_agent = AsyncMock()
    mock_agent.run.return_value = "Agent response"
    mock_agent_cls.return_value = mock_agent

    result = await run_agent("Test prompt", model="auto", thinking=False)

    assert result["type"] == "response"
    assert result["content"] == "Agent response"
    assert result["model"] == "resolved_model"

    mock_get_config.assert_called_once()
    mock_resolve_model.assert_called_once_with(
        api_key="fake_key",
        base_url="fake_url",
        preferred="auto",
        thinking=False,
    )
    mock_agent_cls.assert_called_once_with(model="resolved_model")
    mock_agent.run.assert_awaited_once_with("Test prompt")


@pytest.mark.asyncio
@patch("bridge.get_config")
@patch("bridge.resolve_model")
@patch("bridge.CodingAgent")
async def test_run_agent_exception(mock_agent_cls, mock_resolve_model, mock_get_config):
    mock_get_config.return_value = {"api_key": "fake_key", "base_url": "fake_url"}
    mock_resolve_model.return_value = "resolved_model"

    mock_agent = AsyncMock()
    mock_agent.run.side_effect = Exception("Test exception")
    mock_agent_cls.return_value = mock_agent

    result = await run_agent("Test prompt")

    assert result["type"] == "error"
    assert result["content"] == "Test exception"
    assert "traceback" in result


@pytest.mark.asyncio
@patch("bridge.get_config")
@patch("bridge.resolve_model")
@patch("bridge.CodingAgent")
async def test_run_agent_stream_success(mock_agent_cls, mock_resolve_model, mock_get_config):
    mock_get_config.return_value = {"api_key": "fake_key", "base_url": "fake_url"}
    mock_resolve_model.return_value = "resolved_model"

    mock_agent = MagicMock()

    async def async_generator():
        yield "chunk1"
        yield "chunk2"

    mock_agent.stream.return_value = async_generator()
    mock_agent_cls.return_value = mock_agent

    chunks = []
    async for chunk in run_agent_stream("Test stream prompt", model="auto", thinking=False):
        chunks.append(json.loads(chunk))

    assert len(chunks) == 2
    assert chunks[0] == {"type": "chunk", "content": "chunk1"}
    assert chunks[1] == {"type": "chunk", "content": "chunk2"}

    mock_get_config.assert_called_once()
    mock_resolve_model.assert_called_once_with(
        api_key="fake_key",
        base_url="fake_url",
        preferred="auto",
        thinking=False,
    )
    mock_agent_cls.assert_called_once_with(model="resolved_model")
    mock_agent.stream.assert_called_once_with("Test stream prompt")


@pytest.mark.asyncio
@patch("bridge.get_config")
@patch("bridge.resolve_model")
@patch("bridge.CodingAgent")
async def test_run_agent_stream_exception(mock_agent_cls, mock_resolve_model, mock_get_config):
    mock_get_config.return_value = {"api_key": "fake_key", "base_url": "fake_url"}
    mock_resolve_model.return_value = "resolved_model"

    mock_agent = MagicMock()

    async def async_generator_with_exception():
        raise Exception("Stream exception")
        yield "never reached"

    mock_agent.stream.return_value = async_generator_with_exception()
    mock_agent_cls.return_value = mock_agent

    chunks = []
    async for chunk in run_agent_stream("Test stream prompt"):
        chunks.append(json.loads(chunk))

    assert len(chunks) == 1
    assert chunks[0]["type"] == "error"
    assert chunks[0]["content"] == "Stream exception"
    assert "traceback" in chunks[0]


@pytest.mark.asyncio
@patch("bridge.run_agent")
async def test_handle_request_chat(mock_run_agent):
    mock_run_agent.return_value = {"type": "response", "content": "hello"}
    request = {
        "method": "chat",
        "params": {"prompt": "hi", "model": "test_model", "thinking": True},
        "id": "1",
    }

    result = await handle_request(request)

    assert result == {"type": "response", "content": "hello", "id": "1"}
    mock_run_agent.assert_awaited_once_with("hi", model="test_model", thinking=True)


@pytest.mark.asyncio
@patch("bridge.run_agent_stream")
async def test_handle_request_chat_stream(mock_run_agent_stream, capsys):
    async def fake_stream(*args, **kwargs):
        yield json.dumps({"type": "chunk", "content": "hi"})
        yield json.dumps({"type": "chunk", "content": " there"})

    mock_run_agent_stream.return_value = fake_stream()
    request = {
        "method": "chat_stream",
        "params": {"prompt": "hi", "model": "test_model", "thinking": False},
        "id": "2",
    }

    result = await handle_request(request)

    assert result == {"type": "done", "id": "2"}

    # Check printed output
    captured = capsys.readouterr()
    lines = captured.out.strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"type": "chunk", "content": "hi", "id": "2"}
    assert json.loads(lines[1]) == {"type": "chunk", "content": " there", "id": "2"}


@pytest.mark.asyncio
@patch("bridge.get_memory")
@patch("bridge.get_config")
@patch("bridge.os.getcwd")
async def test_handle_request_get_context(mock_getcwd, mock_get_config, mock_get_memory):
    mock_getcwd.return_value = "/test/dir"
    mock_get_config.return_value = {"model": "test_model"}
    mock_memory = MagicMock()
    mock_memory.summarize.return_value = "Test memory summary"
    mock_get_memory.return_value = mock_memory

    request = {"method": "get_context", "id": "3"}

    result = await handle_request(request)

    assert result == {
        "type": "context",
        "id": "3",
        "content": {
            "model": "test_model",
            "cwd": "/test/dir",
            "memory_summary": "Test memory summary",
        },
    }


@pytest.mark.asyncio
async def test_handle_request_reset():
    request = {"method": "reset", "id": "4"}
    result = await handle_request(request)
    assert result == {"type": "ok", "id": "4", "content": "Agent reset"}


@pytest.mark.asyncio
async def test_handle_request_unknown_method():
    request = {"method": "unknown", "id": "5"}
    result = await handle_request(request)
    assert result == {"type": "error", "id": "5", "content": "Unknown method: unknown"}


@pytest.mark.asyncio
@patch("bridge.run_agent")
async def test_handle_request_exception(mock_run_agent):
    # Force an exception inside the try block
    mock_run_agent.side_effect = Exception("Internal chat error")
    request = {"method": "chat", "params": {}, "id": "error_id"}

    result = await handle_request(request)

    assert result["type"] == "error"
    assert result["id"] == "error_id"
    assert result["content"] == "Internal chat error"
    assert "traceback" in result


@pytest.mark.asyncio
@patch("bridge.sys.stdin")
@patch("bridge.handle_request")
async def test_main(mock_handle_request, mock_stdin, capsys):
    mock_stdin.__iter__.return_value = iter(
        [
            '{"method": "chat", "id": "1"}',
            "",  # empty line
            '{"method": "reset", "id": "2"}',
        ]
    )

    async def fake_handle_request(req):
        return {"type": "ok", "id": req["id"]}

    mock_handle_request.side_effect = fake_handle_request

    await main()

    captured = capsys.readouterr()
    lines = captured.out.strip().split("\n")

    assert len(lines) == 3
    assert json.loads(lines[0]) == {"type": "ready", "content": "LuckyD Code bridge ready"}
    assert json.loads(lines[1]) == {"type": "ok", "id": "1"}
    assert json.loads(lines[2]) == {"type": "ok", "id": "2"}


@pytest.mark.asyncio
@patch("bridge.sys.stdin")
async def test_main_json_error(mock_stdin, capsys):
    mock_stdin.__iter__.return_value = iter(["{invalid_json}"])

    await main()

    captured = capsys.readouterr()
    lines = captured.out.strip().split("\n")

    assert len(lines) == 2
    assert json.loads(lines[0]) == {"type": "ready", "content": "LuckyD Code bridge ready"}
    assert json.loads(lines[1])["type"] == "error"
    assert "Invalid JSON:" in json.loads(lines[1])["content"]


@pytest.mark.asyncio
@patch("bridge.sys.stdin")
@patch("bridge.handle_request")
async def test_main_done_type_ignored(mock_handle_request, mock_stdin, capsys):
    mock_stdin.__iter__.return_value = iter(['{"method": "chat_stream", "id": "1"}'])

    async def fake_handle_request(req):
        return {"type": "done", "id": req["id"]}

    mock_handle_request.side_effect = fake_handle_request

    await main()

    captured = capsys.readouterr()
    lines = captured.out.strip().split("\n")

    assert len(lines) == 1
    assert json.loads(lines[0]) == {"type": "ready", "content": "LuckyD Code bridge ready"}
