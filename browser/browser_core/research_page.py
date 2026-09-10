"""Deep Research Swarm — in-browser interactive multi-agent research tool.

Provides:
- SwarmManager: manages background multi-agent research runs with live event streaming
- research_html(): serves the modern responsive single-page web app for the tool
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if getattr(sys, "frozen", False):
    _internal = Path(sys.executable).resolve().parent / "_internal"
    if _internal.exists() and str(_internal) not in sys.path:
        sys.path.insert(0, str(_internal))

from features.deep_research.config import settings as drs_settings
from features.deep_research.graph import run_swarm
from features.deep_research.runtime.events import EventEmitter, RunEvent
from tools.deep_research_tool import _VALID_BACKENDS, _VALID_PROVIDERS, DEPTH_PRESETS


class SwarmManager:
    """Manages active and historical Deep Research swarm runs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active_run: dict[str, Any] | None = None
        self._cancel_requested = False
        self._thread: threading.Thread | None = None

    def start_research(
        self,
        query: str,
        depth: str = "standard",
        backend: str = "auto",
        provider: str = "auto",
        context: str = "",
        dry_run: bool = False,
    ) -> str:
        """Start a new research run in a background thread."""
        query = (query or "").strip()
        if not query:
            raise ValueError("Research question is required")

        with self._lock:
            if self._active_run and self._active_run.get("status") == "running":
                raise RuntimeError("A research swarm is already running")

            run_id = f"run-{time.strftime('%Y%m%d-%H%M%S')}"
            self._cancel_requested = False
            self._active_run = {
                "id": run_id,
                "query": query,
                "depth": depth,
                "backend": backend,
                "provider": provider,
                "context": context,
                "dry_run": bool(dry_run),
                "status": "running",
                "stage": "planning",
                "start_time": time.time(),
                "end_time": None,
                "elapsed": 0.0,
                "events": [],
                "evidence_count": 0,
                "searches_count": 0,
                "report_markdown": "",
                "plan": None,
                "error": None,
                "run_dir": None,
            }

        emitter = EventEmitter()

        def _on_event(ev: RunEvent) -> None:
            with self._lock:
                if not self._active_run or self._active_run["id"] != run_id:
                    return
                node = ev.node.lower()
                msg = ev.message

                # Map node to high-level stage
                if node in ("init", "planner"):
                    self._active_run["stage"] = "planning"
                elif node in ("researcher", "research"):
                    self._active_run["stage"] = "researching"
                    if "search" in msg.lower():
                        self._active_run["searches_count"] += 1
                    if "evidence" in msg.lower() or "passage" in msg.lower():
                        self._active_run["evidence_count"] += 1
                elif node in ("synthesizer", "synthesize"):
                    self._active_run["stage"] = "synthesizing"
                elif node in ("critic", "critique"):
                    self._active_run["stage"] = "critiquing"
                elif node in ("verifier", "verify"):
                    self._active_run["stage"] = "verifying"
                elif node in ("finalizer", "finalize"):
                    self._active_run["stage"] = "finalizing"

                self._active_run["elapsed"] = round(time.time() - self._active_run["start_time"], 1)
                self._active_run["events"].append(
                    {
                        "ts": round(ev.ts, 2),
                        "node": ev.node,
                        "message": ev.message,
                        "level": ev.level,
                    }
                )

        emitter.subscribe(_on_event)

        def _worker() -> None:
            effective_query = query
            if context.strip():
                effective_query = (
                    f"{query}\n\nAdditional browser context:\n{context.strip()[:2500]}"
                )

            # Save previous settings
            saved = (
                drs_settings.research_rounds,
                drs_settings.searches_per_round,
                drs_settings.max_urls_per_round,
                drs_settings.max_passages_per_worker,
                drs_settings.max_iterations,
                drs_settings.max_parallel,
                drs_settings.search_backend,
                drs_settings.provider,
            )

            try:
                # Apply preset
                preset = DEPTH_PRESETS.get(depth.lower().strip(), DEPTH_PRESETS["standard"])
                drs_settings.research_rounds = preset["research_rounds"]
                drs_settings.searches_per_round = preset["searches_per_round"]
                drs_settings.max_urls_per_round = preset["max_urls_per_round"]
                drs_settings.max_passages_per_worker = preset["max_passages_per_worker"]
                drs_settings.max_iterations = preset["max_iterations"]
                drs_settings.max_parallel = preset["max_parallel"]

                if backend in _VALID_BACKENDS:
                    drs_settings.search_backend = backend
                if provider in _VALID_PROVIDERS:
                    drs_settings.provider = provider
                if dry_run:
                    drs_settings.provider = "mock"

                # Run swarm
                report = asyncio.run(
                    run_swarm(
                        effective_query,
                        dry_run=dry_run,
                        no_tui=True,
                        provider=provider if provider != "auto" else None,
                        emitter=emitter,
                    )
                )

                with self._lock:
                    if self._active_run and self._active_run["id"] == run_id:
                        self._active_run["status"] = "completed"
                        self._active_run["stage"] = "done"
                        self._active_run["report_markdown"] = report or ""
                        self._active_run["end_time"] = time.time()
                        self._active_run["elapsed"] = round(
                            time.time() - self._active_run["start_time"], 1
                        )

            except Exception as e:
                with self._lock:
                    if self._active_run and self._active_run["id"] == run_id:
                        self._active_run["status"] = "failed"
                        self._active_run["stage"] = "error"
                        self._active_run["error"] = str(e)
                        self._active_run["end_time"] = time.time()
                        self._active_run["elapsed"] = round(
                            time.time() - self._active_run["start_time"], 1
                        )
            finally:
                # Restore settings
                with contextlib.suppress(Exception):
                    (
                        drs_settings.research_rounds,
                        drs_settings.searches_per_round,
                        drs_settings.max_urls_per_round,
                        drs_settings.max_passages_per_worker,
                        drs_settings.max_iterations,
                        drs_settings.max_parallel,
                        drs_settings.search_backend,
                        drs_settings.provider,
                    ) = saved

        self._thread = threading.Thread(target=_worker, daemon=True)
        self._thread.start()
        return run_id

    def cancel_run(self) -> bool:
        with self._lock:
            if not self._active_run or self._active_run.get("status") != "running":
                return False
            self._cancel_requested = True
            self._active_run["status"] = "cancelled"
            self._active_run["stage"] = "cancelled"
            self._active_run["end_time"] = time.time()
            self._active_run["elapsed"] = round(time.time() - self._active_run["start_time"], 1)
            return True

    def get_status(self, run_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            if not self._active_run:
                return {"status": "idle", "active": False}
            if run_id and self._active_run.get("id") != run_id:
                # Maybe a past run
                past = self.get_run(run_id)
                if past.get("ok"):
                    return {"status": "completed", "active": False, **past}
            return {
                "active": self._active_run.get("status") == "running",
                **self._active_run,
            }

    def list_runs(self) -> list[dict[str, Any]]:
        """List historical research runs from disk."""
        runs_dir = Path(drs_settings.runs_dir)
        if not runs_dir.exists():
            return []

        results = []
        try:
            for item in sorted(runs_dir.iterdir(), reverse=True):
                if not item.is_dir():
                    continue
                report_file = item / "report.md"
                plan_file = item / "plan.json"
                evidence_file = item / "evidence.json"

                query = item.name
                if plan_file.exists():
                    try:
                        pdata = json.loads(plan_file.read_text(encoding="utf-8"))
                        if isinstance(pdata, dict) and pdata.get("query"):
                            query = pdata["query"]
                    except Exception:
                        pass
                elif report_file.exists():
                    try:
                        lines = [
                            ln.strip()
                            for ln in report_file.read_text(encoding="utf-8").splitlines()
                            if ln.strip()
                        ]
                        if lines:
                            query = lines[0].lstrip("#").strip()
                    except Exception:
                        pass

                sources_count = 0
                if evidence_file.exists():
                    try:
                        evdata = json.loads(evidence_file.read_text(encoding="utf-8"))
                        if isinstance(evdata, list):
                            sources_count = len(evdata)
                    except Exception:
                        pass

                results.append(
                    {
                        "id": item.name,
                        "query": query,
                        "timestamp": item.stat().st_mtime,
                        "time_str": time.strftime(
                            "%Y-%m-%d %H:%M", time.localtime(item.stat().st_mtime)
                        ),
                        "sources": sources_count,
                        "has_report": report_file.exists(),
                    }
                )
        except Exception:
            pass
        return results[:50]

    def get_run(self, run_id: str) -> dict[str, Any]:
        """Load full details for a past research run."""
        runs_dir = Path(drs_settings.runs_dir)
        target = runs_dir / run_id
        if not target.exists() or not target.is_dir():
            return {"ok": False, "error": f"Run {run_id} not found"}

        report_file = target / "report.md"
        evidence_file = target / "evidence.json"
        plan_file = target / "plan.json"
        audit_file = target / "citation_audit.json"

        report_md = report_file.read_text(encoding="utf-8") if report_file.exists() else ""
        evidence = []
        if evidence_file.exists():
            with contextlib.suppress(Exception):
                evidence = json.loads(evidence_file.read_text(encoding="utf-8"))

        plan = {}
        if plan_file.exists():
            with contextlib.suppress(Exception):
                plan = json.loads(plan_file.read_text(encoding="utf-8"))

        audit = {}
        if audit_file.exists():
            with contextlib.suppress(Exception):
                audit = json.loads(audit_file.read_text(encoding="utf-8"))

        return {
            "ok": True,
            "id": run_id,
            "report_markdown": report_md,
            "evidence": evidence,
            "plan": plan,
            "audit": audit,
        }


# Singleton manager
swarm_manager = SwarmManager()


def research_html() -> str:
    """Return the single-page application HTML for the Deep Research Swarm Tool.

    Authentication uses the HttpOnly session cookie the browser sets on its
    own profile (4.0) — no token is embedded in the page.
    """
    # NOTE: the f-prefix below is load-bearing — the page's JS is written with
    # doubled braces ({{ }}) that the f-string collapses to single braces
    # (F541 is a false positive here; silenced via per-file-ignores).
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LuckyD Deep Research Swarm</title>
<style>
  :root {{
    --bg: #090d16;
    --surface: #0f172a;
    --surface2: #1e293b;
    --border: #334155;
    --accent: #38bdf8;
    --accent2: #818cf8;
    --accent-glow: rgba(56, 189, 248, 0.25);
    --success: #34d399;
    --warning: #fbbf24;
    --danger: #f87171;
    --text: #f1f5f9;
    --muted: #94a3b8;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg);
    color: var(--text);
    font: 14px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    display: flex;
    flex-direction: column;
    height: 100vh;
    overflow: hidden;
  }}
  header {{
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 12px 24px;
    display: flex;
    align-items: center;
    gap: 16px;
    flex-shrink: 0;
  }}
  .brand {{
    font-size: 17px;
    font-weight: 700;
    letter-spacing: -0.3px;
    display: flex;
    align-items: center;
    gap: 8px;
    color: #fff;
  }}
  .brand-badge {{
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    color: #000;
    font-size: 11px;
    font-weight: 800;
    padding: 2px 7px;
    border-radius: 6px;
    letter-spacing: 0.5px;
  }}
  .pill {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 12px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 600;
    background: rgba(148, 163, 184, 0.12);
    color: var(--muted);
    border: 1px solid var(--border);
  }}
  .pill.running {{
    background: rgba(56, 189, 248, 0.15);
    color: var(--accent);
    border-color: var(--accent);
    animation: pulse 2s infinite;
  }}
  .pill.completed {{
    background: rgba(52, 211, 153, 0.15);
    color: var(--success);
    border-color: var(--success);
  }}
  .pill.error {{
    background: rgba(248, 113, 113, 0.15);
    color: var(--danger);
    border-color: var(--danger);
  }}
  @keyframes pulse {{
    0%, 100% {{ opacity: 1; }}
    50% {{ opacity: 0.65; }}
  }}
  .sp {{ flex: 1; }}
  .btn {{
    background: var(--surface2);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 7px 14px;
    border-radius: 8px;
    font: 600 13px system-ui;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    transition: all 0.15s ease;
  }}
  .btn:hover {{
    background: #27354f;
    border-color: var(--accent);
    color: #fff;
  }}
  .btn-primary {{
    background: linear-gradient(135deg, #0284c7, #4f46e5);
    border: none;
    color: #fff;
    box-shadow: 0 0 16px var(--accent-glow);
  }}
  .btn-primary:hover {{
    filter: brightness(1.15);
    transform: translateY(-1px);
  }}
  .btn-danger {{
    background: rgba(248, 113, 113, 0.15);
    border-color: var(--danger);
    color: var(--danger);
  }}
  .btn-danger:hover {{
    background: var(--danger);
    color: #000;
  }}

  /* Main layout */
  .workspace {{
    display: flex;
    flex: 1;
    overflow: hidden;
  }}
  .left-pane {{
    width: 440px;
    min-width: 360px;
    max-width: 540px;
    background: var(--surface);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    overflow-y: auto;
    padding: 20px;
    gap: 18px;
  }}
  .right-pane {{
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    background: var(--bg);
  }}

  /* Form controls */
  .form-group {{
    display: flex;
    flex-direction: column;
    gap: 6px;
  }}
  label {{
    font-size: 12px;
    font-weight: 600;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }}
  textarea, select, input[type="text"] {{
    background: var(--surface2);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 8px;
    padding: 10px 12px;
    font: inherit;
    font-size: 13px;
    outline: none;
    transition: border 0.15s;
  }}
  textarea:focus, select:focus, input[type="text"]:focus {{
    border-color: var(--accent);
    box-shadow: 0 0 0 2px var(--accent-glow);
  }}
  textarea {{
    min-height: 90px;
    resize: vertical;
    font-family: inherit;
  }}
  .grid-2 {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
  }}
  .checkbox-card {{
    background: rgba(255, 255, 255, 0.03);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px 12px;
    display: flex;
    align-items: flex-start;
    gap: 10px;
    cursor: pointer;
    user-select: none;
  }}
  .checkbox-card:hover {{
    background: rgba(255, 255, 255, 0.05);
  }}
  .checkbox-card input {{
    margin-top: 3px;
  }}
  .checkbox-text {{
    display: flex;
    flex-direction: column;
    gap: 2px;
  }}
  .checkbox-title {{
    font-weight: 600;
    font-size: 12px;
    color: #fff;
  }}
  .checkbox-desc {{
    font-size: 11px;
    color: var(--muted);
    line-height: 1.35;
  }}

  /* Swarm stages pipeline */
  .pipeline-card {{
    background: rgba(255, 255, 255, 0.02);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }}
  .stages {{
    display: flex;
    gap: 4px;
  }}
  .stage-step {{
    flex: 1;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 8px 4px;
    text-align: center;
    font-size: 11px;
    font-weight: 600;
    color: var(--muted);
    transition: all 0.2s ease;
  }}
  .stage-step.active {{
    background: rgba(56, 189, 248, 0.2);
    border-color: var(--accent);
    color: #fff;
    box-shadow: 0 0 10px var(--accent-glow);
  }}
  .stage-step.done {{
    background: rgba(52, 211, 153, 0.15);
    border-color: var(--success);
    color: var(--success);
  }}
  .metrics-strip {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 6px;
    text-align: center;
  }}
  .metric-item {{
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 6px 4px;
  }}
  .metric-val {{
    font-size: 15px;
    font-weight: 700;
    color: #fff;
  }}
  .metric-lbl {{
    font-size: 10px;
    color: var(--muted);
    text-transform: uppercase;
  }}

  /* Right pane: viewer */
  .viewer-tabs {{
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 8px 20px;
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
  }}
  .vtab {{
    background: transparent;
    border: 1px solid transparent;
    color: var(--muted);
    padding: 6px 12px;
    border-radius: 6px;
    font-weight: 600;
    font-size: 13px;
    cursor: pointer;
  }}
  .vtab.active {{
    background: var(--surface2);
    border-color: var(--border);
    color: #fff;
  }}
  .viewer-content {{
    flex: 1;
    overflow-y: auto;
    padding: 24px 32px;
  }}

  /* Report presentation */
  .report-box {{
    max-width: 900px;
    margin: 0 auto;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 36px 44px;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.35);
  }}
  .report-box h1 {{
    font-size: 24px;
    margin-bottom: 16px;
    color: #fff;
    border-bottom: 1px solid var(--border);
    padding-bottom: 12px;
  }}
  .report-box h2 {{
    font-size: 19px;
    margin: 24px 0 10px;
    color: var(--accent);
  }}
  .report-box h3 {{
    font-size: 15px;
    margin: 18px 0 8px;
    color: #e2e8f0;
  }}
  .report-box p {{
    margin-bottom: 14px;
    line-height: 1.7;
    color: #cbd5e1;
  }}
  .report-box ul, .report-box ol {{
    margin: 10px 0 16px 24px;
    color: #cbd5e1;
    line-height: 1.6;
  }}
  .report-box li {{
    margin-bottom: 6px;
  }}
  .report-box blockquote {{
    border-left: 3px solid var(--accent);
    background: rgba(56, 189, 248, 0.06);
    padding: 12px 18px;
    margin: 16px 0;
    border-radius: 0 8px 8px 0;
    color: #93c5fd;
  }}
  .report-box pre {{
    background: #060a12;
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px;
    overflow-x: auto;
    margin: 16px 0;
    font-family: Consolas, monospace;
    font-size: 12px;
    color: #f1f5f9;
  }}
  .report-box code {{
    background: rgba(255, 255, 255, 0.08);
    padding: 2px 6px;
    border-radius: 4px;
    font-family: Consolas, monospace;
    font-size: 12px;
  }}
  .report-box a {{
    color: var(--accent);
    text-decoration: none;
  }}
  .report-box a:hover {{
    text-decoration: underline;
  }}

  /* Events stream */
  .events-box {{
    background: #060a12;
    border: 1px solid var(--border);
    border-radius: 8px;
    height: 200px;
    overflow-y: auto;
    font-family: "Cascadia Code", Consolas, monospace;
    font-size: 11px;
    padding: 10px 12px;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }}
  .event-row {{
    display: flex;
    gap: 8px;
    line-height: 1.4;
  }}
  .event-node {{
    color: var(--accent2);
    font-weight: 700;
    min-width: 90px;
  }}
  .event-msg {{
    color: #cbd5e1;
    word-break: break-word;
  }}
  .event-msg.ok {{ color: var(--success); }}
  .event-msg.warn {{ color: var(--warning); }}
  .event-msg.err {{ color: var(--danger); }}

  /* Drawer for history */
  .drawer {{
    position: fixed;
    top: 0; right: 0; bottom: 0;
    width: 380px;
    background: var(--surface);
    border-left: 1px solid var(--border);
    box-shadow: -10px 0 30px rgba(0, 0, 0, 0.5);
    display: flex;
    flex-direction: column;
    transform: translateX(100%);
    transition: transform 0.25s cubic-bezier(0.16, 1, 0.3, 1);
    z-index: 1000;
  }}
  .drawer.open {{
    transform: translateX(0);
  }}
  .drawer-header {{
    padding: 16px 20px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 12px;
  }}
  .drawer-content {{
    flex: 1;
    overflow-y: auto;
    padding: 12px 16px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }}
  .run-item {{
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px 12px;
    cursor: pointer;
    transition: border 0.15s;
  }}
  .run-item:hover {{
    border-color: var(--accent);
  }}
  .run-query {{
    font-weight: 600;
    font-size: 13px;
    color: #fff;
    margin-bottom: 4px;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }}
  .run-meta {{
    font-size: 11px;
    color: var(--muted);
  }}
  .empty-hint {{
    text-align: center;
    color: var(--muted);
    padding: 60px 20px;
    font-size: 13px;
  }}
