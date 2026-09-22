import os
import sys
from pathlib import Path

import pytest

import config

def test_project_dir_normal(monkeypatch):
    """Test _project_dir in normal execution."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    expected = Path(config.__file__).parent.resolve()
    assert config._project_dir() == expected

def test_project_dir_frozen(monkeypatch, tmp_path):
    """Test _project_dir in PyInstaller frozen execution."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    # Mock sys.executable to point inside tmp_path
    mock_exe = tmp_path / "app" / "executable.exe"
    mock_exe.parent.mkdir()
    monkeypatch.setattr(sys, "executable", str(mock_exe))

    assert config._project_dir() == mock_exe.resolve().parent

def test_load_env_basic(monkeypatch, tmp_path):
    """Test basic environment variable loading."""
    env_file = tmp_path / ".env"
    env_file.write_text("TEST_KEY=test_value\nANOTHER_KEY=123", encoding="utf-8")

    monkeypatch.setattr(config, "BASE_ENV_FILE", env_file)
    monkeypatch.setattr(config, "ENV_FILE", env_file)

    config.load_env()

    assert os.environ.get("TEST_KEY") == "test_value"
    assert os.environ.get("ANOTHER_KEY") == "123"

def test_load_env_bom_handling(monkeypatch, tmp_path):
    """Test loading .env file with UTF-8 BOM."""
    env_file = tmp_path / ".env"
    # Write with utf-8-sig to include BOM
    env_file.write_text("BOM_KEY=bom_value", encoding="utf-8-sig")

    monkeypatch.setattr(config, "BASE_ENV_FILE", env_file)
    monkeypatch.setattr(config, "ENV_FILE", env_file)

    config.load_env()

    assert os.environ.get("BOM_KEY") == "bom_value"
    # Ensure BOM isn't part of the key
    assert "\ufeff" not in os.environ.get("BOM_KEY", "")

def test_load_env_edge_cases(monkeypatch, tmp_path):
    """Test edge cases like comments, empty lines, missing =, quotes."""
    env_file = tmp_path / ".env"
    content = """
# This is a comment
EMPTY_KEY=
  # Indented comment

NO_EQUALS_LINE
QUOTED_KEY1="quoted_value1"
QUOTED_KEY2='quoted_value2'
 SPACES_KEY = spaced_value
"""
    env_file.write_text(content, encoding="utf-8")

    monkeypatch.setattr(config, "BASE_ENV_FILE", env_file)
    monkeypatch.setattr(config, "ENV_FILE", env_file)

    config.load_env()

    assert os.environ.get("EMPTY_KEY") == ""
    assert os.environ.get("QUOTED_KEY1") == "quoted_value1"
    assert os.environ.get("QUOTED_KEY2") == "quoted_value2"
    assert os.environ.get("SPACES_KEY") == "spaced_value"

def test_load_env_overlay(monkeypatch, tmp_path):
    """Test agent-specific overlay loading."""
    base_env = tmp_path / ".env"
    overlay_env = tmp_path / ".luckyd-agent-test.env"

    base_env.write_text("SHARED_KEY=shared\nOVERRIDE_KEY=base", encoding="utf-8")
    overlay_env.write_text("OVERRIDE_KEY=overlay\nAGENT_KEY=agent", encoding="utf-8")

    monkeypatch.setattr(config, "BASE_ENV_FILE", base_env)
    monkeypatch.setattr(config, "ENV_FILE", overlay_env)

    config.load_env()

    assert os.environ.get("SHARED_KEY") == "shared"
    assert os.environ.get("OVERRIDE_KEY") == "overlay"
    assert os.environ.get("AGENT_KEY") == "agent"

def test_get_config(monkeypatch):
    """Test get_config function."""
    monkeypatch.setenv("CODING_AGENT_MAX_TURNS", "42")
    monkeypatch.setenv("CODING_AGENT_TEMP", "0.5")
    monkeypatch.setenv("CODING_AGENT_MAX_TOKENS", "4096")
    monkeypatch.setenv("CODING_AGENT_TIMEOUT", "300")
    monkeypatch.setenv("CODING_AGENT_MAX_OUTPUT", "8000")
    monkeypatch.setenv("CODING_AGENT_CMD_TIMEOUT", "120")

    # Mock resolve_provider_config to return something predictable
    monkeypatch.setattr(config, "resolve_provider_config", lambda: {"provider": "mock"})

    cfg = config.get_config()

    assert cfg["provider"] == "mock"
    assert cfg["max_turns"] == 42
    assert cfg["temperature"] == 0.5
    assert cfg["max_tokens"] == 4096
    assert cfg["timeout_sec"] == 300
    assert cfg["max_output_chars"] == 8000
    assert cfg["command_timeout_sec"] == 120
