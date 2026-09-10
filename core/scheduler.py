"""
Scheduled local agents — "works while you rest".

Persistent schedules (cron / every-N-minutes / daily-at) that run the agent
headlessly in the background, with run history, retries, disable controls,
and a morning digest. Unattended runs execute under a strict permission
boundary (see core/unattended.py): shell, desktop, and system scopes can
never be granted to a schedule.

Storage: SQLite at ~/.luckyd/schedules.db (LUCKYD_DATA_DIR overrides).
"""

from __future__ import annotations

import re
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ── cron parsing (vendored, stdlib only) ──────────────────────────────────

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_DOWS = {
    "sun": 0,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
}


def _parse_field(text: str, lo: int, hi: int, names: dict[str, int] | None = None) -> set[int]:
    """Parse one cron field into a set of ints. Supports *, */n, a-b, a-b/n, a,b,c, names."""
    result: set[int] = set()

    def val(tok: str) -> int:
        tok = tok.strip().lower()
        if names and tok in names:
            return names[tok]
        v = int(tok)
        if not (lo <= v <= hi):
            raise ValueError(f"cron value {v} out of range [{lo}-{hi}]")
        return v

    for part in text.split(","):
        part = part.strip()
        if not part:
            raise ValueError("empty cron field part")
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            step = int(step_s)
            if step < 1:
                raise ValueError("cron step must be >= 1")
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = val(a), val(b)
            if start > end:
                raise ValueError(f"cron range {a}-{b} is backwards")
        elif part == "":
            raise ValueError("missing range before /step")
        else:
            start = end = val(part)
        result.update(range(start, end + 1, step))
    if not result:
        raise ValueError("cron field matched nothing")
    return result


@dataclass
class CronSpec:
    minute: set[int]
    hour: set[int]
    dom: set[int]
    month: set[int]
    dow: set[int]
    dom_star: bool = False
    dow_star: bool = False

    @classmethod
    def parse(cls, expr: str) -> CronSpec:
        parts = expr.split()
        if len(parts) != 5:
            raise ValueError(f"cron expression needs 5 fields, got {len(parts)}: {expr!r}")
        m, h, dom, mon, dow = parts
        return cls(
            minute=_parse_field(m, 0, 59),
            hour=_parse_field(h, 0, 23),
            dom=_parse_field(dom, 1, 31),
            month=_parse_field(mon, 1, 12, _MONTHS),
            dow=_parse_field(dow, 0, 6, _DOWS),
            dom_star=dom.strip() == "*",
            dow_star=dow.strip() == "*",
        )

    def matches(self, dt: datetime) -> bool:
        # Standard cron: dom and dow are OR-ed when both are restricted.
        py_dow = (dt.weekday() + 1) % 7  # Monday=0 -> Sunday=0
        if not (dt.minute in self.minute and dt.hour in self.hour and dt.month in self.month):
            return False
        dom_ok = dt.day in self.dom
        dow_ok = py_dow in self.dow
        if self.dom_star and self.dow_star:
            return True
        if self.dom_star:
            return dow_ok
        if self.dow_star:
            return dom_ok
        return dom_ok or dow_ok


def cron_next(expr: str, after: datetime) -> datetime:
    """Next datetime strictly after `after` matching the cron expression (minute precision)."""
    spec = CronSpec.parse(expr)
    candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = after + timedelta(days=366)
    while candidate <= limit:
        if spec.matches(candidate):
            return candidate
        candidate += timedelta(minutes=1)
    raise ValueError(f"no cron match within a year for {expr!r}")


# ── schedule model ────────────────────────────────────────────────────────

VALID_SCOPES = {
    "read",
    "network",
    "browser",
    "files",
    "shell",
    "desktop",
    "git",
    "memory",
    "agents",
    "MCP",
    "system",
}
# Scopes that can NEVER be granted to an unattended schedule.
HARD_DENY_SCOPES = {"shell", "desktop", "system"}
DEFAULT_ALLOW_SCOPES = ["read", "network", "memory"]
MIN_INTERVAL_MINUTES = 5


