"""LuckyD Browser — entry point.

Run:  python browser/main.py        (from the repo root)
  or: browser\\run_browser.bat
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
# Force deterministic sys.path order: browser/ FIRST, then the repo root.
# The repo root also has core/ and ui.py — browser's own packages must win.
# (Python may have already added the script dir, so remove before inserting.)
for path in (BASE.parent, BASE):
    entry = str(path)
    if entry in sys.path:
        sys.path.remove(entry)
    sys.path.insert(0, entry)

# Expose Chrome DevTools Protocol on localhost only — enables future
# Playwright/browser-use control of the live tabs (see browser/README.md).
os.environ.setdefault("QTWEBENGINE_REMOTE_DEBUGGING", "127.0.0.1:9222")

# QtWebEngineWidgets must be imported before QApplication is created so Qt
# WebEngine initializes its shared OpenGL context correctly.
from browser_app import BrowserApp
from PySide6 import QtWebEngineWidgets  # noqa: F401


def main() -> int:
    return BrowserApp(sys.argv).run()


if __name__ == "__main__":
    raise SystemExit(main())
