"""
Shared test fixtures and configuration for LuckyD Code.

Usage:
    pytest -v                       # Run all tests
    pytest -v -m "not slow"         # Skip slow tests
    pytest -v -m integration        # Only integration tests
    pytest --cov                    # With coverage
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to sys.path so tests can find core/, sandbox, etc.
# Works both locally and in CI with pip install -e .
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


import json
import os
import tempfile
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import pytest


# ── Disable API calls in tests ─────────────────────────────────────────
@pytest.fixture(autouse=True)
def _mock_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent accidental API calls during testing.

    Set CODING_AGENT_TEST_API=1 to allow real API calls for integration tests.
    """
    if not os.environ.get("CODING_AGENT_TEST_API"):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-fake-key-for-testing-only")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-fake-key")
        monkeypatch.setenv("GOOGLE_API_KEY", "test-google-key")
        monkeypatch.setenv("ZAI_API_KEY", "sk-test-zai-key")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-openrouter-key")
        monkeypatch.setenv("CODING_AGENT_LOG_LEVEL", "CRITICAL")
        # Trajectory recording is on by default in production; keep it off
        # for the suite so agent.run() tests don't write data/trajectories/.
        # (test_trajectory.py opts back in per-test.)
        monkeypatch.setenv("CODING_AGENT_TRAJECTORY", "0")


# ── Temporary directory fixtures ───────────────────────────────────────


@pytest.fixture
def tmp_project_dir() -> Generator[Path, None, None]:
    """Create a temporary project directory with some test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)

        # Create some test files
        (project_dir / "test.py").write_text("# Test file\nprint('hello')", encoding="utf-8")
        (project_dir / "README.md").write_text("# Test Project\n", encoding="utf-8")
        (project_dir / "requirements.txt").write_text("pytest>=8.0\n", encoding="utf-8")
        (project_dir / ".env").write_text("# Test env\n", encoding="utf-8")

        # Create a subdirectory
        (project_dir / "src").mkdir(exist_ok=True)
        (project_dir / "src" / "__init__.py").write_text("", encoding="utf-8")
        (project_dir / "src" / "module.py").write_text(
            "def foo():\n    return 42\n", encoding="utf-8"
        )

        yield project_dir


@pytest.fixture
def tmp_memory_dir() -> Generator[Path, None, None]:
    """Create a temporary directory for memory store testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


# ── Async fixtures ─────────────────────────────────────────────────────


@pytest.fixture
async def async_context() -> AsyncGenerator[dict, None]:
    """Provide a shared async context dictionary."""
    ctx = {"counter": 0, "items": []}
    yield ctx


# ── Mock data fixtures ─────────────────────────────────────────────────


@pytest.fixture
def sample_messages() -> list[dict]:
    """Sample conversation messages for testing."""
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello, how are you?"},
        {"role": "assistant", "content": "I'm doing great! How can I help you today?"},
    ]


@pytest.fixture
def sample_tool_schema() -> dict:
    """Sample OpenAI tool schema for testing."""
    return {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The file path to read",
                    }
                },
                "required": ["path"],
            },
        },
    }


@pytest.fixture
def sample_model_response() -> dict:
    """Sample LLM response for testing."""
    return {
        "id": "test-response-id",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "This is a test response.",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 50,
            "completion_tokens": 10,
            "total_tokens": 60,
        },
    }


# ── Configuration fixtures ─────────────────────────────────────────────


@pytest.fixture
def temp_config_file(tmp_project_dir: Path) -> Path:
    """Create a temporary config file."""
    config = {
        "model": "test-model",
        "temperature": 0.0,
        "max_tokens": 100,
        "max_turns": 5,
    }
    config_path = tmp_project_dir / "test_config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return config_path


# ── Helper markers ─────────────────────────────────────────────────────


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line("markers", "integration: marks tests as integration tests")
    config.addinivalue_line("markers", "network: marks tests that require network access")
    config.addinivalue_line("markers", "llm: marks tests that call an LLM API")
