"""LuckyD Browser - a full-featured Chromium-based web browser for Windows."""

__version__ = "10.9.4"

# Shown once after an update (first launch with a new version).
WHATS_NEW = (
    "LuckyD v10.9.4 - Fixed a crash when searching with the AI assistant "
    "sidebar open: the web tab is now isolated from the sidebar during "
    "fullscreen video, so searching no longer glitches or crashes. "
    "v10.9.3 hardening: approval gates are wired once at every entry "
    "point, Edit now asks like Write/Bash, retries stop at two with a "
    "120s cap, a stuck tool loop trips the circuit breaker, and agent "
    "identity no longer leaks into plain terminal tabs."
)
