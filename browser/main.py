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

if getattr(sys, "frozen", False):
    # Packaged builds have no console — without this hook a startup exception
    # dies in a bare "Unhandled exception in script" dialog. Land it in a file
    # next to the data dir instead, so bug reports carry a real traceback.
    import traceback as _traceback
    from datetime import datetime as _datetime

    def _crash_log(exc_type, exc, tb) -> None:
        try:
            log_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "LuckyDBrowser"
            log_dir.mkdir(parents=True, exist_ok=True)
            stamp = _datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with (log_dir / "crash.log").open("a", encoding="utf-8") as fh:
                fh.write(f"\n=== {stamp} ===\n")
                _traceback.print_exception(exc_type, exc, tb, file=fh)
        except Exception:
            pass

    sys.excepthook = _crash_log

# QtWebEngineWidgets must be imported before QApplication is created so Qt
# WebEngine initializes its shared OpenGL context correctly.
from browser_app import BrowserApp
from PySide6 import QtWebEngineWidgets  # noqa: F401


def main() -> int:
    return BrowserApp(sys.argv).run()


if __name__ == "__main__":
    raise SystemExit(main())
