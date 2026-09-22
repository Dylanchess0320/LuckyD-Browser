import asyncio
import logging
from unittest.mock import patch

import pytest

import logging_setup


@pytest.fixture(autouse=True)
def reset_logging_state():
    # Save original state
    orig_initialized = logging_setup._initialized
    orig_root_logger = logging_setup._root_logger

    # Reset state for test
    logging_setup._initialized = False
    logging_setup._root_logger = None

    yield

    # Restore original state
    logging_setup._initialized = orig_initialized
    logging_setup._root_logger = orig_root_logger


def test_get_logger_returns_agent_logger():
    logger = logging_setup.get_logger("test_logger")
    assert isinstance(logger, logging_setup.AgentLogger)
    assert logger.name == "test_logger"

    # Check delegation to underlying logger
    assert hasattr(logger, "info")
    assert callable(logger.info)


def test_get_logger_singleton():
    # First call should configure root logger
    with patch("logging_setup._configure_root_logger") as mock_configure:
        _ = logging_setup.get_logger("test_logger1")
        assert logging_setup._initialized is True
        mock_configure.assert_called_once()

        # Second call should not configure root logger again
        _ = logging_setup.get_logger("test_logger2")
        mock_configure.assert_called_once()


def test_agent_logger_timer():
    logger = logging_setup.get_logger("test_timer")

    with (
        patch.object(logger._logger, "log") as mock_log,
        patch("time.perf_counter", side_effect=[0.0, 1.5]),
    ):
        with logger.timer("test_op", level=logging.DEBUG, extra_key="extra_val"):
            pass

        mock_log.assert_called_once()
        args, kwargs = mock_log.call_args
        assert args[0] == logging.DEBUG
        assert "[TIMER] test_op completed in 1.500s" in args[1]
        assert kwargs["extra"]["extra"]["operation"] == "test_op"
        assert kwargs["extra"]["extra"]["duration_ms"] == 1500.0
        assert kwargs["extra"]["extra"]["extra_key"] == "extra_val"


def test_agent_logger_timed_sync():
    logger = logging_setup.get_logger("test_timed_sync")

    @logger.timed(level=logging.WARNING, test_arg="value")
    def sync_func():
        return "success"

    with (
        patch.object(logger._logger, "log") as mock_log,
        patch("time.perf_counter", side_effect=[0.0, 0.5]),
    ):
        result = sync_func()

        assert result == "success"
        mock_log.assert_called_once()
        args, kwargs = mock_log.call_args
        assert args[0] == logging.WARNING
        assert "[TIMER] sync_func completed in 0.500s" in args[1]
        assert kwargs["extra"]["extra"]["test_arg"] == "value"


@pytest.mark.asyncio
async def test_agent_logger_timed_async():
    logger = logging_setup.get_logger("test_timed_async")

    @logger.timed(level=logging.INFO, async_arg="async_value")
    async def async_func():
        await asyncio.sleep(0.01)
        return "async_success"

    with (
        patch.object(logger._logger, "log") as mock_log,
        patch("time.perf_counter", side_effect=[0.0, 2.0]),
    ):
        result = await async_func()

        assert result == "async_success"
        mock_log.assert_called_once()
        args, kwargs = mock_log.call_args
        assert args[0] == logging.INFO
        assert "[TIMER] async_func completed in 2.000s" in args[1]
        assert kwargs["extra"]["extra"]["async_arg"] == "async_value"
