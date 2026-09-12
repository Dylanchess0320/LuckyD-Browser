# Scheduled Agents — works while you rest

LuckyD can run agents on a timetable, unattended: nightly research briefs,
morning price checks, weekly repo hygiene. You describe the task once; LuckyD
does it while you sleep and leaves a digest for the morning.

## Creating a schedule

Ask the agent, e.g. *"every weekday at 7am, check flight prices for my trip
and summarize the cheapest options"*, or use the **⏰ Schedules** dashboard
in HQ (`/schedules`), or the CLI:

```
luckyd-code schedule --list
luckyd-code schedule --run-now <id>
luckyd-code schedule --daemon     # run the scheduler in the foreground
```

Timing options: `cron` (`0 7 * * 1-5`), `every_minutes` (≥ 5), or `daily_at`
(`07:30` local).

## The unattended permission boundary

A scheduled run has no human to approve things, so it runs under stricter
rules than an interactive session — enforced by `core/unattended.py`, not by
convention:

- **Shell, desktop, and system scopes can never be granted** to a schedule.
  Asking for them is a creation error, and the hook denies them even if they
  somehow end up on the allow-list.
- Each schedule declares its own `allow_scopes` (default: `read, network,
  memory`). Add `browser` for web tasks, `files`/`git` for repo chores.
- Anything outside the allow-list is **denied, never prompted** — the hook
  never touches the approval queue, so a run can't hang waiting for a human.
- Per-run caps: max turns (default 25), max runtime (default 10 min, hard
  kill), retries with backoff (default 1).
- Every tool call is audit-logged under a `sched_<id>_<time>` session, so
  the Trust Center shows exactly what your night-shift agents did.

## Run history & morning digest

- Every attempt is recorded (status, summary, duration, error) — visible in
  `/schedules`, `ScheduleHistory`, or `luckyd-code schedule --history`.
- A failing schedule still advances to its next run instead of spinning;
  failures show up in the digest for you to fix.
- The **morning digest** collects runs since you last checked
  (`ScheduleDigest`, the `☀️` banner in `/schedules`, or
  `/api/schedules/digest`). Reading it marks it read.

## How it runs

- Inside HQ, a background daemon thread ticks every 30s and fires due
  schedules sequentially (one run at a time per process).
- Standalone: `luckyd-code schedule --daemon`, or `python -m core.schedule_daemon`.
- The runner temporarily swaps the process-global hooks for the unattended
  set and restores them afterwards, so scheduled runs can't disturb an
  interactive session's approvals.

Storage: `~/.luckyd/schedules.db` (SQLite; `LUCKYD_DATA_DIR` overrides).
