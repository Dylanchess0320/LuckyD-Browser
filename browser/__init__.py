"""LuckyD Browser - a full-featured Chromium-based web browser for Windows."""

__version__ = "9.5.0"

# Shown once after an update (first launch with a new version).
WHATS_NEW = (
    "LuckyD v9.5.0 - Terminal crash fix: a malformed Bash tool call (missing "
    "command) used to raise out of the tool and kill the agent's terminal "
    "session — the shell tools now validate input and return an error result "
    "instead, and the timeout kill path is covered by regression tests. On "
    "top of everything from 9.4.0."
)
