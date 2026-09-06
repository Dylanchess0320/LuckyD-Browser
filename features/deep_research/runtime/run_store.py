"""Persists every run's full artifacts to data/deep_research/runs/<timestamp>/."""

from __future__ import annotations

import contextlib
import json
from datetime import datetime, timezone
from pathlib import Path

from ..config import settings
from ..schemas import CitationAudit, Critique, EvidenceCard, ResearchPlan, ResearchReport
from .events import EventEmitter, RunEvent


class RunStore:
    def __init__(
        self,
        query: str,
        emitter: EventEmitter | None = None,
        runs_dir: str | None = None,
    ) -> None:
        self.ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")[:18]
        base = Path(runs_dir or settings.runs_dir)
        self.dir = base / self.ts
        self.dir.mkdir(parents=True, exist_ok=True)
        self.emitter = emitter or EventEmitter()
        self.log_path = self.dir / "events.log"
        self._log = self.log_path.open("a", encoding="utf-8")

    def emit(self, node: str, message: str, level: str = "info") -> None:
        ev = RunEvent(node=node, message=message, level=level)  # type: ignore[arg-type]
        line = f"[{ev.ts:.0f}] {level.upper():4} {node}: {message}\n"
        self._log.write(line)
        self._log.flush()
        self.emitter.emit(ev)

    def save_json(self, name: str, obj) -> Path:
        p = self.dir / name
        if isinstance(obj, list):
            data = [x.model_dump(mode="json") if hasattr(x, "model_dump") else x for x in obj]
            p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        elif hasattr(obj, "model_dump_json"):
            p.write_text(obj.model_dump_json(indent=2), encoding="utf-8")
        else:
            p.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
        return p

    def save_text(self, name: str, text: str) -> Path:
        p = self.dir / name
        p.write_text(text, encoding="utf-8")
        return p

    def save_plan(self, plan: ResearchPlan) -> None:
        self.save_json("plan.json", plan)

    def save_evidence(self, evidence: list[EvidenceCard]) -> None:
        self.save_json("evidence.json", evidence)

    def save_report(self, report: ResearchReport, markdown: str) -> None:
        self.save_json("report.json", report)
        self.save_text("report.md", markdown)

    def save_critique(self, critique: Critique, iteration: int) -> None:
        self.save_json(f"critique_iter{iteration}.json", critique)

    def save_citation_audit(self, audit: CitationAudit) -> None:
        self.save_json("citation_audit.json", audit)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._log.close()
