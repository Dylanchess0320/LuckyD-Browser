import pytest
from logging_setup import redact_sensitive


def test_redact_sensitive_empty_string():
    """Test that redacting an empty string returns an empty string."""
    assert redact_sensitive("") == ""


def test_redact_sensitive_normal_string():
    """Test that redacting a normal string works."""
    text = "api_key='sk-1234567890123456789012'"
    redacted = redact_sensitive(text)
    assert "***REDACTED***" in redacted


def test_redact_sensitive_none():
    """Test that redacting None returns None gracefully."""
    assert redact_sensitive(None) is None
