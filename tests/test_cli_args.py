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

from browser_core.startup import startup_urls_from_argv


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
