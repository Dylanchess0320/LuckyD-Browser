#!/usr/bin/env python3
"""
LuckyD Code — harness launcher (live source).

Thin launcher for the Harness HQ web server. The browser's
``browser_core/harness_bridge._find_exe()`` prefers this file over the frozen
``luckyd-code.exe`` so the HQ tab / Harness mode run the LIVE, FIXED agent
backend (core/llm_client.py) instead of the stale exe that raised
``Illegal header value b'Bearer '``.

It accepts the exact same CLI the bridge uses::

    luckyd-harness.py --web --port 8000 --host 127.0.0.1

and forwards every argument verbatim to ``web_server.main()``.

When the *browser* is frozen (installed app), ``web_server`` may not be
importable from the browser's bundle. In that case we fall back to launching a
real Python interpreter on the repo's ``web_server.py`` (dev machine), so the
HQ still runs the fixed live source.
"""

from __future__ import annotations

import os
import subprocess
import sys

# Make sure the repo root (this file's folder) is importable when launched
# from any working directory.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)


def _run_live() -> None:
    import web_server

    web_server.main()


def _frozen_fallback() -> None:
    """Frozen-browser context: run web_server.py with a real Python interpreter.

    Search order for the live backend source:
      1. The dev repo (full backend incl. optional tools) — best experience.
      2. A bundled copy next to the launcher (lean: core chat + memory only).
    """
    script_name = "web_server.py"
    search_dirs = [
        _HERE,  # live repo (dev machine)
        os.path.join(_HERE, "_internal"),  # frozen: bundled copy
        os.path.join(os.getcwd()),  # current dir
        os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop", "coding-agent"),
        os.path.join(os.path.expanduser("~"), "Desktop", "coding-agent"),
    ]
    script = next(
        (
            os.path.join(d, script_name)
            for d in search_dirs
            if os.path.exists(os.path.join(d, script_name))
        ),
        None,
    )
    if script is None:
        sys.stderr.write(
            "web_server.py not found. The live harness needs the coding-agent "
            "backend source (or the bundled copy).\n"
        )
        sys.exit(1)
    backend_dir = os.path.dirname(os.path.abspath(script))
    env = os.environ.copy()
    env["PYTHONPATH"] = backend_dir + os.pathsep + env.get("PYTHONPATH", "")
    for py in ("python", "python3", "py"):
        try:
            subprocess.run([py, script] + sys.argv[1:], check=False, cwd=backend_dir, env=env)
            return
        except FileNotFoundError:
            continue
    sys.stderr.write("No Python interpreter found to run the live harness.\n")
    sys.exit(1)


if __name__ == "__main__":
    try:
        _run_live()
    except ImportError:
        _frozen_fallback()
