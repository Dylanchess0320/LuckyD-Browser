import pytest
import sys
import io
import json
from unittest.mock import AsyncMock, patch

from main import run_one_shot_json
from agent import CodingAgent

@pytest.mark.asyncio
async def test_run_one_shot_json_exception_handling():
    agent = AsyncMock(spec=CodingAgent)
    agent.run.side_effect = Exception("Test Exception")

    # Capture stdout
    stdout_capture = io.StringIO()

    with patch('sys.stdout', stdout_capture):
        with patch('main.run_exclusive'): # Mocking run_exclusive
            await run_one_shot_json(agent, "test message")

    output = stdout_capture.getvalue()
    lines = [line for line in output.split('\n') if line]

    # Assert there is at least one line (the error line)
    assert len(lines) >= 1

    # Assert the last line is the error JSON
    error_json = json.loads(lines[-1])
    assert error_json["type"] == "error"
    assert "Test Exception" in error_json["text"]
