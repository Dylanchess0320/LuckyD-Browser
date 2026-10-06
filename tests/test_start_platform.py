"""Tests for start_platform.py"""

from __future__ import annotations

import subprocess
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import start_platform


def test_check_harness_alive_success():
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value.status = 200
        assert start_platform._check_harness_alive() is True


def test_check_harness_alive_failure():
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value.status = 404
        assert start_platform._check_harness_alive() is False

    with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
        assert start_platform._check_harness_alive() is False


def test_start_harness_exe_missing():
    with patch("pathlib.Path.exists", return_value=False):
        assert start_platform._start_harness() is None


@patch("start_platform._check_harness_alive")
def test_start_harness_already_running(mock_check):
    mock_check.return_value = True
    with patch("pathlib.Path.exists", return_value=True):
        assert start_platform._start_harness() is None


@patch("start_platform._check_harness_alive")
@patch("subprocess.Popen")
@patch("time.sleep")
def test_start_harness_success(mock_sleep, mock_popen, mock_check):
    mock_check.side_effect = [False, True]

    mock_proc = MagicMock()
    mock_popen.return_value = mock_proc

    with patch("pathlib.Path.exists", return_value=True):
        proc = start_platform._start_harness()
        assert proc is mock_proc
        mock_popen.assert_called_once()


@patch("start_platform._check_harness_alive")
@patch("subprocess.Popen")
@patch("time.sleep")
@patch("time.monotonic")
def test_start_harness_timeout(mock_monotonic, mock_sleep, mock_popen, mock_check):
    mock_check.return_value = False
    mock_monotonic.side_effect = [0.0, 16.0]

    mock_proc = MagicMock()
    mock_popen.return_value = mock_proc

    with patch("pathlib.Path.exists", return_value=True):
        proc = start_platform._start_harness()
        assert proc is mock_proc


@patch("subprocess.Popen")
def test_start_browser(mock_popen):
    mock_proc = MagicMock()
    mock_popen.return_value = mock_proc

    proc = start_platform._start_browser()
    assert proc is mock_proc
    mock_popen.assert_called_once()


@patch("subprocess.run")
def test_stop_instances_win32(mock_run):
    with patch("sys.platform", "win32"):
        start_platform._stop_instances()
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "taskkill" in args


@patch("subprocess.run")
def test_stop_instances_linux(mock_run):
    with patch("sys.platform", "linux"):
        start_platform._stop_instances()
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "pkill" in args


@pytest.mark.asyncio
async def test_launch_nothing():
    with (
        patch("start_platform._start_harness") as mock_sh,
        patch("start_platform._start_browser") as mock_sb,
    ):
        await start_platform.launch(harness=False, browser=False)
        mock_sh.assert_not_called()
        mock_sb.assert_not_called()


@pytest.mark.asyncio
async def test_launch_processes_exit():
    mock_harness_proc = MagicMock()
    mock_harness_proc.poll.return_value = 1

    with (
        patch("start_platform._start_harness", return_value=mock_harness_proc),
        patch("start_platform._start_browser", return_value=None),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        await start_platform.launch(harness=True, browser=False)
        mock_harness_proc.poll.assert_called_once()
        mock_harness_proc.terminate.assert_not_called()


@pytest.mark.asyncio
async def test_launch_keyboard_interrupt():
    mock_harness_proc = MagicMock()
    mock_harness_proc.poll.return_value = None

    with (
        patch("start_platform._start_harness", return_value=mock_harness_proc),
        patch("start_platform._start_browser", return_value=None),
        patch("asyncio.sleep", new_callable=AsyncMock, side_effect=KeyboardInterrupt),
    ):
        await start_platform.launch(harness=True, browser=False)

        mock_harness_proc.terminate.assert_called_once()
        mock_harness_proc.wait.assert_called_once_with(timeout=5.0)


@patch("asyncio.run")
@patch("start_platform.launch", MagicMock())
def test_main_default(mock_run):
    with patch("sys.argv", ["start_platform.py"]):
        start_platform.main()
        mock_run.assert_called_once()


@patch("start_platform._stop_instances")
def test_main_stop(mock_stop):
    with patch("sys.argv", ["start_platform.py", "--stop"]):
        start_platform.main()
        mock_stop.assert_called_once()


@patch("asyncio.run")
@patch("start_platform.launch", MagicMock())
def test_main_harness_only(mock_run):
    with patch("sys.argv", ["start_platform.py", "--harness"]):
        start_platform.main()
        mock_run.assert_called_once()


@patch("asyncio.run")
@patch("start_platform.launch", MagicMock())
def test_main_browser_only(mock_run):
    with patch("sys.argv", ["start_platform.py", "--browser"]):
        start_platform.main()
        mock_run.assert_called_once()


@patch("builtins.print")
def test_main_help(mock_print):
    with patch("sys.argv", ["start_platform.py", "--help"]):
        start_platform.main()
        mock_print.assert_called_once_with(start_platform.__doc__)
