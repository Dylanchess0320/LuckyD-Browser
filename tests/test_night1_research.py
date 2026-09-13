"""Night-1 browser-core audit: research_page.SwarmManager.

Run-id path-traversal guard, on-disk run storage, and start/cancel validation.
(Deep-swarm execution itself is covered by test_browser_research.py.)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from browser_core.research_page import SwarmManager


@pytest.fixture()
def mgr_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SwarmManager:
    from browser_core import research_page

    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(research_page.drs_settings, "runs_dir", str(runs))
    return SwarmManager()


def _seed_run(runs: Path, name: str, query: str = "Q?") -> None:
    d = runs / name
    d.mkdir()
    (d / "report.md").write_text("# Report\n\nbody", encoding="utf-8")
    (d / "plan.json").write_text(json.dumps({"query": query}), encoding="utf-8")
    (d / "evidence.json").write_text(json.dumps([{"u": "https://a.com"}]), encoding="utf-8")


# ── get_run: traversal guard ─────────────────────────────────────────


@pytest.mark.parametrize(
    "evil",
    ["../../etc", "..\\..\\win", "/abs/path", "", ".", "..", "run;rm", "a/b", "a b"],
)
def test_get_run_rejects_traversal_and_garbage(mgr_in_tmp, evil: str) -> None:
    res = mgr_in_tmp.get_run(evil)
    assert res["ok"] is False
    assert "not found" in res["error"]


def test_get_run_reads_seeded_run(mgr_in_tmp: SwarmManager) -> None:
    # runs dir comes from the monkeypatched drs_settings
    from browser_core import research_page

    runs_dir = Path(research_page.drs_settings.runs_dir)
    _seed_run(runs_dir, "run-20260913-000000", query="What is Q?")
    res = mgr_in_tmp.get_run("run-20260913-000000")
    assert res["ok"] is True
    assert res["report_markdown"].startswith("# Report")
    assert res["plan"]["query"] == "What is Q?"
    assert len(res["evidence"]) == 1


def test_get_run_missing_dir(mgr_in_tmp: SwarmManager) -> None:
    res = mgr_in_tmp.get_run("run-20200101-000000")
    assert res == {"ok": False, "error": "Run run-20200101-000000 not found"}


# ── list_runs ────────────────────────────────────────────────────────


def test_list_runs_from_disk(mgr_in_tmp: SwarmManager) -> None:
    from browser_core import research_page

    runs_dir = Path(research_page.drs_settings.runs_dir)
    _seed_run(runs_dir, "run-20260913-000001", query="First?")
    (runs_dir / "not-a-dir.txt").write_text("x", encoding="utf-8")
    rows = mgr_in_tmp.list_runs()
    assert len(rows) == 1
    assert rows[0]["query"] == "First?"
    assert rows[0]["sources"] == 1
    assert rows[0]["has_report"] is True


def test_list_runs_missing_dir_is_empty(mgr_in_tmp: SwarmManager, tmp_path: Path) -> None:
    from browser_core import research_page

    research_page.drs_settings.runs_dir = str(tmp_path / "ghost")
    assert mgr_in_tmp.list_runs() == []


# ── status / cancel / start validation ───────────────────────────────


def test_status_idle_and_cancel_idle(mgr_in_tmp: SwarmManager) -> None:
    assert mgr_in_tmp.get_status() == {"status": "idle", "active": False}
    assert mgr_in_tmp.cancel_run() is False


def test_start_research_rejects_empty_query(mgr_in_tmp: SwarmManager) -> None:
    with pytest.raises(ValueError, match="Research question is required"):
        mgr_in_tmp.start_research("   ")


def test_start_research_rejects_double_start(mgr_in_tmp: SwarmManager) -> None:
    # Simulate a stuck running worker without spawning threads.
    mgr_in_tmp._active_run = {"id": "run-x", "status": "running"}
    with pytest.raises(RuntimeError, match="already running"):
        mgr_in_tmp.start_research("another question")


def test_cancel_running_run(mgr_in_tmp: SwarmManager) -> None:
    mgr_in_tmp._active_run = {"id": "run-x", "status": "running", "start_time": 0.0}
    assert mgr_in_tmp.cancel_run() is True
    st = mgr_in_tmp.get_status("run-x")
    assert st["status"] == "cancelled" and st["active"] is False
