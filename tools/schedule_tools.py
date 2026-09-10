"""
Scheduled-agent tools — create and manage background schedules conversationally.

Creating/enabling/deleting schedules and running one immediately require
approval (they start unattended work). Listing, history, and the morning
digest are read-only.
"""

from __future__ import annotations

import json
from typing import Any

from core.scheduler import (
    Schedule,
    ScheduleStore,
    build_schedule,
    compute_next_run,
    validate_schedule_fields,
)

from .base import ToolBase, ToolOutput
from .registry import register_tool

_store: ScheduleStore | None = None


def get_store() -> ScheduleStore:
    global _store
    if _store is None:
        _store = ScheduleStore()
    return _store


def _fmt_schedule(s: Schedule) -> str:
    nxt = s.next_run_at or "—"
    return (
        f"• {s.name} [{s.id}] {'✅' if s.enabled else '⏸️ disabled'} — "
        f"{s.kind}"
        + (f" '{s.cron}'" if s.kind == "cron" else "")
        + (f" every {s.every_minutes}m" if s.kind == "every" else "")
        + (f" at {s.daily_at}" if s.kind == "daily" else "")
        + f" — next: {nxt} — scopes: {','.join(s.allow_scopes)}"
        + (f" — last: {s.last_status}" if s.last_status else "")
    )


def _build_schedule(**kwargs) -> Schedule:
    kind = kwargs.pop("kind")
    return build_schedule(kind=kind, **kwargs)


class ScheduleCreateTool(ToolBase):
    name = "ScheduleCreate"
    description = (
        "Create a background schedule that runs the agent unattended on a timetable. "
        "Give it a name, the task prompt, and WHEN: cron ('0 7 * * *'), every_minutes "
        "(>=5), or daily_at ('07:30'). Unattended runs can NEVER use shell/desktop/system "
        "scopes; default scopes are read,network,memory (add 'browser' etc. as needed)."
    )
    aliases = ["CreateSchedule"]
    permission_level = "REQUIRES_APPROVAL"
    parameters = {
        "name": {"type": "string", "description": "Short name, e.g. 'morning flight check'."},
        "prompt": {"type": "string", "description": "The task the agent should do each run."},
        "cron": {"type": "string", "description": "5-field cron, e.g. '0 7 * * *' for 7am daily."},
        "every_minutes": {"type": "integer", "description": "Run every N minutes (>= 5)."},
        "daily_at": {"type": "string", "description": "'HH:MM' 24h local time, run daily."},
        "allow_scopes": {
            "type": "string",
            "description": 'JSON list of scopes, e.g. \'["read","network","browser"]\'.',
        },
        "max_turns": {
            "type": "integer",
            "description": "Max agent turns per run (1-100, default 25).",
        },
        "max_runtime_minutes": {
            "type": "integer",
            "description": "Kill the run after N minutes (default 10).",
        },
        "max_retries": {"type": "integer", "description": "Retries on failure (0-5, default 1)."},
    }

    async def execute(
        self,
        name: str,
        prompt: str,
        cron: str = "",
        every_minutes: int = 0,
        daily_at: str = "",
        allow_scopes: str = "",
        max_turns: int = 25,
        max_runtime_minutes: int = 10,
        max_retries: int = 1,
    ) -> ToolOutput:
        kinds = [bool(cron), bool(every_minutes), bool(daily_at)]
        if sum(kinds) != 1:
            return ToolOutput(
                text="Error: give exactly one of cron, every_minutes, daily_at.", error=True
            )
        kind = "cron" if cron else ("every" if every_minutes else "daily")
        try:
            scopes = json.loads(allow_scopes) if allow_scopes else None
            sched = _build_schedule(
                kind=kind,
                name=name,
                prompt=prompt,
                cron=cron,
                every_minutes=every_minutes,
                daily_at=daily_at,
                allow_scopes=scopes,
                max_turns=max_turns,
                max_runtime_sec=max_runtime_minutes * 60,
                max_retries=max_retries,
            )
        except (ValueError, json.JSONDecodeError) as e:
            return ToolOutput(text=f"Error: {e}", error=True)
        get_store().create(sched)
        return ToolOutput(
            text=f"Schedule created:\n{_fmt_schedule(sched)}\n\nIt will run unattended with scopes: "
            f"{', '.join(sched.allow_scopes)}. Shell/desktop/system are never allowed overnight.",
            title="Schedule created",
            metadata={"schedule": sched.to_dict()},
        )