@dataclass
class Schedule:
    id: str
    name: str
    prompt: str
    kind: str  # "cron" | "every" | "daily"
    cron: str = ""
    every_minutes: int = 0
    daily_at: str = ""  # "HH:MM"
    timezone: str = "local"
    enabled: bool = True
    allow_scopes: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOW_SCOPES))
    max_turns: int = 25
    max_runtime_sec: int = 600
    max_retries: int = 1
    created_at: str = ""
    updated_at: str = ""
    last_run_at: str = ""
    last_status: str = ""
    next_run_at: str = ""
    run_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["allow_scopes"] = list(self.allow_scopes)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Schedule:
        return cls(**{**d, "allow_scopes": list(d.get("allow_scopes") or [])})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_schedule_fields(
    *,
    name: str,
    prompt: str,
    kind: str,
    cron: str = "",
    every_minutes: int = 0,
    daily_at: str = "",
    allow_scopes: list[str] | None = None,
    max_turns: int = 25,
    max_runtime_sec: int = 600,
    max_retries: int = 1,
) -> list[str]:
    """Return a cleaned allow_scopes list, or raise ValueError with a human message."""
    if not name.strip():
        raise ValueError("schedule name is required")
    if not prompt.strip():
        raise ValueError("schedule prompt (the task) is required")
    if kind == "cron":
        CronSpec.parse(cron)  # raises with a clear message
    elif kind == "every":
        if every_minutes < MIN_INTERVAL_MINUTES:
            raise ValueError(f"interval must be at least {MIN_INTERVAL_MINUTES} minutes")
    elif kind == "daily":
        if not re.fullmatch(r"\d{1,2}:\d{2}", daily_at.strip()):
            raise ValueError("daily_at must look like HH:MM (24h)")
        h, m = (int(x) for x in daily_at.split(":"))
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError("daily_at must be a valid 24h time")
    else:
        raise ValueError(f"unknown schedule kind {kind!r} (cron | every | daily)")

    scopes = list(allow_scopes) if allow_scopes else list(DEFAULT_ALLOW_SCOPES)
    unknown = [s for s in scopes if s not in VALID_SCOPES]
    if unknown:
        raise ValueError(f"unknown permission scopes: {', '.join(unknown)}")
    denied = [s for s in scopes if s in HARD_DENY_SCOPES]
    if denied:
        raise ValueError(
            f"scopes {', '.join(denied)} can never be granted to an unattended "
            "schedule (they need a human present). Remove them."
        )
    if not (1 <= max_turns <= 100):
        raise ValueError("max_turns must be 1-100")
    if not (60 <= max_runtime_sec <= 7200):
        raise ValueError("max_runtime_sec must be 60-7200")
    if not (0 <= max_retries <= 5):
        raise ValueError("max_retries must be 0-5")
    return scopes


def compute_next_run(sched: Schedule, after: datetime | None = None) -> datetime:
    after = after or datetime.now(timezone.utc)
    if sched.kind == "cron":
        return cron_next(sched.cron, after)
    if sched.kind == "every":
        return after + timedelta(minutes=sched.every_minutes)
    # daily: next local HH:MM
    h, m = (int(x) for x in sched.daily_at.split(":"))
    local = after.astimezone()
    candidate = local.replace(hour=h, minute=m, second=0, microsecond=0)
    if candidate <= after:
        candidate += timedelta(days=1)
    return candidate


# ── SQLite store ──────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, prompt TEXT NOT NULL,
    kind TEXT NOT NULL, cron TEXT DEFAULT '', every_minutes INTEGER DEFAULT 0,
    daily_at TEXT DEFAULT '', timezone TEXT DEFAULT 'local',
    enabled INTEGER DEFAULT 1, allow_scopes TEXT DEFAULT '[]',
    max_turns INTEGER DEFAULT 25, max_runtime_sec INTEGER DEFAULT 600,
    max_retries INTEGER DEFAULT 1,
    created_at TEXT, updated_at TEXT, last_run_at TEXT DEFAULT '',
    last_status TEXT DEFAULT '', next_run_at TEXT DEFAULT '', run_count INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL, schedule_name TEXT DEFAULT '',
    started_at TEXT, finished_at TEXT, status TEXT DEFAULT 'running',
    summary TEXT DEFAULT '', turns INTEGER DEFAULT 0,
    duration_sec REAL DEFAULT 0, attempt INTEGER DEFAULT 1,
    error TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_runs_sched ON runs(schedule_id, started_at DESC);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


