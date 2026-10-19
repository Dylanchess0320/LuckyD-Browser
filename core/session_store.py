"""
Session store — persistent conversation sessions (save / resume / continue).

Pattern from goose sessions and `claude --resume/--continue`: every agent run is
persisted under data/sessions/<conversation_id>.json so users can:
  lucky-code --continue          # resume most recent session
  lucky-code --resume <id>       # resume a specific session (prefix match)
  /sessions                      # list sessions in the REPL
  /resume <id>                   # switch session mid-REPL
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import DATA_DIR

SESSIONS_DIR = DATA_DIR / "sessions"
SESSION_FILE_VERSION = 1


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _preview_from_messages(messages: list[dict]) -> str:
    """First meaningful user message, trimmed — used as the session title."""
    for m in messages:
        if m.get("role") == "user":
            content = (m.get("content") or "").strip().replace("\n", " ")
            if content and not content.startswith("You've reached the maximum"):
                return content[:120]
    return "(empty session)"


class SessionStore:
    """File-backed session persistence."""

    def __init__(self, sessions_dir: Path | None = None):
        self.dir = sessions_dir or SESSIONS_DIR
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
        return self.dir / f"{safe}.json"

    # ── write ────────────────────────────────────────────────────────

    def save(
        self,
        conversation_id: str,
        messages: list[dict],
        model: str = "",
        provider: str = "",
        meta: dict[str, Any] | None = None,
    ) -> Path:
        """Persist (create or update) a session. Returns the file path."""
        existing = self.load(conversation_id) or {}
        created_at = existing.get("created_at", _utcnow())
        record = {
            "version": SESSION_FILE_VERSION,
            "conversation_id": conversation_id,
            "created_at": created_at,
            "updated_at": _utcnow(),
            "model": model,
            "provider": provider,
            "preview": _preview_from_messages(messages),
            "message_count": len(messages),
            "messages": messages,
            "meta": meta or existing.get("meta", {}),
        }
        path = self._path(conversation_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
        tmp.replace(path)  # atomic on same volume
        return path

    # ── read ─────────────────────────────────────────────────────────

    def load(self, session_id: str) -> dict | None:
        """Load a session by exact id or unique prefix. None if not found/ambiguous."""
        path = self._path(session_id)
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return None
        # Prefix match (e.g. /resume conv_20250722)
        matches = sorted(self.dir.glob(f"{session_id}*.json"))
        if len(matches) == 1:
            try:
                return json.loads(matches[0].read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    def latest(self) -> dict | None:
        """Most recently updated session."""
        files = sorted(self.dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for f in files:
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
        return None

    def resolve(self, query: str) -> tuple[dict | None, list[dict]]:
        """Resolve a session query by 'latest', 1-based list index, ID/prefix, or preview substring.

        Returns `(session_record, candidates)`:
        - `(record, [])` when uniquely resolved
        - `(None, [summary, ...])` when multiple sessions match `query`
        - `(None, [])` when no session matches
        """
        q = (query or "").strip()
        if not q or q.lower() == "latest":
            rec = self.latest()
            return (rec, [])

        recent = self.list(limit=50)
        # 1-based numeric index into recent sessions (matches `/sessions` numbering)
        if q.isdigit():
            idx = int(q)
            if 1 <= idx <= len(recent):
                cid = recent[idx - 1].get("conversation_id", "")
                rec = self.load(cid) if cid else None
                if rec:
                    return (rec, [])

        # Exact file match
        exact_path = self._path(q)
        if exact_path.exists():
            try:
                return (json.loads(exact_path.read_text(encoding="utf-8")), [])
            except Exception:
                return (None, [])

        # Prefix match on conversation_id
        q_low = q.lower()
        id_matches = [
            s for s in recent if str(s.get("conversation_id", "")).lower().startswith(q_low)
        ]
        if len(id_matches) == 1:
            rec = self.load(id_matches[0]["conversation_id"])
            return (rec, [])
        if len(id_matches) > 1:
            return (None, id_matches[:10])

        # Substring match on preview text
        preview_matches = [s for s in recent if q_low in str(s.get("preview", "")).lower()]
        if len(preview_matches) == 1:
            rec = self.load(preview_matches[0]["conversation_id"])
            return (rec, [])
        if len(preview_matches) > 1:
            return (None, preview_matches[:10])

        return (None, [])

    def list(self, limit: int = 15) -> list[dict]:
        """Session summaries, newest first."""
        files = sorted(self.dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        out = []
        for f in files[:limit]:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                out.append(
                    {
                        "conversation_id": data.get("conversation_id", f.stem),
                        "preview": data.get("preview", ""),
                        "updated_at": data.get("updated_at", ""),
                        "model": data.get("model", ""),
                        "provider": data.get("provider", ""),
                        "message_count": data.get("message_count", 0),
                    }
                )
            except Exception:
                continue
        return out

    def delete(self, session_id: str) -> bool:
        record = self.load(session_id)
        if not record:
            return False
        self._path(record.get("conversation_id", session_id)).unlink(missing_ok=True)
        return True


# ── Singleton ─────────────────────────────────────────────────────────

_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    global _store
    if _store is None:
        _store = SessionStore()
    return _store
