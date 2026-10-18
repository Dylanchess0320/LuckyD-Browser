"""Startup URL handling: open URLs/files passed on the command line.

Qt-free on purpose — BrowserApp wires it, and the parser is unit-tested
without Qt. Single-instance forwarding (route argv to an existing window)
is a follow-up; each launch currently opens its own window.
"""

from __future__ import annotations

import re
from pathlib import Path

#: Bare domains gain https:// — requires a dotted TLD so search text
#: ("how to bake bread") and flags never become fake URLs.
_BARE_DOMAIN = re.compile(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(/.*)?$")


#: Flags that answer `--version` (checked case-insensitively, without the
#: program name). `-v` is included on purpose — `browser/main.py` only
#: answers CLI flags on a real launch (`if __name__ == "__main__"`), so
#: `pytest -v` (or any importer) never trips it.
_VERSION_FLAGS = frozenset({"--version", "-v", "-V", "version"})

#: Flags that answer `--help`.
_HELP_FLAGS = frozenset({"--help", "-h", "-?", "/?", "help", "--usage", "-help"})


def cli_action_from_argv(argv: list[str] | tuple[str, ...]) -> str | None:
    """CLI action requested by argv (`"version"`, `"help"`, or None).

    Scans args after argv[0] (the program) case-insensitively. Returns
    ``"help"`` when any help flag is present (help wins when both are
    passed), ``"version"`` when any version flag is present, else None
    for a normal browser launch. Never raises — a flag problem must
    never block startup.
    """
    try:
        args = [str(a or "").strip() for a in list(argv)[1:]]
    except Exception:
        return None
    lowered = {a.lower() for a in args if a}
    if lowered & {f.lower() for f in _HELP_FLAGS}:
        return "help"
    if lowered & {f.lower() for f in _VERSION_FLAGS}:
        return "version"
    return None


def version_line(version: str | None) -> str:
    """One-line version string for `--version` (Qt-free, never raises)."""
    ver = (version or "").strip() or "unknown"
    return f"LuckyD Browser {ver}"


def help_text(version: str | None) -> str:
    """Multi-line help text for `--help` (Qt-free, never raises)."""
    ver = (version or "").strip() or "unknown"
    return (
        f"LuckyD Browser {ver}\n"
        "\n"
        "Usage:\n"
        "  LuckyDBrowser.exe [options] [url-or-file ...]\n"
        "\n"
        "Options:\n"
        "  --version      Print the version and exit\n"
        "  --help         Show this help and exit\n"
        "\n"
        "Startup URLs/files:\n"
        "  Full URLs (https://, luckyd://, file://, ...) open as tabs.\n"
        "  Existing files open as file:// URLs; bare domains gain https://.\n"
        "  Flags (-*) and anything else are ignored.\n"
        "\n"
        "Examples:\n"
        "  LuckyDBrowser.exe https://example.com ./notes.html\n"
    )


def startup_urls_from_argv(argv: list[str] | tuple[str, ...]) -> list[str]:
    """URLs to open from command-line args (argv[0], the program, is skipped).

    - Args with a scheme (https://, luckyd://, file://, ...) pass through.
    - Existing files become file:// URLs.
    - Qt-style flags (-*) and anything else are ignored.
    """
    urls: list[str] = []
    for arg in list(argv)[1:]:
        text = (arg or "").strip()
        if not text or text.startswith("-"):
            continue
        if "://" in text:
            urls.append(text)
            continue
        if Path(text).is_file():
            urls.append(Path(text).resolve().as_uri())
            continue
        if _BARE_DOMAIN.match(text):
            urls.append(f"https://{text}")
    return urls