</style>
</head>
<body>

<header>
  <div class="brand">
    <span>🔬</span> LuckyD Deep Research
    <span class="brand-badge">SWARM v5.0</span>
  </div>
  <span id="status-pill" class="pill">idle</span>
  <span class="sp"></span>
  <button class="btn" id="btn-runs" onclick="toggleDrawer()">📜 Past Runs (<span id="runs-count">0</span>)</button>
  <button class="btn" onclick="window.open('/mesh', '_blank')">🛸 Agent Mesh</button>
  <button class="btn" onclick="window.open('/hq', '_blank')">⚡ Agent HQ</button>
</header>

<div class="workspace">
  <!-- Left configuration & progress pane -->
  <div class="left-pane">
    <div class="form-group">
      <label for="query-input">Research Question or Topic</label>
      <textarea id="query-input" placeholder="e.g. Compare modern transformer inference architectures (vLLM, SGLang, TensorRT-LLM) on latency, throughput, and memory efficiency..."></textarea>
    </div>

    <div class="grid-2">
      <div class="form-group">
        <label for="depth-select">Swarm Depth</label>
        <select id="depth-select">
          <option value="quick">⚡ Quick (1 round · fast)</option>
          <option value="standard" selected>🎯 Standard (2 rounds · balanced)</option>
          <option value="deep">🔬 Deep (3 rounds · thorough)</option>
          <option value="max">🧠 Max (4 rounds · exhaustive)</option>
        </select>
      </div>
      <div class="form-group">
        <label for="backend-select">Search Backend</label>
        <select id="backend-select">
          <option value="auto" selected>✨ Auto (Smart Engine)</option>
          <option value="ddg">🦆 DuckDuckGo (Free / Keyless)</option>
          <option value="gemini">🌐 Gemini Grounding</option>
          <option value="tavily">🔍 Tavily AI Search</option>
          <option value="brave">🦁 Brave Search</option>
        </select>
      </div>
    </div>

    <div class="form-group">
      <label for="provider-select">LLM Provider</label>
      <select id="provider-select">
        <option value="auto" selected>✨ Auto (Free Zen pool → OpenRouter → Ollama)</option>
        <option value="opencode">🆓 OpenCode Zen Free (nemotron-3-ultra)</option>
        <option value="openrouter">🌐 OpenRouter Free (:free models)</option>
        <option value="ollama">💻 Local Ollama (offline, unlimited)</option>
        <option value="gemini">♊ Google Gemini (native grounding)</option>
        <option value="luckyd">🤖 LuckyD / Active Browser Provider</option>
        <option value="mock">🧪 Mock (Offline Fast Test)</option>
      </select>
    </div>

    <div class="checkbox-card" onclick="toggleContextCheckbox(event)">
      <input type="checkbox" id="context-check" checked>
      <div class="checkbox-text">
        <span class="checkbox-title">Inject Current Tab as Context</span>
        <span class="checkbox-desc" id="context-desc">No active tab attached</span>
      </div>
    </div>

    <div style="display: flex; gap: 8px; margin-top: 4px;">
      <button class="btn btn-primary" id="btn-launch" style="flex: 1;" onclick="startSwarm()">
        🚀 Launch Swarm
      </button>
      <button class="btn btn-danger" id="btn-stop" style="display: none;" onclick="cancelSwarm()">
        ⏹ Cancel
      </button>
    </div>

    <!-- Swarm pipeline visualizer -->
    <div class="pipeline-card">
      <label>Swarm Architecture & Execution</label>
      <div class="stages">
        <div class="stage-step" id="st-plan">1. Plan</div>
        <div class="stage-step" id="st-res">2. Research</div>
        <div class="stage-step" id="st-syn">3. Synth</div>
        <div class="stage-step" id="st-crit">4. Audit</div>
        <div class="stage-step" id="st-fin">5. Report</div>
      </div>
      <div class="metrics-strip">
        <div class="metric-item"><div class="metric-val" id="m-elapsed">0s</div><div class="metric-lbl">Elapsed</div></div>
        <div class="metric-item"><div class="metric-val" id="m-evidence">0</div><div class="metric-lbl">Sources</div></div>
        <div class="metric-item"><div class="metric-val" id="m-searches">0</div><div class="metric-lbl">Queries</div></div>
        <div class="metric-item"><div class="metric-val" id="m-status">—</div><div class="metric-lbl">Stage</div></div>
      </div>
    </div>

    <div class="form-group">
      <label>Live Swarm Event Stream</label>
      <div class="events-box" id="events-box">
        <div style="color: var(--muted); font-style: italic;">Ready to launch swarm...</div>
      </div>
    </div>
  </div>

  <!-- Right report & evidence viewer pane -->
  <div class="right-pane">
    <div class="viewer-tabs">
      <button class="vtab active" id="tab-report" onclick="switchTab('report')">📄 Citation Report</button>
      <button class="vtab" id="tab-evidence" onclick="switchTab('evidence')">📑 Evidence Cards</button>
      <button class="vtab" id="tab-raw" onclick="switchTab('raw')">📝 Raw Markdown</button>
      <span class="sp"></span>
      <button class="btn" id="btn-copy" onclick="copyReport()">📋 Copy Report</button>
      <button class="btn" id="btn-save" onclick="saveReport()">💾 Download .md</button>
    </div>

    <div class="viewer-content">
      <div id="view-report" class="report-box">
        <div class="empty-hint">
          <h3>🔬 No report generated yet</h3>
          <p style="margin-top: 8px;">Enter your research topic on the left and click <b>Launch Swarm</b>.</p>
          <p style="margin-top: 4px; font-size: 12px; color: var(--muted);">The multi-agent swarm will plan sub-questions, dispatch parallel workers, extract verified evidence, synthesize findings, and produce a grounded report with full citations.</p>
        </div>
      </div>
      <div id="view-evidence" style="display: none; max-width: 900px; margin: 0 auto;"></div>
      <div id="view-raw" style="display: none; max-width: 900px; margin: 0 auto;">
        <textarea id="raw-markdown" style="width: 100%; height: 500px; font-family: Consolas, monospace; font-size: 12px;" readonly></textarea>
      </div>
    </div>
  </div>