class ScheduleStore:
    """Thread-safe SQLite store for schedules, run history, and digest state."""

    def __init__(self, path: Path | None = None):
        from core.trust import _data_dir

        self.path = Path(path) if path else _data_dir() / "schedules.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        return conn

    # ── schedules ──
    def create(self, sched: Schedule) -> Schedule:
        import json as _json

        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO schedules (id,name,prompt,kind,cron,every_minutes,daily_at,
                   timezone,enabled,allow_scopes,max_turns,max_runtime_sec,max_retries,
                   created_at,updated_at,last_run_at,last_status,next_run_at,run_count)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    sched.id,
                    sched.name,
                    sched.prompt,
                    sched.kind,
                    sched.cron,
                    sched.every_minutes,
                    sched.daily_at,
                    sched.timezone,
                    int(sched.enabled),
                    _json.dumps(sched.allow_scopes),
                    sched.max_turns,
                    sched.max_runtime_sec,
                    sched.max_retries,
                    sched.created_at,
                    sched.updated_at,
                    sched.last_run_at,
                    sched.last_status,
                    sched.next_run_at,
                    sched.run_count,
                ),
            )
        return sched

    def get(self, schedule_id: str) -> Schedule | None:
        import json as _json

        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["enabled"] = bool(d["enabled"])
        d["allow_scopes"] = _json.loads(d["allow_scopes"] or "[]")
        return Schedule.from_dict(d)

    def list(self, enabled_only: bool = False) -> list[Schedule]:
        import json as _json

        with self._lock, self._connect() as conn:
            q = (
                "SELECT * FROM schedules"
                + (" WHERE enabled=1" if enabled_only else "")
                + " ORDER BY name"
            )
            rows = conn.execute(q).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["enabled"] = bool(d["enabled"])
            d["allow_scopes"] = _json.loads(d["allow_scopes"] or "[]")
            out.append(Schedule.from_dict(d))
        return out

    def update(self, sched: Schedule) -> None:
        import json as _json

        sched.updated_at = _now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                """UPDATE schedules SET name=?,prompt=?,kind=?,cron=?,every_minutes=?,
                   daily_at=?,timezone=?,enabled=?,allow_scopes=?,max_turns=?,
                   max_runtime_sec=?,max_retries=?,updated_at=?,last_run_at=?,
                   last_status=?,next_run_at=?,run_count=? WHERE id=?""",
                (
                    sched.name,
                    sched.prompt,
                    sched.kind,
                    sched.cron,
                    sched.every_minutes,
                    sched.daily_at,
                    sched.timezone,
                    int(sched.enabled),
                    _json.dumps(sched.allow_scopes),
                    sched.max_turns,
                    sched.max_runtime_sec,
                    sched.max_retries,
                    sched.updated_at,
                    sched.last_run_at,
                    sched.last_status,
                    sched.next_run_at,
                    sched.run_count,
                    sched.id,
                ),
            )

    def delete(self, schedule_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
            conn.execute("DELETE FROM runs WHERE schedule_id=?", (schedule_id,))
            return cur.rowcount > 0

    def due(self, now: datetime | None = None) -> list[Schedule]:
        now = now or datetime.now(timezone.utc)
        return [
            s
            for s in self.list(enabled_only=True)
            if s.next_run_at and datetime.fromisoformat(s.next_run_at) <= now
        ]

    # ── runs ──
    def record_run_start(self, schedule_id: str, schedule_name: str, attempt: int = 1) -> str:
        run_id = uuid.uuid4().hex[:12]
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO runs (id,schedule_id,schedule_name,started_at,status,attempt)
                   VALUES (?,?,?,?, 'running', ?)""",
                (run_id, schedule_id, schedule_name, _now_iso(), attempt),
            )
        return run_id

    def record_run_end(
        self,
        run_id: str,
        *,
        status: str,
        summary: str = "",
        turns: int = 0,
        duration_sec: float = 0.0,
        error: str = "",
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """UPDATE runs SET finished_at=?,status=?,summary=?,turns=?,
                   duration_sec=?,error=? WHERE id=?""",
                (_now_iso(), status, summary[:2000], turns, duration_sec, error[:1000], run_id),
            )

    def history(self, schedule_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            if schedule_id:
                rows = conn.execute(
                    "SELECT * FROM runs WHERE schedule_id=? ORDER BY started_at DESC LIMIT ?",
                    (schedule_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
                ).fetchall()
        return [dict(r) for r in rows]

    # ── digest ──
    def digest(self, since_iso: str | None = None) -> list[dict[str, Any]]:
        """Runs finished since `since_iso` (or since the digest watermark)."""
        watermark = since_iso or self.get_meta("digest_seen") or "1970-01-01T00:00:00+00:00"
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM runs WHERE status != 'running'
                   AND finished_at > ? ORDER BY finished_at DESC""",
                (watermark,),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_digest_seen(self) -> None:
        self.set_meta("digest_seen", _now_iso())

    def get_meta(self, key: str) -> str | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO meta (key,value) VALUES (?,?)", (key, value))


def new_schedule_id() -> str:
    return uuid.uuid4().hex[:8]


def build_schedule(
    *,
    name: str,
    prompt: str,
    kind: str,
    cron: str = "",
    every_minutes: int = 0,
    daily_at: str = "",
    allow_scopes: list[str] | None = None,
    max_turns: int = 25,
    max_runtime_sec: int = 600,
    max_retries: int = 1,
) -> Schedule:
    """Validate fields and return an unsaved Schedule with next_run_at computed."""
    scopes = validate_schedule_fields(
        name=name,
        prompt=prompt,
        kind=kind,
        cron=cron,
        every_minutes=every_minutes,
        daily_at=daily_at,
        allow_scopes=allow_scopes,
        max_turns=max_turns,
        max_runtime_sec=max_runtime_sec,
        max_retries=max_retries,
    )
    now = _now_iso()
    sched = Schedule(
        id=new_schedule_id(),
        name=name.strip(),
        prompt=prompt.strip(),
        kind=kind,
        cron=cron,
        every_minutes=every_minutes,
        daily_at=daily_at.strip(),
        allow_scopes=scopes,
        max_turns=max_turns,
        max_runtime_sec=max_runtime_sec,
        max_retries=max_retries,
        created_at=now,
        updated_at=now,
    )
    sched.next_run_at = compute_next_run(sched).isoformat(timespec="seconds")
    return sched
