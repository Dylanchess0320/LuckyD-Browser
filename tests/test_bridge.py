import io
import json
from unittest.mock import patch

import pytest

from bridge import READY_MESSAGE, main


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