</div>

<!-- History slide-out drawer -->
<div class="drawer" id="runs-drawer">
  <div class="drawer-header">
    <b style="color: #fff; font-size: 15px;">📜 Research Runs History</b>
    <span class="sp"></span>
    <button class="btn" onclick="toggleDrawer()">✕</button>
  </div>
  <div class="drawer-content" id="runs-list">
    <div class="empty-hint">Loading historical runs...</div>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);
let activeRunId = null;
let pollTimer = null;
let pageContextData = "";
let currentMarkdown = "";
let currentEvidence = [];

// API helper — auth rides the HttpOnly session cookie (4.0); same-origin
// fetch() sends it automatically, so no Authorization header is needed.
async function api(path, opts = {{}}) {{
  const headers = Object.assign({{'Content-Type': 'application/json'}}, opts.headers || {{}});
  const res = await fetch(path, Object.assign({{}}, opts, {{headers}}));
  if (!res.ok) {{
    const err = await res.json().catch(() => ({{error: 'HTTP ' + res.status}}));
    throw new Error(err.error || 'Request failed');
  }}
  return await res.json();
}}

// Initialize from URL parameters
window.addEventListener('DOMContentLoaded', () => {{
  const params = new URLSearchParams(window.location.search);
  const q = params.get('q');
  if (q) $('query-input').value = q;

  const ctxUrl = params.get('context_url');
  const ctxTitle = params.get('context_title');
  if (ctxUrl || ctxTitle) {{
    pageContextData = `${{ctxTitle || 'Current Page'}}: ${{ctxUrl || ''}}`;
    $('context-desc').textContent = pageContextData;
    $('context-check').checked = true;
  }} else {{
    $('context-desc').textContent = 'No current tab provided';
    $('context-check').checked = false;
  }}

  loadRuns();
  checkStatus();
}});

