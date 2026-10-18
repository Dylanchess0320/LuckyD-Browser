"""P1: startup URL/file CLI args.

``LuckyDBrowser.exe https://example.com ./notes.html`` opens each target
in its own tab instead of silently dropping argv on the floor.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))

from browser_core.startup import (
    cli_action_from_argv,
    help_text,
    startup_urls_from_argv,
    version_line,
)


def test_full_urls_pass_through():
    argv = ["LuckyDBrowser.exe", "https://example.com/a?b=1", "luckyd://settings"]
    assert startup_urls_from_argv(argv) == ["https://example.com/a?b=1", "luckyd://settings"]


def test_bare_domain_gains_https():
    assert startup_urls_from_argv(["exe", "example.com/docs"]) == ["https://example.com/docs"]


def test_existing_file_becomes_file_url(tmp_path):
    page = tmp_path / "notes.html"
    page.write_text("<h1>hi</h1>", encoding="utf-8")
    (out,) = startup_urls_from_argv(["exe", str(page)])
    assert out.startswith("file://")
    assert out.endswith("notes.html")


def test_flags_search_text_and_missing_files_ignored(tmp_path):
    argv = [
        "exe",
        "--disable-gpu",
        "-qmljsdebugger",
        "how to bake bread",
        str(tmp_path / "nope.html"),
    ]
    assert startup_urls_from_argv(argv) == []


def test_argv0_skipped_and_empty_safe():
    assert startup_urls_from_argv(["LuckyDBrowser.exe"]) == []
    assert startup_urls_from_argv([]) == []
    assert startup_urls_from_argv(["exe", "", "  "]) == []


def test_cli_action_version_flags():
    for flag in ("--version", "-v", "-V", "version", "--VERSION"):
        assert cli_action_from_argv(["exe", flag]) == "version"


def test_cli_action_help_flags():
    for flag in ("--help", "-h", "-?", "/?", "help", "--usage", "-help"):
        assert cli_action_from_argv(["exe", flag]) == "help"


def test_cli_action_help_wins_and_normal_launch_is_none():
    assert cli_action_from_argv(["exe", "--help", "--version"]) == "help"
    assert cli_action_from_argv(["exe"]) is None
    assert cli_action_from_argv([]) is None
    assert cli_action_from_argv(["exe", "https://example.com"]) is None
    # Qt/Chromium engine flags are NOT our flags — normal launch.
    assert cli_action_from_argv(["exe", "--disable-gpu", "-qmljsdebugger"]) is None


def test_version_line_and_help_text():
    assert version_line("10.7.3") == "LuckyD Browser 10.7.3"
    assert version_line("") == "LuckyD Browser unknown"
    assert version_line(None) == "LuckyD Browser unknown"
    text = help_text("10.7.3")
    assert text.startswith("LuckyD Browser 10.7.3")
    assert "--version" in text and "--help" in text
    assert help_text(None).startswith("LuckyD Browser unknown")
