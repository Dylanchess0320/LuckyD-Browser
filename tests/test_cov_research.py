"""Coverage push: browser_core/research_page.py remaining paths.

Covers: module re-import bootstrapping (sys.path insert, frozen _internal,
brand import fallback), _on_event stale-event guard + stage mapping +
search/evidence counters, context-augmented query, backend/provider/dry_run
application + settings restore, worker exception -> failed status,
get_status() past-run merge, and list_runs()/get_run() disk edge cases.
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

from browser_core import research_page
from browser_core.research_page import SwarmManager

from features.deep_research.runtime.events import RunEvent


@pytest.fixture()
def tmp_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(research_page.drs_settings, "runs_dir", str(runs))
    return runs


def _fake_swarm(
    monkeypatch: pytest.MonkeyPatch, behavior: str = "ok", captured: dict | None = None
):
    """Replace run_swarm with a fast deterministic fake (no network)."""

    async def _fake(query, **kwargs):
        if captured is not None:
            captured["query"] = query
            captured["kwargs"] = kwargs
        if behavior == "boom":
            raise RuntimeError("swarm exploded")
        emitter = kwargs.get("emitter")
        if emitter is not None and behavior == "events":
            emitter.emit(RunEvent(node="planner", message="plan ready"))
            emitter.emit(RunEvent(node="researcher", message="search finished"))
            emitter.emit(RunEvent(node="researcher", message="evidence passage stored"))
            emitter.emit(RunEvent(node="synthesizer", message="draft written"))
            emitter.emit(RunEvent(node="critic", message="review notes"))
            emitter.emit(RunEvent(node="verifier", message="claims checked"))
            emitter.emit(RunEvent(node="finalizer", message="report done"))
        return "# Report"

    monkeypatch.setattr(research_page, "run_swarm", _fake)


def _seed_run(runs: Path, name: str, **files: str) -> Path:
    d = runs / name
    d.mkdir()
    for fname, content in files.items():
        (d / fname).write_text(content, encoding="utf-8")
    return d


# ── _on_event: stale-event guard ─────────────────────────────────────


def test_on_event_ignores_stale_or_cleared_run(monkeypatch, tmp_runs) -> None:
    captured: dict = {}
    _fake_swarm(monkeypatch, captured=captured)
    mgr = SwarmManager()
    mgr.start_research("stale question")
    assert mgr.wait_for_completion(timeout=30)
    emitter = captured["kwargs"]["emitter"]

    # Active run cleared (e.g. replaced) -> event dropped silently.
    mgr._active_run = None
    emitter.emit(RunEvent(node="researcher", message="late event"))
    assert mgr._active_run is None

    # Id mismatch -> event dropped silently.
    mgr._active_run = {"id": "run-other", "status": "running", "events": []}
    emitter.emit(RunEvent(node="researcher", message="wrong run"))
    assert mgr._active_run["events"] == []


def test_on_event_stage_mapping_and_counters(monkeypatch, tmp_runs) -> None:
    _fake_swarm(monkeypatch, behavior="events")
    mgr = SwarmManager()
    mgr.start_research("mapping question")
    assert mgr.wait_for_completion(timeout=30)
    run = mgr._active_run
    assert run["status"] == "completed"
    assert run["stage"] == "done"  # successful completion overwrites the event's final stage
    assert run["searches_count"] == 1
    assert run["evidence_count"] == 1
    assert len(run["events"]) == 7
    assert run["report_markdown"] == "# Report"


# ── _worker: context, backend/provider, failure ──────────────────────


def test_worker_augments_query_with_context(monkeypatch, tmp_runs) -> None:
    captured: dict = {}
    _fake_swarm(monkeypatch, captured=captured)
    mgr = SwarmManager()
    mgr.start_research("base question", context="page says hello")
    assert mgr.wait_for_completion(timeout=30)
    assert "base question" in captured["query"]
    assert "Additional browser context:" in captured["query"]
    assert "page says hello" in captured["query"]


def test_worker_applies_backend_provider_dry_run_then_restores(monkeypatch, tmp_runs) -> None:
    captured: dict = {}
    _fake_swarm(monkeypatch, captured=captured)
    before = (
        research_page.drs_settings.research_rounds,
        research_page.drs_settings.search_backend,
        research_page.drs_settings.provider,
    )
    mgr = SwarmManager()
    mgr.start_research("q", backend="ddg", provider="gemini", dry_run=True)
    assert mgr.wait_for_completion(timeout=30)
    # provider kwarg passed through (dry_run forces the *setting* to mock,
    # but the explicit provider argument is still forwarded).
    assert captured["kwargs"]["provider"] == "gemini"
    after = (
        research_page.drs_settings.research_rounds,
        research_page.drs_settings.search_backend,
        research_page.drs_settings.provider,
    )
    assert after == before


def test_worker_ignores_unknown_backend(monkeypatch, tmp_runs) -> None:
    _fake_swarm(monkeypatch)
    before_backend = research_page.drs_settings.search_backend
    mgr = SwarmManager()
    mgr.start_research("q", backend="bogus-backend", provider="auto")
    assert mgr.wait_for_completion(timeout=30)
    assert research_page.drs_settings.search_backend == before_backend


def test_worker_exception_marks_run_failed(monkeypatch, tmp_runs) -> None:
    _fake_swarm(monkeypatch, behavior="boom")
    mgr = SwarmManager()
    run_id = mgr.start_research("doomed question")
    assert mgr.wait_for_completion(timeout=30)
    run = mgr._active_run
    assert run["id"] == run_id
    assert run["status"] == "failed"
    assert run["stage"] == "error"
    assert run["error"] == "swarm exploded"
    assert run["end_time"] is not None


# ── get_status: past-run merge ───────────────────────────────────────


def test_get_status_merges_past_run(tmp_runs) -> None:
    _seed_run(
        tmp_runs,
        "run-20200101-000000",
        **{
            "plan.json": json.dumps({"query": "Old question?"}),
            "report.md": "# Old report",
        },
    )
    mgr = SwarmManager()
    mgr._active_run = {"id": "run-current", "status": "running"}
    st = mgr.get_status("run-20200101-000000")
    assert st["status"] == "completed"
    assert st["active"] is False
    assert st["ok"] is True
    assert st["plan"]["query"] == "Old question?"


def test_get_status_unknown_past_run_falls_through(tmp_runs) -> None:
    mgr = SwarmManager()
    mgr._active_run = {"id": "run-current", "status": "running"}
    st = mgr.get_status("run-19990101-000000")
    assert st["active"] is True
    assert st["id"] == "run-current"


# ── list_runs disk edge cases ────────────────────────────────────────


def test_list_runs_plan_json_invalid_falls_back_to_dirname(tmp_runs) -> None:
    _seed_run(tmp_runs, "run-badplan", **{"plan.json": "not json{{"})
    rows = SwarmManager().list_runs()
    assert len(rows) == 1
    assert rows[0]["query"] == "run-badplan"


def test_list_runs_plan_without_query_uses_dirname(tmp_runs) -> None:
    _seed_run(tmp_runs, "run-noquery", **{"plan.json": json.dumps({"steps": []})})
    rows = SwarmManager().list_runs()
    assert rows[0]["query"] == "run-noquery"


def test_list_runs_report_as_directory_and_blank_report(tmp_runs) -> None:
    d1 = _seed_run(tmp_runs, "run-repdir")
    (d1 / "report.md").mkdir()  # read_text raises -> swallowed
    _seed_run(tmp_runs, "run-blankrep", **{"report.md": "\n\n   \n"})
    _seed_run(tmp_runs, "run-titlerep", **{"report.md": "# Fancy Title\n\nbody"})
    rows = {r["id"]: r for r in SwarmManager().list_runs()}
    assert rows["run-repdir"]["query"] == "run-repdir"
    assert rows["run-repdir"]["has_report"] is True
    assert rows["run-blankrep"]["query"] == "run-blankrep"
    assert rows["run-titlerep"]["query"] == "Fancy Title"


def test_list_runs_evidence_invalid_json_counts_zero(tmp_runs) -> None:
    _seed_run(
        tmp_runs,
        "run-badev",
        **{"evidence.json": "[broken", "report.md": "# R"},
    )
    rows = SwarmManager().list_runs()
    assert rows[0]["sources"] == 0


def test_list_runs_runs_dir_is_file_returns_empty(tmp_path, monkeypatch) -> None:
    f = tmp_path / "not-a-dir"
    f.write_text("x")
    monkeypatch.setattr(research_page.drs_settings, "runs_dir", str(f))
    assert SwarmManager().list_runs() == []


# ── get_run: audit file ──────────────────────────────────────────────


def test_get_run_reads_audit_file(tmp_runs) -> None:
    _seed_run(
        tmp_runs,
        "run-20200102-000000",
        **{
            "report.md": "# R",
            "citation_audit.json": json.dumps({"score": 0.9}),
        },
    )
    res = SwarmManager().get_run("run-20200102-000000")
    assert res["ok"] is True
    assert res["audit"] == {"score": 0.9}


# ── module re-import: bootstrap branches ─────────────────────────────


def test_reimport_inserts_missing_root_and_frozen_internal(monkeypatch, tmp_path) -> None:
    """Lines 22 / 24-26: _ROOT absent from sys.path + frozen layout."""
    import importlib

    root = str(_REPO_ROOT)
    fake_exe = tmp_path / "app.exe"
    fake_exe.write_text("x")
    internal = tmp_path / "_internal"
    internal.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != root])
    importlib.reload(research_page)
    assert sys.path[0] == str(internal)  # line 26 ran
    assert root in sys.path  # line 22 ran
    monkeypatch.undo()  # restore real sys.path / frozen / executable
    importlib.reload(research_page)  # normal module state for later tests


def test_reimport_brand_import_fallback(monkeypatch) -> None:
    """Lines 31-33: `from browser_core.brand import ...` failing at import."""
    import importlib

    monkeypatch.setitem(sys.modules, "browser_core.brand", None)
    monkeypatch.setitem(sys.modules, "browser_core.page_shell", None)
    importlib.reload(research_page)
    # The except-ImportError fallback (lines 31-33) imported these names via
    # the browser.browser_core.* path instead.
    assert "browser.browser_core.brand" in sys.modules
    assert callable(research_page.css_vars)
    assert isinstance(research_page.VARS_PLACEHOLDER, str)
    monkeypatch.undo()
    importlib.reload(research_page)  # normal module state for later tests


# ── round 2: remaining branch gaps ───────────────────────────────────


def test_worker_ignores_unknown_provider(monkeypatch, tmp_runs) -> None:
    """provider not in _VALID_PROVIDERS -> settings.provider untouched."""
    _fake_swarm(monkeypatch)
    before = research_page.drs_settings.provider
    mgr = SwarmManager()
    mgr.start_research("q", provider="bogus-provider")
    assert mgr.wait_for_completion(timeout=30)
    assert research_page.drs_settings.provider == before


def test_worker_skips_completion_when_run_replaced(monkeypatch, tmp_runs) -> None:
    """The success-path `if self._active_run ...` False branch (line 184)."""
    mgr = SwarmManager()

    async def _fake(query, **kwargs):
        mgr._active_run = None  # replaced/cancelled while the swarm worked
        return "# Report"

    monkeypatch.setattr(research_page, "run_swarm", _fake)
    mgr.start_research("q")
    assert mgr.wait_for_completion(timeout=30)
    assert mgr._active_run is None


def test_worker_skips_failure_marking_when_run_replaced(monkeypatch, tmp_runs) -> None:
    """The except-path `if self._active_run ...` False branch (line 195)."""
    mgr = SwarmManager()

    async def _fake(query, **kwargs):
        mgr._active_run = None
        raise RuntimeError("boom after replace")

    monkeypatch.setattr(research_page, "run_swarm", _fake)
    mgr.start_research("q")
    assert mgr.wait_for_completion(timeout=30)
    assert mgr._active_run is None


def test_list_runs_bare_dir_and_nonlist_evidence(tmp_runs) -> None:
    """No plan.json and no report.md (279->291); evidence.json not a list."""
    bare = tmp_runs / "run-bare"
    bare.mkdir()
    (bare / "evidence.json").write_text('{"not": "a list"}', encoding="utf-8")
    rows = {r["id"]: r for r in SwarmManager().list_runs()}
    assert rows["run-bare"]["query"] == "run-bare"
    assert rows["run-bare"]["sources"] == 0


# ── 9.6: engine chip / evidence propagation / honest cancel ─────────────


def test_research_html_has_engine_chip_and_key_hints() -> None:
    html = research_page.research_html()
    assert "engine-chip" in html
    assert "st-ver" in html
    assert "needs API key" in html
    assert "1 round" in html and "3 rounds" in html


def test_resolved_engine_published_and_evidence_propagated(monkeypatch, tmp_runs) -> None:
    captured: dict = {}
    _fake_swarm(monkeypatch, captured=captured)
    # Seed a run dir like RunStore would: the worker must report it back
    # into run_dir and load evidence.json on completion.
    seed = tmp_runs / "run-20200102-000000"
    seed.mkdir()
    import json as _json

    cards = [
        {"id": "e0", "url": "https://example.com/a", "title": "A"},
        {"id": "e1", "url": "https://example.com/b", "title": "B"},
    ]
    (seed / "evidence.json").write_text(_json.dumps(cards), encoding="utf-8")
    mgr = SwarmManager()
    run_id = mgr.start_research("engine question", provider="mock", dry_run=True)
    assert mgr.wait_for_completion(timeout=30)
    run = mgr._active_run
    assert run["id"] == run_id
    assert "mock" in (run.get("engine_label") or "")
    assert run.get("run_dir") == str(seed), "worker must report RunStore.dir back into run_dir"
    assert run.get("evidence") == cards
    assert run.get("evidence_count") == 2
    st = mgr.get_status(run_id)
    assert "engine_label" in st


def test_keyed_backend_without_key_fails_fast(monkeypatch, tmp_runs) -> None:
    import pytest

    for var in ("TAVILY_API_KEY", "BRAVE_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    mgr = SwarmManager()
    with pytest.raises(ValueError, match="no API key configured"):
        mgr.start_research("q", backend="tavily")
    with pytest.raises(ValueError, match="no API key configured"):
        mgr.start_research("q", backend="brave")
    with pytest.raises(ValueError, match="no API key configured"):
        mgr.start_research("q", backend="gemini")


def test_premium_worker_missing_key_is_plain_language(monkeypatch) -> None:
    from features.deep_research.workers import researcher as rmod

    for var in ("TAVILY_API_KEY", "BRAVE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    try:
        rmod._collect_urls_premium("q", "tavily")
        raise AssertionError("tavily without key must raise")
    except RuntimeError as e:
        assert "No API key configured" in str(e)
    try:
        rmod._collect_urls_premium("q", "brave")
        raise AssertionError("brave without key must raise")
    except RuntimeError as e:
        assert "No API key configured" in str(e)


def test_cancel_marks_cancelled_and_blocks_overlap(monkeypatch, tmp_runs) -> None:
    import threading

    started = threading.Event()
    release = threading.Event()

    async def _slow(query, **kwargs):
        started.set()
        release.wait(timeout=30)
        return "# slow report"

    monkeypatch.setattr(research_page, "run_swarm", _slow)
    mgr = SwarmManager()
    mgr.start_research("slow question")
    assert started.wait(timeout=30)
    assert mgr.cancel_run() is True
    st = mgr.get_status()
    assert st["status"] == "cancelled"
    # A new start must not overlap the still-live worker: it joins first.
    release.set()
    assert mgr.wait_for_completion(timeout=30)
    run_id2 = mgr.start_research("next question", provider="mock", dry_run=True)
    assert run_id2
    assert mgr.wait_for_completion(timeout=30)


def test_cancelled_worker_stays_cancelled(monkeypatch, tmp_runs) -> None:
    import threading

    started = threading.Event()
    release = threading.Event()

    async def _slow(query, **kwargs):
        started.set()
        release.wait(timeout=30)
        return "# slow report"

    monkeypatch.setattr(research_page, "run_swarm", _slow)
    mgr = SwarmManager()
    mgr.start_research("slow question")
    assert started.wait(timeout=30)
    assert mgr.cancel_run() is True
    release.set()
    assert mgr.wait_for_completion(timeout=30)
    assert mgr._active_run["status"] == "cancelled"


def test_plain_failure_mapping() -> None:
    mgr = SwarmManager()
    assert "No API key configured" in mgr._plain_failure(RuntimeError("missing GEMINI_API_KEY"))
    assert "google-genai" in mgr._plain_failure(RuntimeError("google-genai is not installed"))


def test_reimport_frozen_without_internal_dir(monkeypatch, tmp_path) -> None:
    """Frozen layout with no _internal dir: the exists() guard skips the insert."""
    import importlib

    fake_exe = tmp_path / "app.exe"
    fake_exe.write_text("x")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    importlib.reload(research_page)
    assert str(tmp_path / "_internal") not in sys.path
    monkeypatch.undo()
    importlib.reload(research_page)
