"""
Structured logging system for LuckyD Code.

Provides:
- Structured JSON logging (production) and human-readable (dev)
- Log levels: DEBUG, INFO, WARNING, ERROR, CRITICAL
- Automatic correlation IDs per session
- File + console output
- Sensitive data redaction (API keys, tokens)
- Performance timing context manager

Usage:
    from logging_setup import get_logger
    logger = get_logger(__name__)
    logger.info("Agent started", extra={"model": "deepseek-v4"})
    with logger.timer("api_call"):
        response = await client.chat(messages)
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import sys
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).parent.resolve()
LOG_DIR = PROJECT_DIR / "logs"


def _enable_windows_ansi() -> bool:
    """Turn on ANSI/VT100 escape processing for the Windows console.

    Modern Windows (10+) consoles - Windows Terminal, PowerShell 7, VS Code's
    terminal, and even plain cmd.exe - support ANSI colors, but the console
    mode flag has to be set first. Without this, color codes print as raw
    garbage (or get silently swallowed), which is why colors used to just be
    disabled outright on win32. Returns True if VT processing is active
    (either enabled here, or was already on).
    """
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        enable_virtual_terminal_processing = 0x0004
        std_output_handle = -11
        std_error_handle = -12
        kernel32 = ctypes.windll.kernel32
        ok = True
        for std_handle in (std_output_handle, std_error_handle):
            handle = kernel32.GetStdHandle(std_handle)
            mode = ctypes.c_uint32()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                ok = False
                continue
            if not kernel32.SetConsoleMode(handle, mode.value | enable_virtual_terminal_processing):
                ok = False
        return ok
    except Exception:
        return False


_WINDOWS_ANSI_OK = _enable_windows_ansi()

# ── Sensitive data patterns ──────────────────────────────────────────
SENSITIVE_PATTERNS = [
    (r'(api_key["\']?\s*[:=]\s*["\'])([^"\']+)(["\'])', r"\1***REDACTED***\3"),
    (r"(sk-[a-zA-Z0-9]{20,})", "sk-***REDACTED***"),
    (r"(Bearer\s+)[a-zA-Z0-9._-]+", r"\1***REDACTED***"),
    (r"(Authorization:\s*)[a-zA-Z0-9._-]+", r"\1***REDACTED***"),
    (r'(password["\']?\s*[:=]\s*["\'])([^"\']+)(["\'])', r"\1***REDACTED***"),
    (r'(token["\']?\s*[:=]\s*["\'])([^"\']+)(["\'])', r"\1***REDACTED***"),
]

# ── JSON Formatter (production) ──────────────────────────────────────────


class JSONFormatter(logging.Formatter):
    """Output logs as newline-delimited JSON."""

    def __init__(self, redact: bool = True):
        super().__init__()
        self.redact = redact

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if self.redact:
            log_entry["message"] = redact_sensitive(log_entry["message"])
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
            }
        if hasattr(record, "extra") and record.extra:
            log_entry.update(record.extra)
        correlation_id = getattr(record, "correlation_id", None)
        if correlation_id:
            log_entry["correlation_id"] = correlation_id
        return json.dumps(log_entry, default=str)


class ReadableFormatter(logging.Formatter):
    """Human-readable output for development."""

    COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[41m",
    }
    RESET = "\033[0m"

    def __init__(self, redact: bool = True, use_colors: bool = True):
        super().__init__()
        self.redact = redact
        self.use_colors = use_colors and _WINDOWS_ANSI_OK

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if self.redact:
            message = redact_sensitive(message)
        timestamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S.%f")[:-3]
        level = record.levelname
        if self.use_colors:
            color = self.COLORS.get(level, "")
            return f"{color}{timestamp} {level:<8} {record.name:<20}{self.RESET} {message}"
        return f"{timestamp} {level:<8} {record.name:<20} {message}"


# ── Correlation ID Filter ────────────────────────────────────────────────


class CorrelationFilter(logging.Filter):
    """Add correlation_id to every log record."""

    def __init__(self, correlation_id: str | None = None):
        super().__init__()
        self.correlation_id = correlation_id or ""

    def filter(self, record: logging.LogRecord) -> bool:
        if self.correlation_id:
            record.correlation_id = self.correlation_id
        return True


# ── AgentLogger Wrapper ─────────────────────────────────────────────────


class AgentLogger:
    """Wrapper adding performance timing and convenience methods."""

    def __init__(self, logger: logging.Logger):
        self._logger = logger

    def __getattr__(self, name: str) -> Any:
        return getattr(self._logger, name)

    @contextlib.contextmanager
    def timer(self, operation: str, level: int = logging.INFO, **extra: Any) -> Iterator[None]:
        """Context manager that logs duration of an operation.

        Usage:
            with logger.timer("api_call", model="deepseek"):
                response = await client.chat(messages)
        """
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            self._logger.log(
                level,
                f"[TIMER] {operation} completed in {elapsed:.3f}s",
                extra={
                    "extra": {
                        "operation": operation,
                        "duration_ms": round(elapsed * 1000, 1),
                        **extra,
                    }
                },
            )

    def timed(self, level: int = logging.INFO, **extra: Any):
        """Decorator that logs function execution time.

        Usage:
            @logger.timed()
            async def my_function():
                ...
        """

        def decorator(func):
            import functools

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                with self.timer(func.__name__, level=level, **extra):
                    return await func(*args, **kwargs)

            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                with self.timer(func.__name__, level=level, **extra):
                    return func(*args, **kwargs)

            import asyncio

            if asyncio.iscoroutinefunction(func):
                return async_wrapper
            return sync_wrapper

        return decorator


# ── Global state & factory ──────────────────────────────────────────────

_initialized = False
_root_logger: logging.Logger | None = None


def get_logger(name: str = __name__) -> AgentLogger:
    """Get a logger by name. Configures root on first call."""
    global _initialized, _root_logger

    if not _initialized:
        _configure_root_logger()
        _initialized = True

    logger = logging.getLogger(name)
    return AgentLogger(logger)


def _configure_root_logger(level: str | None = None) -> None:
    """Configure the root logger with console and file handlers."""
    global _root_logger

    config_level = level or os.environ.get("CODING_AGENT_LOG_LEVEL", "INFO").upper()
    log_format = os.environ.get("CODING_AGENT_LOG_FORMAT", "readable")
    log_file = os.environ.get("CODING_AGENT_LOG_FILE", str(LOG_DIR / "agent.log"))

    root = logging.getLogger()
    root.setLevel(getattr(logging, config_level, logging.INFO))
    root.handlers.clear()

    # Console handler
    console = logging.StreamHandler(sys.stderr)
    if log_format == "json":
        console.setFormatter(JSONFormatter())
    else:
        console.setFormatter(ReadableFormatter())
    console.setLevel(logging.DEBUG)
    root.addHandler(console)

    # File handler (rotating)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            log_file,
            when="midnight",
            interval=1,
            backupCount=30,
            encoding="utf-8",
        )
        if log_format == "json":
            file_handler.setFormatter(JSONFormatter())
        else:
            file_handler.setFormatter(ReadableFormatter(use_colors=False))
        file_handler.setLevel(logging.DEBUG)
        root.addHandler(file_handler)

        # Error-only file handler
        error_handler = RotatingFileHandler(
            LOG_DIR / "errors.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=10,
            encoding="utf-8",
        )
        error_handler.setFormatter(JSONFormatter())
        error_handler.setLevel(logging.ERROR)
        root.addHandler(error_handler)

    except (OSError, PermissionError) as e:
        root.warning(f"Could not create log file: {e}")

    root.addFilter(CorrelationFilter())
    _root_logger = root


def set_correlation_id(correlation_id: str) -> None:
    """Set the global correlation ID for the current session."""
    global _root_logger
    if _root_logger:
        for f in _root_logger.filters[:]:
            if isinstance(f, CorrelationFilter):
                _root_logger.removeFilter(f)
        _root_logger.addFilter(CorrelationFilter(correlation_id))


def configure(
    level: str = "INFO", log_format: str = "readable", log_file: str | None = None
) -> None:
    """Explicitly configure logging (overrides defaults).

    Args:
        level: DEBUG, INFO, WARNING, ERROR, CRITICAL
        log_format: 'readable' or 'json'
        log_file: Path to log file (None = default)
    """
    env_overrides = {}
    if level:
        env_overrides["CODING_AGENT_LOG_LEVEL"] = level
    if log_format:
        env_overrides["CODING_AGENT_LOG_FORMAT"] = log_format
    if log_file:
        env_overrides["CODING_AGENT_LOG_FILE"] = log_file

    for key, val in env_overrides.items():
        os.environ[key] = str(val)

    global _initialized
    _initialized = False
    get_logger("root")


def redact_sensitive(text: str) -> str:
    """Redact API keys, tokens, and passwords from log messages."""
    for pattern, replacement in SENSITIVE_PATTERNS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text
