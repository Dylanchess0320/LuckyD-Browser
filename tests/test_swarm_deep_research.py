"""Regression tests for the Deep Research swarm (overnight hardening swarm).

Covers real bugs found in features/deep_research/:
  1. synthesizer._rewrite_findings corrupted inline [eID] citations via naive
     sequential str.replace (substring collisions + cascading re-replacement).
  2. researcher._collect_urls_ddg never recorded search budget, so
     DRS_MAX_SEARCH_QUERIES was unenforced for the DDG backend.
  3. RunStore truncated run timestamps to [:18], so runs started within the
     same 10 ms collided in one directory and overwrote each other's artifacts.
  4. LuckyDProvider.grounded() swallowed BudgetExhausted as "no results".
  5. BudgetTracker check-then-increment was not atomic across threads, so
     concurrent workers could overshoot the configured limits.
"""

from __future__ import annotations

import threading

import pytest


def _evidence(i: int, worker_id: str):
    from features.deep_research.schemas import EvidenceCard

    return EvidenceCard(
        id=f"e{i}",
        url=f"https://example.com/{worker_id}/{i}",
        title="t",
        quote="q",
        passage="p",
        worker_id=worker_id,
    )


def _worker_result(task_id, worker_id, findings, n_evidence):
    from features.deep_research.schemas import WorkerResult

    return WorkerResult(
        task_id=task_id,
        worker_id=worker_id,
        findings=findings,
        evidence=[_evidence(i, worker_id) for i in range(n_evidence)],
        gaps=[],
        coverage_score=0.9,
        stop_reason="coverage_threshold",
        follow_up_queries=[],
        rounds_run=1,
    )


class TestRewriteFindings:
    """_rewrite_findings must remap each [eID] token exactly once."""

    def test_multidigit_and_cascade_remap(self):
        # Worker 2 has 12 cards; its local e1 -> global e3, e10 -> e12,
        # e11 -> e13. Old code produced "[e12] and [e13] and [e13]" (the [e1]
        # token cascaded e1->e3->e5->...->e13 and "[e1" matched "[e10]").
        from features.deep_research.workers.synthesizer import (
            _merge_evidence,
            _rewrite_findings,
        )

        r1 = _worker_result("t1", "w1", "w1 cites [e0] and [e1].", 2)
        r2 = _worker_result("t2", "w2", "Claim [e1] then [e10] then [e11].", 12)
        _, maps = _merge_evidence([r1, r2])
        out = _rewrite_findings(maps)
        assert "w1 cites [e0] and [e1]." in out  # first worker map is identity
        assert "Claim [e3] then [e12] then [e13]." in out

    def test_cascade_when_new_ids_overlap_old_ids(self):
        # Worker 2's global ids (e2..e6) overlap its local ids (e0..e4).
        # Old code replaced [e0]->[e2], then [e2]->[e4], then [e4]->[e6],
        # so "Info [e0] more [e2]." became "Info [e6] more [e6].".
        from features.deep_research.workers.synthesizer import (
            _merge_evidence,
            _rewrite_findings,
        )

        r1 = _worker_result("t1", "w1", "first [e0].", 2)
        r2 = _worker_result("t2", "w2", "Info [e0] more [e2].", 5)
        _, maps = _merge_evidence([r1, r2])
        out = _rewrite_findings(maps)
        assert "Info [e2] more [e4]." in out

    def test_unknown_tokens_preserved(self):
        from features.deep_research.workers.synthesizer import _rewrite_findings

        r = _worker_result("t1", "w1", "Known [e0]; stray [e99] stays.", 1)
        out = _rewrite_findings([(r, {"e0": "e0"})])
        assert "[e99]" in out
        assert "Known [e0];" in out

    def test_global_ids_unique_across_workers(self):
        from features.deep_research.workers.synthesizer import _merge_evidence

        r1 = _worker_result("t1", "w1", "a [e0].", 3)
        r2 = _worker_result("t2", "w2", "b [e0].", 3)
        merged, _ = _merge_evidence([r1, r2])
        ids = [e.id for e in merged]
        assert ids == ["e0", "e1", "e2", "e3", "e4", "e5"]


class TestDDGSearchBudget:
    def test_ddg_search_counts_against_budget(self, monkeypatch):
        # Old code never called record_search() on the DDG path, so this
        # second call did not raise despite max_search_queries=1.
        from features.deep_research import config as cfg
        from features.deep_research.runtime import budget as budget_mod
        from features.deep_research.workers import researcher as researcher_mod

        budget_mod.reset_budget()
        monkeypatch.setattr(researcher_mod.DDGSearch, "search", lambda self, q, max_results=8: [])
        monkeypatch.setattr(cfg.settings, "max_search_queries", 1)
        try:
            researcher_mod._collect_urls_ddg("query one")
            with pytest.raises(budget_mod.BudgetExhausted):
                researcher_mod._collect_urls_ddg("query two")
        finally:
            budget_mod.reset_budget()


class TestRunStoreTimestamps:
    def test_concurrent_runs_get_unique_dirs(self, tmp_path):
        # Old code truncated the timestamp to [:18] (2 microsecond digits),
        # so back-to-back runs could share a directory and clobber artifacts.
        from features.deep_research.runtime.run_store import RunStore

        s1 = RunStore("q1", runs_dir=str(tmp_path))
        s2 = RunStore("q2", runs_dir=str(tmp_path))
        try:
            assert s1.dir != s2.dir
            # Full "%Y%m%d-%H%M%S-%f" is 8+1+6+1+6 = 22 chars.
            assert len(s1.ts) == 22, f"timestamp truncated: {s1.ts!r}"
            assert s1.dir.is_dir() and s2.dir.is_dir()
        finally:
            s1.close()
            s2.close()


class TestLuckyDBudgetPropagation:
    def test_grounded_reraises_budget_exhausted(self, monkeypatch):
        # Old code caught Exception around record_search(), swallowing
        # BudgetExhausted and degrading to ("", []) instead of letting the
        # worker loop stop with stop_reason="budget_exhausted".
        from features.deep_research import config as cfg
        from features.deep_research.models.luckyd import LuckyDProvider
        from features.deep_research.runtime import budget as budget_mod

        budget_mod.reset_budget()
        monkeypatch.setattr(cfg.settings, "max_search_queries", 1)
        try:
            budget_mod.get_budget().record_search()  # exhaust the budget
            prov = LuckyDProvider()
            with pytest.raises(budget_mod.BudgetExhausted):
                prov.grounded(role="worker", system="s", user="q")
        finally:
            budget_mod.reset_budget()


class TestBudgetAtomicity:
    def test_no_overshoot_under_threads(self, monkeypatch):
        # Check and increment must be atomic: with the old split
        # (check outside the lock, increment inside), concurrent threads
        # could all pass the check and overshoot the limit.
        from features.deep_research import config as cfg
        from features.deep_research.runtime import budget as budget_mod

        budget_mod.reset_budget()
        monkeypatch.setattr(cfg.settings, "max_llm_calls", 50)
        tracker = budget_mod.get_budget()
        raised = 0
        raise_lock = threading.Lock()

        def worker():
            nonlocal raised
            for _ in range(25):
                try:
                    tracker.record_llm()
                except budget_mod.BudgetExhausted:
                    with raise_lock:
                        raised += 1

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        try:
            assert tracker.usage().llm_calls == 50
            assert raised == 8 * 25 - 50
        finally:
            budget_mod.reset_budget()
