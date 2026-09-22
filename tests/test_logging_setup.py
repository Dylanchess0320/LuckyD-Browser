import logging_setup
from logging_setup import CorrelationFilter, _configure_root_logger, set_correlation_id


def test_set_correlation_id(monkeypatch):
    # Ensure a clean state for testing global variables by restoring them after the test
    original_root_logger = logging_setup._root_logger

    try:
        # Make sure root logger is configured and available
        _configure_root_logger()

        # Test 1: Initial setup
        correlation_id = "test-correlation-123"
        set_correlation_id(correlation_id)

        filters = logging_setup._root_logger.filters
        correlation_filters = [f for f in filters if isinstance(f, CorrelationFilter)]

        assert len(correlation_filters) == 1
        assert correlation_filters[0].correlation_id == correlation_id

        # Test 2: Updating correlation ID (should replace existing)
        new_correlation_id = "test-correlation-456"
        set_correlation_id(new_correlation_id)

        filters = logging_setup._root_logger.filters
        correlation_filters = [f for f in filters if isinstance(f, CorrelationFilter)]

        assert len(correlation_filters) == 1
        assert correlation_filters[0].correlation_id == new_correlation_id

        # Test 3: Edge case where _root_logger is None (should not crash)
        logging_setup._root_logger = None
        set_correlation_id("should-not-crash")

    finally:
        # Restore the original state
        logging_setup._root_logger = original_root_logger
