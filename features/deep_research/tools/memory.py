"""Simple local run memory (JSONL). Vector DB is a later plugin."""

from __future__ import annotations

import json
from pathlib import Path

from ..config import settings


class RunMemory:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(settings.runs_dir) / "memory.jsonl"

    def remember(self, query: str, report_path: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = {"query": query, "report": report_path}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def recent(self, limit: int = 20) -> list[dict]:
        if not self.path.exists():
            return []
        rows = []
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                rows.append(json.loads(line))
        return rows[-limit:]
