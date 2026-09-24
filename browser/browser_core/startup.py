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