function toggleContextCheckbox(e) {{
  if (e.target.tagName !== 'INPUT') {{
    $('context-check').checked = !$('context-check').checked;
  }}
}}

function toggleDrawer() {{
  $('runs-drawer').classList.toggle('open');
}}

async function loadRuns() {{
  try {{
    const res = await api('/research/runs');
    const runs = res.runs || [];
    $('runs-count').textContent = runs.length;
    const list = $('runs-list');
    if (!runs.length) {{
      list.innerHTML = '<div class="empty-hint">No past runs found in storage.</div>';
      return;
    }}
    list.innerHTML = '';
    runs.forEach(r => {{
      const div = document.createElement('div');
      div.className = 'run-item';
      div.innerHTML = `
        <div class="run-query">${{escapeHtml(r.query || r.id)}}</div>
        <div class="run-meta">${{r.time_str || ''}} · ${{r.sources || 0}} sources</div>
      `;
      div.onclick = () => loadRunDetails(r.id);
      list.appendChild(div);
    }});
  }} catch (e) {{
    console.warn('Could not load runs:', e);
  }}
}}

async function loadRunDetails(runId) {{
  try {{
    const data = await api('/research/run?id=' + encodeURIComponent(runId));
    if (!data.ok) throw new Error(data.error);
    renderReport(data.report_markdown || '');
    currentEvidence = data.evidence || [];
    renderEvidence(currentEvidence);
    $('status-pill').textContent = 'historical';
    $('status-pill').className = 'pill completed';
    toggleDrawer();
  }} catch (e) {{
    alert('Error loading run: ' + e.message);
  }}
}}

