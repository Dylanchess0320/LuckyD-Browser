"""
Schedule runner — executes one schedule headlessly under unattended guardrails.

Runs serialize against each other and against interactive agent runs through
the shared run lock (core/run_lock.py): a scheduled run, an interactive CLI
run, and an HQ web-server run never mutate shared state (workspace files,
trust store, schedule store) concurrently — across threads and processes.
The runner temporarily swaps the process-global hooks for the unattended set
(UnattendedApprovalHook + AuditHook) and restores them afterwards, so a
scheduler thread can live inside the HQ web server without disturbing the
interactive agent's approvals.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from core.run_lock import run_exclusive


async def _agent_run_inner(sched, session_id: str) -> tuple[str, int]:
    """One headless agent run. Returns (final_text, turns_used)."""
    from core.agent_loop import CodingAgent

    agent = CodingAgent()
    final = await agent.run(sched.prompt, max_turns=sched.max_turns)
    return final, agent.turn_count


async def _run_agent_async(sched, session_id: str) -> tuple[str, int]:
    """Run the agent headlessly, enforcing the schedule's max runtime."""
    return await asyncio.wait_for(
        _agent_run_inner(sched, session_id),
        timeout=sched.max_runtime_sec,
    )


def run_schedule(
    store,
    schedule_id: str,
    *,
    force: bool = False,
    reason: str = "due",
    retry_delay_sec: float = 60.0,
) -> dict[str, Any]:
    """Run a schedule now (with retries). Returns the final run record dict."""
    from core.audit_hook import AuditHook
    from core.hooks import get_hooks, register_plugin, reset_hooks
    from core.unattended import UnattendedApprovalHook

    sched = store.get(schedule_id)
    if sched is None:
        raise ValueError(f"unknown schedule {schedule_id!r}")
    if not sched.enabled and not force:
        raise ValueError(f"schedule '{sched.name}' is disabled")

    # Shared run lock: serializes this scheduled run against other scheduled
    # runs and against interactive agent runs (threads and processes).
    with run_exclusive():
        previous_hooks = get_hooks()
        reset_hooks()
        session_id = f"sched_{sched.id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        register_plugin(
            UnattendedApprovalHook(
                sched.allow_scopes, session_id=session_id, schedule_name=sched.name
            )
        )
        register_plugin(AuditHook(session_id=session_id))
        try:
            return _run_with_retries(store, sched, session_id, reason, retry_delay_sec)
        finally:
            # Restore whatever hooks the host process had.
            from core import hooks as _hooks_mod

            _hooks_mod._hooks = previous_hooks


def _run_with_retries(
    store, sched, session_id: str, reason: str, retry_delay_sec: float
) -> dict[str, Any]:
    from core.scheduler import compute_next_run

    last_record: dict[str, Any] = {}
    attempts = 1 + max(0, sched.max_retries)
    for attempt in range(1, attempts + 1):
        run_id = store.record_run_start(sched.id, sched.name, attempt=attempt)
        started = time.monotonic()
        try:
            final_text, turns = asyncio.run(_run_agent_async(sched, session_id))
            duration = time.monotonic() - started
            summary = (final_text or "").strip()[:400] or "(no output)"
            store.record_run_end(
                run_id, status="ok", summary=summary, turns=turns, duration_sec=duration
            )
            last_record = {
                "run_id": run_id,
                "schedule_id": sched.id,
                "status": "ok",
                "summary": summary,
                "turns": turns,
                "duration_sec": round(duration, 1),
                "attempt": attempt,
            }
            break
        except asyncio.TimeoutError:
            duration = time.monotonic() - started
            store.record_run_end(
                run_id,
                status="timeout",
                duration_sec=duration,
                error=f"exceeded max_runtime_sec={sched.max_runtime_sec}",
                summary=f"Timed out after {sched.max_runtime_sec}s (attempt {attempt}).",
            )
            last_record = {
                "run_id": run_id,
                "schedule_id": sched.id,
                "status": "timeout",
                "attempt": attempt,
                "duration_sec": round(duration, 1),
            }
        except Exception as e:
            duration = time.monotonic() - started
            store.record_run_end(
                run_id,
                status="error",
                duration_sec=duration,
                error=f"{type(e).__name__}: {e}",
                summary=f"Failed: {type(e).__name__} (attempt {attempt}).",
            )
            last_record = {
                "run_id": run_id,
                "schedule_id": sched.id,
                "status": "error",
                "error": f"{type(e).__name__}: {e}",
                "attempt": attempt,
                "duration_sec": round(duration, 1),
            }
        if attempt < attempts:
            time.sleep(retry_delay_sec * attempt)

    # Advance the schedule regardless of outcome (a failing schedule must not
    # spin forever — the user sees the failure in history/digest and can fix it).
    now = datetime.now(timezone.utc)
    sched.last_run_at = now.isoformat(timespec="seconds")
    sched.last_status = last_record.get("status", "error")
    sched.run_count += 1
    try:
        sched.next_run_at = compute_next_run(sched, now).isoformat(timespec="seconds")
    except ValueError:
        sched.enabled = False  # cron can never match again; park it
    store.update(sched)
    last_record["reason"] = reason
    last_record["next_run_at"] = sched.next_run_at
    return last_record
