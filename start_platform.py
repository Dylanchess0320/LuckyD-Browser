"""
LuckyD One Platform — Unified Launcher.

Starts both:
  - luckyd-code.exe harness server (backend, port 8000)
  - LuckyD Browser (Qt frontend)

Together they form a single powerful AI platform:
  Browser = GUI + LLM providers (Kimi, Claude, etc.)
  Harness = 98 tools + infrastructure

NOTE: the browser now AUTO-STARTS the harness on its own (Settings →
"Start the coding-agent backend on launch"), and the ⚡ HQ button /
Ctrl+Shift+H opens the exe's full workspace in a tab. This launcher is
only needed if you want both running without the browser's autostart.

Usage:
    python start_platform.py            # Launch both
    python start_platform.py --browser  # Browser only
    python start_platform.py --harness  # Harness only
    python start_platform.py --stop     # Stop running instances
"""

from __future__ import annotations

import asyncio
import contextlib
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
HARNESS_EXE = BASE_DIR / "luckyd-code.exe"
BROWSER_MAIN = BASE_DIR / "browser" / "main.py"
HARNESS_PORT = 8000
HARNESS_HOST = "127.0.0.1"


def _print(*args, **kwargs):
    print("[Platform]", *args, **kwargs)


def _check_harness_alive() -> bool:
    """Quick sync check if harness is already running."""
    import urllib.request as req

    try:
        r = req.urlopen(f"http://{HARNESS_HOST}:{HARNESS_PORT}/health", timeout=2)
        return r.status == 200
    except Exception:
        return False


def _start_harness() -> subprocess.Popen | None:
    """Start the luckyd-code.exe harness server."""
    if not HARNESS_EXE.exists():
        _print(f"⚠ Harness exe not found at {HARNESS_EXE}")
        _print("  The browser will run without backend tools.")
        return None

    if _check_harness_alive():
        _print(f"✓ Harness already running on http://{HARNESS_HOST}:{HARNESS_PORT}")
        return None

    _print("Starting harness server...")
    proc = subprocess.Popen(
        [str(HARNESS_EXE), "--web", "--port", str(HARNESS_PORT), "--host", HARNESS_HOST],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )

    # Wait for it to be ready
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if _check_harness_alive():
            _print(f"✓ Harness ready at http://{HARNESS_HOST}:{HARNESS_PORT}")
            return proc
        time.sleep(0.5)

    _print("⚠ Harness did not start in time — continuing anyway")
    return proc


def _start_browser() -> subprocess.Popen:
    """Start the LuckyD Browser."""
    _print("Starting LuckyD Browser...")
    return subprocess.Popen(
        [sys.executable, str(BROWSER_MAIN)],
        cwd=str(BASE_DIR),
    )


def _stop_instances():
    """Stop running harness and browser processes.

    NOTE: this intentionally does NOT call /api/clear — that endpoint wipes
    the agent's session state. Stopping should never destroy user data.
    """
    # Kill luckyd-code.exe processes
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/f", "/im", "luckyd-code.exe"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        subprocess.run(
            ["pkill", "-f", "luckyd-code.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    _print("✓ Stopped all instances")


async def launch(harness: bool = True, browser: bool = True):
    """Launch platform components."""
    procs = []

    if harness:
        p = _start_harness()
        if p:
            procs.append(p)

    if browser:
        p = _start_browser()
        procs.append(p)

    if not procs:
        _print("Nothing to launch. Use --harness, --browser, or both (default).")
        return

    _print()
    _print("━" * 50)
    _print("  LuckyD One Platform is running!")
    if harness:
        _print(f"  • Harness API:  http://{HARNESS_HOST}:{HARNESS_PORT}")
        _print(f"  • Web UI:       http://{HARNESS_HOST}:{HARNESS_PORT}")
        _print(f"  • API Docs:     http://{HARNESS_HOST}:{HARNESS_PORT}/docs")
        _print("  • Tools:        98 available via the harness")
    if browser:
        _print("  • Browser GUI:  LuckyD Browser window")
    _print("  • LLM:          Your choice (Kimi, Claude, GPT, etc.)")
    _print("━" * 50)
    _print()
    _print("Press Ctrl+C to stop everything.")

    try:
        while True:
            await asyncio.sleep(1)
            for p in procs[:]:
                if p.poll() is not None:
                    _print(f"⚠ Process exited unexpectedly (code {p.returncode})")
                    procs.remove(p)
            if not procs:
                _print("All processes exited.")
                break
    except KeyboardInterrupt:
        _print("\nShutting down...")
    finally:
        for p in procs:
            try:
                p.terminate()
                p.wait(timeout=5.0)
            except Exception:
                with contextlib.suppress(Exception):
                    p.kill()


def main():
    args = sys.argv[1:]
    run_harness = True
    run_browser = True

    if "--stop" in args:
        _stop_instances()
        return
    if "--harness" in args and "--browser" not in args:
        run_browser = False
    if "--browser" in args and "--harness" not in args:
        run_harness = False
    if "--help" in args or "-h" in args:
        print(__doc__)
        return

    asyncio.run(launch(harness=run_harness, browser=run_browser))


if __name__ == "__main__":
    main()