class ScheduleListTool(ToolBase):
    name = "ScheduleList"
    description = "List all background schedules with their next run time and status."
    aliases = ["ListSchedules"]
    permission_level = "ALWAYS_ALLOW"
    parameters = {}

    async def execute(self) -> ToolOutput:
        schedules = get_store().list()
        if not schedules:
            return ToolOutput(text="No schedules yet. Create one with ScheduleCreate.")
        return ToolOutput(text="Schedules:\n" + "\n".join(_fmt_schedule(s) for s in schedules))


class ScheduleGetTool(ToolBase):
    name = "ScheduleGet"
    description = "Show full details of one schedule by id."
    permission_level = "ALWAYS_ALLOW"
    parameters = {"id": {"type": "string", "description": "Schedule id."}}

    async def execute(self, id: str) -> ToolOutput:
        s = get_store().get(id)
        if not s:
            return ToolOutput(text=f"Error: unknown schedule {id!r}.", error=True)
        return ToolOutput(
            text=_fmt_schedule(s) + f"\nTask: {s.prompt}", metadata={"schedule": s.to_dict()}
        )


class ScheduleUpdateTool(ToolBase):
    name = "ScheduleUpdate"
    description = (
        "Update a schedule: name, prompt, timing (cron/every_minutes/daily_at), "
        "allow_scopes (JSON list), max_turns, max_runtime_minutes, max_retries. "
        "Only pass the fields you want to change."
    )
    permission_level = "REQUIRES_APPROVAL"
    parameters = {
        "id": {"type": "string", "description": "Schedule id."},
        "name": {"type": "string"},
        "prompt": {"type": "string"},
        "cron": {"type": "string"},
        "every_minutes": {"type": "integer"},
        "daily_at": {"type": "string"},
        "allow_scopes": {"type": "string"},
        "max_turns": {"type": "integer"},
        "max_runtime_minutes": {"type": "integer"},
        "max_retries": {"type": "integer"},
    }

    async def execute(self, id: str, **kwargs: Any) -> ToolOutput:
        store = get_store()
        s = store.get(id)
        if not s:
            return ToolOutput(text=f"Error: unknown schedule {id!r}.", error=True)
        changes = {
            k: v for k, v in kwargs.items() if v not in ("", 0, None) or k in ("cron", "daily_at")
        }
        # Timing: exactly one timing field may be set per update.
        timing = {k: changes[k] for k in ("cron", "every_minutes", "daily_at") if changes.get(k)}
        try:
            if "name" in changes:
                s.name = changes["name"]
            if "prompt" in changes:
                s.prompt = changes["prompt"]
            if timing:
                s.kind = (
                    "cron"
                    if timing.get("cron")
                    else ("every" if timing.get("every_minutes") else "daily")
                )
                s.cron = timing.get("cron", "")
                s.every_minutes = timing.get("every_minutes", 0)
                s.daily_at = timing.get("daily_at", "")
            scopes = (
                json.loads(changes["allow_scopes"])
                if changes.get("allow_scopes")
                else s.allow_scopes
            )
            s.allow_scopes = validate_schedule_fields(
                name=s.name,
                prompt=s.prompt,
                kind=s.kind,
                cron=s.cron,
                every_minutes=s.every_minutes,
                daily_at=s.daily_at,
                allow_scopes=scopes,
                max_turns=changes.get("max_turns", s.max_turns),
                max_runtime_sec=changes.get("max_runtime_minutes", s.max_runtime_sec // 60) * 60,
                max_retries=changes.get("max_retries", s.max_retries),
            )
            if "max_turns" in changes:
                s.max_turns = changes["max_turns"]
            if "max_runtime_minutes" in changes:
                s.max_runtime_sec = changes["max_runtime_minutes"] * 60
            if "max_retries" in changes:
                s.max_retries = changes["max_retries"]
            if timing:
                s.next_run_at = compute_next_run(s).isoformat(timespec="seconds")
        except (ValueError, json.JSONDecodeError) as e:
            return ToolOutput(text=f"Error: {e}", error=True)
        store.update(s)
        return ToolOutput(text=f"Updated:\n{_fmt_schedule(s)}", metadata={"schedule": s.to_dict()})


class ScheduleDeleteTool(ToolBase):
    name = "ScheduleDelete"
    description = "Delete a schedule and its run history."
    permission_level = "REQUIRES_APPROVAL"
    parameters = {"id": {"type": "string", "description": "Schedule id."}}

    async def execute(self, id: str) -> ToolOutput:
        if get_store().delete(id):
            return ToolOutput(text=f"Schedule {id} deleted.")
        return ToolOutput(text=f"Error: unknown schedule {id!r}.", error=True)


class ScheduleEnableTool(ToolBase):
    name = "ScheduleEnable"
    description = "Enable a paused schedule."
    permission_level = "REQUIRES_APPROVAL"
    parameters = {"id": {"type": "string", "description": "Schedule id."}}

    async def execute(self, id: str) -> ToolOutput:
        store = get_store()
        s = store.get(id)
        if not s:
            return ToolOutput(text=f"Error: unknown schedule {id!r}.", error=True)
        s.enabled = True
        if not s.next_run_at:
            s.next_run_at = compute_next_run(s).isoformat(timespec="seconds")
        store.update(s)
        return ToolOutput(text=f"Enabled:\n{_fmt_schedule(s)}")


class ScheduleDisableTool(ToolBase):
    name = "ScheduleDisable"
    description = "Pause a schedule (keeps it and its history)."
    permission_level = "NORMAL"
    parameters = {"id": {"type": "string", "description": "Schedule id."}}

    async def execute(self, id: str) -> ToolOutput:
        store = get_store()
        s = store.get(id)
        if not s:
            return ToolOutput(text=f"Error: unknown schedule {id!r}.", error=True)
        s.enabled = False
        store.update(s)
        return ToolOutput(text=f"Paused '{s.name}'. Re-enable with ScheduleEnable.")


class ScheduleRunNowTool(ToolBase):
    name = "ScheduleRunNow"
    description = "Run a schedule immediately (one-off), with its normal unattended guardrails."
    permission_level = "REQUIRES_APPROVAL"
    parameters = {"id": {"type": "string", "description": "Schedule id."}}

    async def execute(self, id: str) -> ToolOutput:
        from core.schedule_runner import run_schedule

        try:
            record = run_schedule(get_store(), id, force=True, reason="manual", retry_delay_sec=0)
        except ValueError as e:
            return ToolOutput(text=f"Error: {e}", error=True)
        status = record.get("status")
        text = (
            f"Run {record['run_id']} finished: {status}.\n"
            f"{record.get('summary', '')}\n"
            f"Next run: {record.get('next_run_at')}"
        )
        return ToolOutput(text=text, metadata={"run": record}, error=status not in ("ok",))


class ScheduleHistoryTool(ToolBase):
    name = "ScheduleHistory"
    description = "Show recent runs (status, summary, duration). Optionally filter by schedule id."
    permission_level = "ALWAYS_ALLOW"
    parameters = {
        "id": {"type": "string", "description": "Schedule id (optional)."},
        "limit": {"type": "integer", "description": "Max runs to show (default 10)."},
    }

    async def execute(self, id: str = "", limit: int = 10) -> ToolOutput:
        runs = get_store().history(id or None, min(max(limit, 1), 50))
        if not runs:
            return ToolOutput(text="No runs recorded yet.")
        lines = []
        for r in runs:
            lines.append(
                f"• {r['schedule_name']} [{r['started_at']}] {r['status']} "
                f"({r['duration_sec']:.0f}s, attempt {r['attempt']}): "
                f"{(r['summary'] or r['error'])[:160]}"
            )
        return ToolOutput(text="Recent runs:\n" + "\n".join(lines), metadata={"runs": runs})


class ScheduleDigestTool(ToolBase):
    name = "ScheduleDigest"
    description = (
        "Read your morning digest: what scheduled agents did since you last "
        "checked. Reading it marks it as read."
    )
    aliases = ["MorningDigest"]
    permission_level = "ALWAYS_ALLOW"
    parameters = {}

    async def execute(self) -> ToolOutput:
        store = get_store()
        runs = store.digest()
        store.mark_digest_seen()
        if not runs:
            return ToolOutput(text="Nothing new from your scheduled agents. ☕")
        ok = sum(1 for r in runs if r["status"] == "ok")
        bad = [r for r in runs if r["status"] != "ok"]
        lines = [
            f"Overnight digest: {len(runs)} run(s), {ok} ok"
            + (f", {len(bad)} need attention." if bad else ".")
        ]
        for r in runs:
            mark = "✅" if r["status"] == "ok" else "⚠️"
            lines.append(
                f"{mark} {r['schedule_name']} — {r['status']}: {(r['summary'] or r['error'])[:200]}"
            )
        return ToolOutput(text="\n".join(lines), metadata={"runs": runs})


for _t in (
    ScheduleCreateTool(),
    ScheduleListTool(),
    ScheduleGetTool(),
    ScheduleUpdateTool(),
    ScheduleDeleteTool(),
    ScheduleEnableTool(),
    ScheduleDisableTool(),
    ScheduleRunNowTool(),
    ScheduleHistoryTool(),
    ScheduleDigestTool(),
):
    register_tool(_t)
