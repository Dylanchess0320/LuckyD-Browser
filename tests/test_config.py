import os
from unittest.mock import patch

from config import get_config


@patch("config.resolve_provider_config")
@patch.dict(os.environ, {}, clear=True)
def test_get_config_fallback_logic(mock_resolve):
    """Test get_config fallback logic with empty environment variables."""
    mock_resolve.return_value = {"provider": "mock"}

    config = get_config()

    # Assert provider config is present
    assert config["provider"] == "mock"

    # Assert fallback logic correctly parses default values
    assert config["max_turns"] == 30
    assert config["temperature"] == 0.0
    assert config["max_tokens"] == 8192
    assert config["timeout_sec"] == 120
    assert config["max_output_chars"] == 4000
    assert config["command_timeout_sec"] == 60


@patch("config.resolve_provider_config")
@patch.dict(
    os.environ,
    {
        "CODING_AGENT_MAX_TURNS": "50",
        "CODING_AGENT_TEMP": "0.5",
        "CODING_AGENT_MAX_TOKENS": "4096",
        "CODING_AGENT_TIMEOUT": "240",
        "CODING_AGENT_MAX_OUTPUT": "8000",
        "CODING_AGENT_CMD_TIMEOUT": "30",
    },
    clear=True,
)
def test_get_config_custom_values(mock_resolve):
    """Test get_config logic with custom environment variables."""
    mock_resolve.return_value = {"provider": "mock2"}

    config = get_config()

    # Assert provider config is present
    assert config["provider"] == "mock2"

    # Assert values correctly read from os.environ
    assert config["max_turns"] == 50
    assert config["temperature"] == 0.5
    assert config["max_tokens"] == 4096
    assert config["timeout_sec"] == 240
    assert config["max_output_chars"] == 8000
    assert config["command_timeout_sec"] == 30
