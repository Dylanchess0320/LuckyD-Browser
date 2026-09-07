"""Read Aloud — speak page or selection text.

Windows uses System.Speech (SAPI) via a PowerShell child we can kill.
Other platforms no-op with available() == False. No extra pip deps.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_CHARS = 6000

_proc: subprocess.Popen | None = None
_script: Path | None = None


def available() -> bool:
    return sys.platform.startswith("win")


def is_speaking() -> bool:
    return _proc is not None and _proc.poll() is None


def stop() -> None:
    global _proc, _script
    proc, _proc = _proc, None
    if proc is not None and proc.poll() is None:
        with contextlib.suppress(Exception):
            proc.kill()
    if _script is not None:
        with contextlib.suppress(Exception):
            _script.unlink(missing_ok=True)
        _script = None


def speak(text: str) -> bool:
    """Start speaking `text`. Returns False when TTS is unavailable."""
    stop()
    body = " ".join((text or "").split())
    if not body or not available():
        return False
    body = body[:MAX_CHARS]
    global _proc, _script
    try:
        fd, raw = tempfile.mkstemp(prefix="luckyd-tts-", suffix=".txt")
        with os.fdopen(fd, "wb") as handle:
            handle.write(body.encode("utf-8"))
        _script = Path(raw)
        # Read the file as UTF-8 and speak it. -NoProfile keeps it fast.
        ps = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$t = Get-Content -Raw -Encoding UTF8 -LiteralPath '{_script}'; "
            "$s.Speak($t)"
        )
        _proc = subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-Command",
                ps,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        stop()
        return False
    return True
