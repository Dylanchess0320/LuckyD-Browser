"""
Checkpoint system — file-level snapshots for undo/restore.
Tracks file changes with before/after content and unified diffs.
Borrows patterns from Cline's checkpoint-diff.ts and checkpoint-restore.ts.
"""

from __future__ import annotations

import contextlib
import difflib
import json
from datetime import datetime, timezone
from pathlib import Path

from .types import CheckpointDiff, FileCheckpoint


class CheckpointManager:
    """Manages file checkpoints for undo/restore functionality."""

    def __init__(self, persist_dir: str | Path | None = None):
        self.checkpoints: list[FileCheckpoint] = []
        self._current_index: int = -1
        self._persist_dir = Path(persist_dir) if persist_dir else None
        if self._persist_dir:
            self._persist_dir.mkdir(parents=True, exist_ok=True)

    def snapshot_before(self, file_path: str, tool_call_id: str = "") -> str | None:
        """Capture a file's content BEFORE an edit."""
        path = Path(file_path).expanduser().resolve()
        if path.exists():
            try:
                return path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                return None
        return None

    def record_change(
        self,
        file_path: str,
        content_before: str | None,
        content_after: str | None = None,
        tool_call_id: str = "",
    ) -> FileCheckpoint:
        """Record a file change checkpoint."""
        cp_id = f"cp_{len(self.checkpoints)}_{datetime.now(timezone.utc).strftime('%H%M%S')}"
        checkpoint = FileCheckpoint(
            file_path=str(Path(file_path).expanduser().resolve()),
            content_before=content_before or "",
            content_after=content_after,
            checkpoint_id=cp_id,
            tool_call_id=tool_call_id,
        )
        self.checkpoints.append(checkpoint)
        self._current_index = len(self.checkpoints) - 1
        if self._persist_dir:
            self._persist_checkpoint(checkpoint)
        return checkpoint

    def record_edit(
        self, file_path: str, old_content: str, new_content: str, tool_call_id: str = ""
    ) -> FileCheckpoint:
        """Convenience: record an edit with both before and after."""
        return self.record_change(file_path, old_content, new_content, tool_call_id)

    def undo_last(self) -> CheckpointDiff | None:
        """Undo the most recent checkpoint."""
        if not self.checkpoints:
            return None
        cp = self.checkpoints[-1]
        path = Path(cp.file_path)
        if not path.exists():
            return None
        current = path.read_text(encoding="utf-8", errors="replace")
        if cp.content_before is not None:
            path.write_text(cp.content_before, encoding="utf-8")
        diff = self._compute_diff(cp.file_path, cp.content_before or "", current)
        self.checkpoints.pop()
        self._current_index = len(self.checkpoints) - 1
        return diff

    def undo_to(self, checkpoint_id: str) -> list[CheckpointDiff]:
        """Undo to a specific checkpoint."""
        idx = next(
            (i for i, cp in enumerate(self.checkpoints) if cp.checkpoint_id == checkpoint_id), None
        )
        if idx is None:
            return []
        diffs = []
        while len(self.checkpoints) > idx + 1:
            d = self.undo_last()
            if d:
                diffs.append(d)
        return diffs

    def get_diff(self, checkpoint_id: str | None = None) -> CheckpointDiff | None:
        """Get the diff for a checkpoint (or the last one if None)."""
        if not self.checkpoints:
            return None
        cp = self.checkpoints[-1] if checkpoint_id is None else None
        if checkpoint_id:
            cp = next((c for c in self.checkpoints if c.checkpoint_id == checkpoint_id), None)
        if cp is None:
            return None
        return self._compute_diff(
            cp.file_path,
            cp.content_before,
            cp.content_after or self._read_current(cp.file_path),
        )

    def list_checkpoints(self) -> list[dict]:
        """List all checkpoints with summary info."""
        return [
            {
                "id": cp.checkpoint_id,
                "file": cp.file_path,
                "timestamp": cp.timestamp,
                "tool_call_id": cp.tool_call_id,
                "size_before": len(cp.content_before) if cp.content_before else 0,
                "size_after": len(cp.content_after) if cp.content_after else 0,
            }
            for cp in self.checkpoints
        ]

    def clear(self):
        """Clear all checkpoints."""
        self.checkpoints.clear()
        self._current_index = -1

    @staticmethod
    def _compute_diff(file_path: str, before: str, after: str) -> CheckpointDiff:
        """Compute a unified diff between before and after."""
        before_lines = before.splitlines(keepends=True)
        after_lines = after.splitlines(keepends=True)
        diff_lines = list(
            difflib.unified_diff(
                before_lines,
                after_lines,
                fromfile=f"a/{file_path}",
                tofile=f"b/{file_path}",
                n=3,
            )
        )
        diff_text = "".join(diff_lines)
        additions = sum(
            1 for line in diff_lines if line.startswith("+") and not line.startswith("+++")
        )
        deletions = sum(
            1 for line in diff_lines if line.startswith("-") and not line.startswith("---")
        )
        return CheckpointDiff(
            file_path=file_path, diff_text=diff_text, additions=additions, deletions=deletions
        )

    @staticmethod
    def _read_current(file_path: str) -> str:
        path = Path(file_path)
        if path.exists():
            return path.read_text(encoding="utf-8", errors="replace")
        return ""

    def _persist_checkpoint(self, cp: FileCheckpoint):
        """Write checkpoint to disk for cross-session review."""
        if not self._persist_dir:
            return
        try:
            path = self._persist_dir / f"{cp.checkpoint_id}.json"
            path.write_text(
                json.dumps(
                    {
                        "file_path": cp.file_path,
                        "content_before": cp.content_before,
                        "content_after": cp.content_after,
                        "timestamp": cp.timestamp,
                        "checkpoint_id": cp.checkpoint_id,
                        "tool_call_id": cp.tool_call_id,
                    },
                    indent=2,
                )
            )
            # Keep checkpoints folder tidy: prune oldest files if count exceeds 50
            files = sorted(self._persist_dir.glob("*.json"), key=lambda f: f.stat().st_mtime)
            if len(files) > 50:
                for old_f in files[:-50]:
                    with contextlib.suppress(Exception):
                        old_f.unlink()
        except Exception:
            pass

    def load_persisted(self, file_path: str) -> list[FileCheckpoint]:
        """Load checkpoints from disk."""
        if not self._persist_dir:
            return []
        results = []
        try:
            target = str(Path(file_path).expanduser().resolve())
            for f in sorted(self._persist_dir.glob("*.json")):
                data = json.loads(f.read_text())
                if data.get("file_path") == target:
                    results.append(FileCheckpoint(**data))
        except Exception:
            pass
        return results


# Global singleton
_checkpoint_manager: CheckpointManager | None = None


def get_checkpoint_manager(persist_dir: str | Path | None = None) -> CheckpointManager:
    """Get the global checkpoint manager."""
    global _checkpoint_manager
    if _checkpoint_manager is None:
        store_dir = persist_dir or Path(__file__).resolve().parent.parent / "data" / "checkpoints"
        _checkpoint_manager = CheckpointManager(store_dir)
    return _checkpoint_manager
