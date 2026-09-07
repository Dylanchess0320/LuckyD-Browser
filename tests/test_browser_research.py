"""Tests for the LuckyD Browser Deep Research Swarm Tool."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

# Ensure browser and repo root are in path
REPO_ROOT = Path(__file__).resolve().parent.parent
BROWSER_DIR = REPO_ROOT / "browser"
for p in (REPO_ROOT, BROWSER_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from browser_core.research_page import SwarmManager, research_html


class TestBrowserResearchPage:
    def test_research_html_structure(self):
        token = "test-auth-token-12345"
        html = research_html(token)
        assert "LuckyD Deep Research" in html
        assert "SWARM v3.8" in html
        assert token in html
        assert "st-plan" in html
        assert "st-res" in html
        assert "st-syn" in html
        assert "st-crit" in html
        assert "st-fin" in html
        assert "depth-select" in html
        assert "backend-select" in html
        assert "provider-select" in html
        assert "context-check" in html
        assert "events-box" in html
        assert "tab-report" in html
        assert "tab-evidence" in html

    def test_swarm_manager_lifecycle(self):
        mgr = SwarmManager()
        st = mgr.get_status()
        assert st["status"] == "idle"
        assert st["active"] is False

        # Start mock run
        run_id = mgr.start_research("What is quantum computing?", provider="mock", dry_run=True)
        assert run_id.startswith("run-")

        # Wait for completion (mock should be < 5s)
        completed = False
        for _ in range(20):
            st = mgr.get_status(run_id)
            if st.get("status") == "completed":
                completed = True
                break
            time.sleep(0.3)

        assert completed, f"Swarm did not complete in time, status: {st}"
        assert st["stage"] == "done"
        assert len(st["report_markdown"]) > 50
        assert len(st["events"]) > 0

    def test_swarm_manager_validation(self):
        mgr = SwarmManager()
        with pytest.raises(ValueError):
            mgr.start_research("")

    def test_swarm_manager_list_runs(self):
        mgr = SwarmManager()
        runs = mgr.list_runs()
        assert isinstance(runs, list)
