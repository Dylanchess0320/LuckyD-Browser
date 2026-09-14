"""Coverage tests for browser_core.tts — Read Aloud (Windows SAPI via PowerShell).

On this Linux container the speech backend cannot run, so the Windows-only
spawn path is exercised with sys.platform faked to "win32" and a hand-written
fake Popen standing in for subprocess.Popen. No audio is ever produced.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from browser_core import tts


class FakePopen:
    """Hand-written stand-in for subprocess.Popen: records args, never spawns."""

    instances: list[FakePopen] = []

    def __init__(self, args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.killed = False
        self._returncode = None
        FakePopen.instances.append(self)

    def poll(self):
        return self._returncode

    def kill(self):
        self.killed = True
        self._returncode = -9


class ExplodingPopen:
    """Popen stand-in whose constructor raises, like a failed spawn."""

    def __init__(self, *args, **kwargs):
        raise OSError("no powershell here")


@pytest.fixture(autouse=True)
def clean_tts_state(monkeypatch):
    """Reset module globals so tests never leak a fake proc or temp file."""
    FakePopen.instances.clear()
    tts._proc = None
    tts._script = None
    yield
    tts.stop()
    tts._proc = None
    tts._script = None


@pytest.fixture()
def fake_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    return monkeypatch


class TestAvailable:
    def test_unavailable_on_linux(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        assert tts.available() is False

    def test_available_on_windows(self, fake_windows):
        assert tts.available() is True


class TestIsSpeaking:
    def test_no_proc_not_speaking(self):
        assert tts.is_speaking() is False

    def test_running_proc_is_speaking(self, fake_windows, monkeypatch):
        monkeypatch.setattr(subprocess, "Popen", FakePopen)
        assert tts.speak("hello world") is True
        assert tts.is_speaking() is True

    def test_finished_proc_not_speaking(self, fake_windows, monkeypatch):
        monkeypatch.setattr(subprocess, "Popen", FakePopen)
        tts.speak("hello world")
        FakePopen.instances[-1]._returncode = 0
        assert tts.is_speaking() is False


class TestSpeakValidation:
    def test_empty_text_returns_false(self, fake_windows):
        assert tts.speak("") is False
        assert tts.speak(None) is False

    def test_whitespace_only_returns_false(self, fake_windows):
        assert tts.speak("   \n\t  ") is False

    def test_unavailable_platform_returns_false(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        assert tts.speak("hello") is False
        assert FakePopen.instances == []


class TestSpeakWindows:
    def test_spawn_and_script_contents(self, fake_windows, monkeypatch):
        monkeypatch.setattr(subprocess, "Popen", FakePopen)
        assert tts.speak("  hello   world\n") is True

        proc = FakePopen.instances[-1]
        assert proc.args[0] == "powershell"
        assert "-NoProfile" in proc.args
        assert proc.kwargs["stdout"] is subprocess.DEVNULL
        assert proc.kwargs["stderr"] is subprocess.DEVNULL

        # The spoken body is whitespace-collapsed and written to the temp file.
        assert tts._script is not None
        assert tts._script.exists()
        assert tts._script.read_text(encoding="utf-8") == "hello world"
        # The PowerShell command reads that file back as UTF-8.
        ps_command = proc.args[proc.args.index("-Command") + 1]
        assert "System.Speech" in ps_command
        assert str(tts._script) in ps_command

    def test_body_truncated_to_max_chars(self, fake_windows, monkeypatch):
        monkeypatch.setattr(subprocess, "Popen", FakePopen)
        tts.speak("word " * 2000)  # 10000 chars
        assert len(tts._script.read_text(encoding="utf-8")) == tts.MAX_CHARS

    def test_speak_restarts_previous(self, fake_windows, monkeypatch):
        monkeypatch.setattr(subprocess, "Popen", FakePopen)
        tts.speak("first")
        first = FakePopen.instances[-1]
        tts.speak("second")
        # stop() at the top of speak() killed the earlier proc.
        assert first.killed is True
        assert len(FakePopen.instances) == 2
        assert tts._script.read_text(encoding="utf-8") == "second"

    def test_spawn_failure_returns_false_and_cleans_up(self, fake_windows, monkeypatch):
        monkeypatch.setattr(subprocess, "Popen", ExplodingPopen)
        assert tts.speak("hello") is False
        assert tts._proc is None
        assert tts._script is None  # temp script removed by stop()


class TestStop:
    def test_stop_with_nothing_running_is_safe(self):
        tts.stop()  # must not raise
        assert tts._proc is None
        assert tts._script is None

    def test_stop_kills_running_proc_and_removes_script(self, fake_windows, monkeypatch):
        monkeypatch.setattr(subprocess, "Popen", FakePopen)
        tts.speak("hello")
        proc = FakePopen.instances[-1]
        script = tts._script
        assert script.exists()

        tts.stop()
        assert proc.killed is True
        assert not script.exists()
        assert tts._proc is None
        assert tts._script is None
        assert tts.is_speaking() is False

    def test_stop_does_not_kill_finished_proc(self, fake_windows, monkeypatch):
        monkeypatch.setattr(subprocess, "Popen", FakePopen)
        tts.speak("hello")
        proc = FakePopen.instances[-1]
        proc._returncode = 0  # already exited
        tts.stop()
        assert proc.killed is False
        assert tts._proc is None