async function startSwarm() {{
  const query = $('query-input').value.trim();
  if (!query) {{
    alert('Please enter a research topic or question.');
    $('query-input').focus();
    return;
  }}

  const depth = $('depth-select').value;
  const backend = $('backend-select').value;
  const provider = $('provider-select').value;
  const includeContext = $('context-check').checked;
  const context = includeContext ? pageContextData : '';

  $('btn-launch').disabled = true;
  $('btn-stop').style.display = 'inline-flex';
  $('events-box').innerHTML = '<div style="color: var(--accent);">🚀 Dispatched research swarm...</div>';
  updatePipelineStage('planning');

  try {{
    const res = await api('/research/start', {{
      method: 'POST',
      body: JSON.stringify({{
        query,
        depth,
        backend,
        provider,
        context,
        dry_run: provider === 'mock'
      }})
    }});
    activeRunId = res.run_id;
    pollStatus();
  }} catch (e) {{
    alert('Failed to start swarm: ' + e.message);
    resetButtons();
  }}
}}

async function cancelSwarm() {{
  try {{
    await api('/research/cancel', {{ method: 'POST' }});
  }} catch (e) {{
    console.warn('Cancel error:', e);
  }}
  resetButtons();
}}

function resetButtons() {{
  $('btn-launch').disabled = false;
  $('btn-stop').style.display = 'none';
}}

