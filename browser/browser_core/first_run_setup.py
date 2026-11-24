"""First-launch agent setup for installed builds.

10.9 removed the installer's post-install bootstrap, so a new user had to
find Start Menu > Agent Setup before the terminal/agent tools worked.  10.9.1
restores that automation without putting a download step in the installer's
``[Run]`` section (which behavioral security products flag):

* the installer writes a one-line ``.first-run-setup`` marker (``1`` when the
  setup task is checked, ``0`` when the user opts out);
* the browser reads it on first launch and starts the bundled, idempotent
  ``LuckyD-Bootstrap.ps1`` in its own console window, so downloads and
  progress stay visible while the browser remains usable;
* ``first_run_setup_done`` in settings.json makes this a one-time action.

The bootstrap only installs missing tools and never writes secrets.  Users
can re-run it any time from Start Menu > Agent Setup / Set up Local AI.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

MARKER_NAME = ".first-run-setup"
DEFAULT_GROUPS = "core,free,ollama"
SETUP_ENV_SKIP = "LUCKYD_SKIP_FIRST_RUN_SETUP"
SETUP_ENV_GROUPS = "LUCKYD_FIRST_RUN_GROUPS"
SETUP_ENV_FORCE = "LUCKYD_FORCE_FIRST_RUN_SETUP"


def app_dir() -> Path:
    """Directory that holds the running browser exe (or the source tree)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bootstrap_path() -> Path | None:
    """Locate the bundled bootstrap script."""
    base = app_dir()
    candidates = [
        base / "LuckyD-Bootstrap.ps1",
        base / "browser" / "installer" / "LuckyD-Bootstrap.ps1",
        Path(__file__).resolve().parent.parent / "installer" / "LuckyD-Bootstrap.ps1",
        Path(__file__).resolve().parent.parent.parent
        / "browser"
        / "installer"
        / "LuckyD-Bootstrap.ps1",
    ]
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def marker_enabled() -> bool:
    """The installer's opt-in marker (missing marker = portable build = on)."""
    try:
        marker = app_dir() / MARKER_NAME
        if not marker.exists():
            return True
        return marker.read_text(encoding="utf-8", errors="replace").strip() != "0"
    except Exception:
        return True


def _env_on(name: str) -> bool:
    return (os.environ.get(name, "") or "").strip().lower() in ("1", "true", "yes", "on")


def enabled(settings) -> bool:
    """Whether first-launch setup should run now."""
    if _env_on(SETUP_ENV_SKIP):
        return False
    try:
        if not bool(settings.get("first_run_setup", True)):
            return False
        if bool(settings.get("first_run_setup_done", False)):
            return False
    except Exception:
        return False
    return marker_enabled()


def powershell_path() -> Path | None:
    """The Windows PowerShell executable, or None when it is missing."""
    powershell = (
        Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    try:
        return powershell if powershell.is_file() else None
    except OSError:
        return None


def launch_first_run_setup(settings) -> bool:
    """Start the bundled bootstrap once; returns True when it was launched.

    Never raises.  Source checkouts are skipped unless
    ``LUCKYD_FORCE_FIRST_RUN_SETUP=1`` (a dev run must not download several
    GB of tools); installed/portable builds run it on first launch.
    """
    if not enabled(settings):
        return False
    if not getattr(sys, "frozen", False) and not _env_on(SETUP_ENV_FORCE):
        return False
    script = bootstrap_path()
    if script is None:
        return False
    groups = (
        (os.environ.get(SETUP_ENV_GROUPS, "") or "").strip()
        or str(settings.get("first_run_setup_groups", "") or "").strip()
        or DEFAULT_GROUPS
    )
    powershell = powershell_path()
    if powershell is None:
        return False
    command = [
        str(powershell),
        "-NoProfile",
        "-ExecutionPolicy",
        "RemoteSigned",
        "-File",
        str(script),
        "-Groups",
        groups,
    ]
    try:
        subprocess.Popen(  # noqa: S603 - fixed argv, bundled script path
            command,
            cwd=str(script.parent),
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
    except Exception:
        return False
    try:
        settings.set("first_run_setup_done", True)
        settings.set("first_run_setup_groups", groups)
    except Exception:
        pass
    return True