function pollStatus() {{
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(checkStatus, 800);
}}

async function checkStatus() {{
  try {{
    const st = await api('/research/status' + (activeRunId ? '?run_id=' + encodeURIComponent(activeRunId) : ''));
    if (!st || st.status === 'idle') {{
      if (!activeRunId) resetButtons();
      return;
    }}

    $('m-elapsed').textContent = (st.elapsed || 0) + 's';
    $('m-evidence').textContent = st.evidence_count || 0;
    $('m-searches').textContent = st.searches_count || 0;
    $('m-status').textContent = st.stage || '—';

    // Status pill
    const pill = $('status-pill');
    pill.textContent = st.stage || st.status;
    pill.className = 'pill ' + (st.status === 'running' ? 'running' : (st.status === 'completed' ? 'completed' : 'error'));

    updatePipelineStage(st.stage);

    // Render events stream
    if (st.events && st.events.length) {{
      const box = $('events-box');
      box.innerHTML = '';
      st.events.forEach(ev => {{
        const row = document.createElement('div');
        row.className = 'event-row';
        row.innerHTML = `<span class="event-node">[${{escapeHtml(ev.node.toUpperCase())}}]</span>` +
                        `<span class="event-msg ${{ev.level}}">${{escapeHtml(ev.message)}}</span>`;
        box.appendChild(row);
      }});
      box.scrollTop = box.scrollHeight;
    }}

    // When completed
    if (st.status === 'completed') {{
      clearInterval(pollTimer);
      pollTimer = null;
      resetButtons();
      if (st.report_markdown) {{
        renderReport(st.report_markdown);
      }}
      loadRuns();
    }} else if (st.status === 'failed' || st.status === 'cancelled') {{
      clearInterval(pollTimer);
      pollTimer = null;
      resetButtons();
      if (st.error) {{
        $('events-box').innerHTML += `<div class="event-msg err">Error: ${{escapeHtml(st.error)}}</div>`;
      }}
    }}
  }} catch (e) {{
    console.warn('Status poll error:', e);
  }}
}}

function updatePipelineStage(stage) {{
  const stages = ['st-plan', 'st-res', 'st-syn', 'st-crit', 'st-fin'];
  const stageMap = {{
    'planning': 0,
    'researching': 1,
    'synthesizing': 2,
    'critiquing': 3,
    'verifying': 3,
    'finalizing': 4,
    'done': 5
  }};

  const idx = stageMap[stage] !== undefined ? stageMap[stage] : -1;
  stages.forEach((id, i) => {{
    const el = $(id);
    if (!el) return;
    el.className = 'stage-step';
    if (i < idx) el.classList.add('done');
    else if (i === idx) el.classList.add('active');
  }});
}}

function switchTab(tab) {{
  ['report', 'evidence', 'raw'].forEach(t => {{
    $('tab-' + t).classList.toggle('active', t === tab);
    $('view-' + t).style.display = (t === tab) ? 'block' : 'none';
  }});
}}

// Lightweight markdown-to-HTML parser for reports.
//
// SECURITY (4.0): report markdown comes from the research swarm (LLM output)
// and may carry injected HTML/JS via indirect prompt injection. Escape the
// entire input FIRST, then apply markdown transforms — captures are already
// escaped, so the inserted tags are the only live markup. Link URLs are
// scheme-allowlisted (javascript:/data: URLs become '#').
function parseMarkdown(md) {{
  if (!md) return '';
  let html = escapeHtml(md);
  // Fenced code blocks (content already escaped above)
  html = html.replace(/```([a-z]*)\\n([\\s\\S]*?)```/g, (m, lang, code) => {{
    return `<pre><code>${{code.trim()}}</code></pre>`;
  }});
  // Headings
  html = html.replace(/^### (.*$)/gim, '<h3>$1</h3>');
  html = html.replace(/^## (.*$)/gim, '<h2>$1</h2>');
  html = html.replace(/^# (.*$)/gim, '<h1>$1</h1>');
  // Blockquotes
  html = html.replace(/^\\> (.*$)/gim, '<blockquote>$1</blockquote>');
  // Bold & Italics
  html = html.replace(/\\*\\*(.*?)\\*\\*/g, '<b>$1</b>');
  html = html.replace(/\\*(.*?)\\*/g, '<i>$1</i>');
  // Links — scheme allowlist blocks javascript:/data: URLs
  html = html.replace(/\\[(.*?)\\]\\((.*?)\\)/g, (m, text, url) => {{
    const u = url.trim();
    const safe = /^(https?:|mailto:)/i.test(u) ? u : '#';
    return `<a href="${{safe}}" target="_blank" rel="noopener">${{text}}</a>`;
  }});
  // Lists
  html = html.replace(/^\\s*[-*]\\s+(.*$)/gim, '<li>$1</li>');
  html = html.replace(/(<li>.*<\\/li>)/s, '<ul>$1</ul>');
  // Paragraphs
  html = html.split(/\\n\\s*\\n/).map(p => {{
    p = p.trim();
    if (!p) return '';
    if (p.startsWith('<h') || p.startsWith('<pre') || p.startsWith('<ul') || p.startsWith('<blockquote')) return p;
    return `<p>${{p}}</p>`;
  }}).join('\\n');

  return html;
}}

function renderReport(md) {{
  currentMarkdown = md;
  $('raw-markdown').value = md;
  $('view-report').innerHTML = parseMarkdown(md) || '<div class="empty-hint">Report markdown empty.</div>';
}}

function renderEvidence(cards) {{
  const container = $('view-evidence');
  if (!cards || !cards.length) {{
    container.innerHTML = '<div class="empty-hint">No structured evidence cards recorded for this run.</div>';
    return;
  }}
  container.innerHTML = '<h2 style="color: #fff; margin-bottom: 16px;">📑 Grounded Evidence Cards (' + cards.length + ')</h2>';
  cards.forEach((c, idx) => {{
    const div = document.createElement('div');
    div.style.cssText = 'background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 14px; margin-bottom: 12px;';
    div.innerHTML = `
      <div style="display: flex; justify-content: space-between; margin-bottom: 6px;">
        <b style="color: var(--accent);">[${{idx + 1}}] ${{escapeHtml(c.title || c.url || 'Source')}}</b>
        <span style="font-size: 11px; color: var(--muted);">${{c.score ? 'Score: ' + c.score : ''}}</span>
      </div>
      <p style="font-size: 13px; color: #cbd5e1; margin-bottom: 8px; line-height: 1.5;">${{escapeHtml(c.passage || c.snippet || c.text || '')}}</p>
      ${{c.url ? `<a href="${{escapeHtml(c.url)}}" target="_blank" style="font-size: 12px; color: var(--accent2);">${{escapeHtml(c.url)}}</a>` : ''}}
    `;
    container.appendChild(div);
  }});
}}

function copyReport() {{
  if (!currentMarkdown) {{
    alert('No report to copy.');
    return;
  }}
  navigator.clipboard.writeText(currentMarkdown);
  const btn = $('btn-copy');
  const orig = btn.textContent;
  btn.textContent = '✅ Copied!';
  setTimeout(() => btn.textContent = orig, 1800);
}}

function saveReport() {{
  if (!currentMarkdown) {{
    alert('No report to save.');
    return;
  }}
  const blob = new Blob([currentMarkdown], {{ type: 'text/markdown;charset=utf-8' }});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `deep-research-${{new Date().toISOString().slice(0, 10)}}.md`;
  a.click();
  URL.revokeObjectURL(url);
}}

function escapeHtml(str) {{
  if (!str) return '';
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}}
</script>

</body>
</html>
"""  # nosec B608 -- HTML/JS templating for the research SPA, not SQL
