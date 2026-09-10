#!/usr/bin/env python3
"""
LuckyD Code — Harness HQ web server (live source).

Live-source replacement for the frozen ``luckyd-code.exe`` harness backend.
It exposes the same HTTP surface the browser expects (``/health``,
``/api/tools``, ``/api/brain/*``, ``/api/orchestrate``, background tasks, …)
but every LLM call runs through the FIXED ``core/llm_client.py`` — so an empty
or whitespace-only API key can no longer produce the
``Illegal header value b'Bearer '`` crash that the stale exe raised.

Run it exactly like the old exe:

    python web_server.py --web --port 8000 --host 127.0.0.1

The browser's ``harness_bridge._find_exe()`` prefers this launcher, so the HQ
tab / Harness mode automatically use the live backend instead of the stale exe.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hmac
import json
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from config import PROJECT_DIR, get_config, load_env
from core.approval_hook import ApprovalHook
from core.audit_hook import AuditHook
from core.hooks import get_hooks, register_plugin
from core.trust import SCOPES, get_audit_log, get_policy, scope_of
from core.types import HookContext
from memory.store import get_memory
from tools.registry import registry

# The ApprovalHook instance wired in main() — used by the Trust dashboard's
# pending-approval endpoints. Resolved lazily so import order never matters.
_APPROVAL_HOOK: ApprovalHook | None = None


def _approval_hook() -> ApprovalHook | None:
    if _APPROVAL_HOOK is not None:
        return _APPROVAL_HOOK
    for hook in get_hooks().before_tool:
        if isinstance(hook, ApprovalHook):
            return hook
    return None


load_env()  # load .env before any agent/LLM construction

# ── Auth token (default-on) ──────────────────────────────────────────────
# Anyone who can reach this port and knows the token can run the full agent
# (Bash/PowerShell/Write/Git). Previously this server had NO auth at all and
# sent Access-Control-Allow-Origin: * on every response, so any webpage open
# in any browser on the machine could drive it. A random per-install token is
# generated once and persisted under .luckyd-code/ (already gitignored).
_TOKEN_PATH = PROJECT_DIR / ".luckyd-code" / "hq_token"

# Name of the HttpOnly session cookie the browser sets on its own profile so
# the in-browser HQ tab authenticates without a JS-visible token (4.0).
HQ_COOKIE = "luckyd_hq"


def _load_or_create_token() -> str:
    try:
        if _TOKEN_PATH.exists():
            tok = _TOKEN_PATH.read_text(encoding="utf-8").strip()
            if tok:
                return tok
        _TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        tok = secrets.token_urlsafe(32)
        _TOKEN_PATH.write_text(tok, encoding="utf-8")
        return tok
    except OSError:
        # Can't persist (read-only fs, etc.) — fall back to a per-run token.
        # Sessions won't survive a restart, but the server is never unauthenticated.
        return secrets.token_urlsafe(32)


_TOKEN = _load_or_create_token()
_ALLOWED_ORIGINS = None  # set once the bound port is known, in main()

# Dedicated event loop in its own thread for all async agent/tool work.
_LOOP = asyncio.new_event_loop()
_LOOP_THREAD = threading.Thread(target=_LOOP.run_forever, name="hq-loop", daemon=True)
_LOOP_THREAD.start()

# One shared agent (lazy) + locks — CodingAgent is stateful (message history).
_AGENT = None
_AGENT_LOCK = threading.Lock()  # guards creation
_run_lock = asyncio.Lock()  # created below on the loop thread


def _get_agent():
    """Lazily build the shared CodingAgent (imports tools, loads memory)."""
    global _AGENT
    if _AGENT is None:
        with _AGENT_LOCK:
            if _AGENT is None:
                from agent import CodingAgent

                _AGENT = CodingAgent()
    return _AGENT


def _run_async(coro, timeout: float = 600.0):
    """Submit a coroutine to the background loop and block for its result."""
    fut = asyncio.run_coroutine_threadsafe(coro, _LOOP)
    return fut.result(timeout)


# ── Scheduled agents (LuckyD 6.0) ──────────────────────────────────────────
# Lazy singleton so the store is only created on first use.
_SCHED_STORE = None


def _sched_store():
    global _SCHED_STORE
    if _SCHED_STORE is None:
        from core.scheduler import ScheduleStore

        _SCHED_STORE = ScheduleStore()
    return _SCHED_STORE


def _sched_run_background(schedule_id: str) -> None:
    from core.schedule_runner import run_schedule

    with contextlib.suppress(Exception):
        run_schedule(_sched_store(), schedule_id, force=True, reason="manual")


# ── in-memory background task registry ───────────────────────────────────────
_TASKS: dict[str, dict] = {}
_TASKS_LOCK = threading.Lock()


async def _agent_run(task: str) -> str:
    """Serialise agent runs — the shared agent cannot run two turns at once."""
    from core.run_lock import run_exclusive

    agent = _get_agent()
    # Shared run lock: never run the interactive agent concurrently with
    # a scheduled run (threads or other processes) — shared state
    # (workspace files, trust/schedule stores) must not mutate twice.
    async with _run_lock, run_exclusive():
        return await agent.run(task)


def _bg_worker(task_id: str, task: str) -> None:
    """Run an agent task in the background, recording status for polling."""
    with _TASKS_LOCK:
        _TASKS[task_id]["status"] = "running"
    try:
        result = _run_async(_agent_run(task), timeout=1800.0)
        with _TASKS_LOCK:
            _TASKS[task_id]["status"] = "done"
            _TASKS[task_id]["result"] = result
    except Exception as exc:  # never let the worker thread die silently
        with _TASKS_LOCK:
            _TASKS[task_id]["status"] = "error"
            _TASKS[task_id]["error"] = f"{type(exc).__name__}: {exc}"


# ── HTTP handler ─────────────────────────────────────────────────────────────
class HQHandler(BaseHTTPRequestHandler):
    server_version = "LuckyDHQ/2.1"

    def _send_json(self, obj, code: int = 200) -> None:
        try:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            # No Access-Control-Allow-Origin here on purpose: this server runs
            # the full agent (Bash/Write/Git). Wildcard CORS let any webpage
            # open in any browser on the machine call it. Same-origin fetches
            # from the HQ page itself don't need CORS headers at all.
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionError, OSError):
            pass

    def _send_html(self, html: str, code: int = 200) -> None:
        try:
            body = html.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            # The HQ page runs the full agent; keep plugins/foreign framing out.
            self.send_header("Content-Security-Policy", "object-src 'none'; base-uri 'self'")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionError, OSError):
            pass

    def _require_schedule_approval(self, tool_name: str, tool_args: dict) -> bool:
        """Gate a mutating schedule action through the trust approval hook.

        Returns True when the trust policy explicitly authorizes the action.
        Otherwise sends HTTP 403 (the denial is recorded in the audit log)
        and returns False. Non-blocking on purpose: this route has no
        interactive approver, so a "needs approval" verdict denies here — the
        user can perform the action conversationally via the agent instead,
        where the pending-approval queue (Trust dashboard) applies.
        """
        hook = _approval_hook()
        if hook is None:
            self._send_json({"error": "approval hook not wired"}, code=503)
            return False
        decision, reason = hook.evaluate_policy(
            tool_name, tool_args, HookContext(turn=0, messages=[], config={})
        )
        if decision == "approved":
            return True
        if decision == "needs_approval":
            # evaluate_policy doesn't audit the not-yet-made decision; the
            # denial on this route is the final outcome, so record it.
            hook._audit_decision(
                tool_name,
                tool_args,
                "denied",
                f"schedule dashboard action blocked: {reason}",
            )
        self._send_json(
            {
                "error": f"schedule action requires approval: {reason}",
                "needs_approval": decision == "needs_approval",
            },
            code=403,
        )
        return False

    def _cookie_token(self) -> str:
        """Extract our session cookie value without logging it."""
        try:
            for part in self.headers.get("Cookie", "").split(";"):
                name, _, value = part.partition("=")
                if name.strip() == HQ_COOKIE:
                    return value.strip().strip('"')
        except Exception:
            return ""
        return ""

    def _authorized(self) -> bool:
        """Require the per-install credential on every call.

        Accepts the ``Authorization: Bearer`` header (external clients, the
        harness supervisor) or the HttpOnly ``luckyd_hq`` session cookie the
        browser sets on its own profile for the in-browser HQ tab (4.0).
        Compared in constant time.
        """
        if hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {_TOKEN}"):
            return True
        presented = self._cookie_token()
        return bool(presented) and hmac.compare_digest(presented, _TOKEN)

    _DENY_NAMES = {".env", ".git"}

    def _path_denied(self, target: Path) -> str:
        """Return a denial reason, or "" if the path is allowed.

        Uses Path.is_relative_to instead of a plain string-prefix check —
        str(target).startswith(str(PROJECT_DIR)) also matches a sibling
        directory like PROJECT_DIR + "-anything". Also blocks .env/.git
        outright regardless of where they sit under the project root.
        """
        try:
            if not target.is_relative_to(PROJECT_DIR):
                return "path outside project"
        except (OSError, ValueError):
            return "invalid path"
        for part in target.parts:
            if part in self._DENY_NAMES:
                return f"access to '{part}' is not allowed"
        return ""

    def _origin_ok(self) -> bool:
        """Reject requests carrying a cross-origin Origin header outright.

        A same-origin page fetching '/api/...' from this same server won't
        send a mismatched Origin. A page loaded from any other site making a
        cross-origin fetch() will — and normal browsers cannot forge this
        header, so this blocks the "any open tab can reach 127.0.0.1:8000"
        class of attack even before the token check runs.
        """
        origin = self.headers.get("Origin")
        if not origin:
            return True  # no Origin header: not a cross-origin browser fetch
        host = self.headers.get("Host", "")
        return origin in (f"http://{host}", f"https://{host}")

    def _body(self) -> dict:
        try:
            size = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            size = 0
        if size <= 0:
            return {}
        try:
            data = json.loads(self.rfile.read(size) or b"{}")
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def log_message(self, *args):  # keep the server quiet
        pass

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        q = parse_qs(parsed.query)
        if not self._origin_ok():
            return self._send_json({"error": "forbidden origin"}, code=403)
        try:
            if path == "/health":
                return self._send_json({"status": "healthy"})
            if not self._authorized():
                return self._send_json({"error": "unauthorized"}, code=401)
            if path in ("/", "/index.html"):
                # Served only after auth (4.0): the page's own fetch() calls
                # authenticate via the HttpOnly session cookie, so no token
                # is injected into the HTML anymore.
                return self._send_html(_HQ_HTML)
            if path == "/api/tools":
                return self._send_json(
                    {"tools": registry.list_with_descriptions(), "count": registry.count}
                )
            if path == "/api/models":
                cfg = get_config()
                return self._send_json(
                    {"models": [cfg.get("model", "")], "provider": cfg.get("provider", "")}
                )
            if path == "/api/files":
                files = sorted(
                    str(p.relative_to(PROJECT_DIR))
                    for p in PROJECT_DIR.rglob("*")
                    if p.is_file()
                    and ".git" not in p.parts
                    and "__pycache__" not in p.parts
                    and p.suffix not in (".pyc", ".exe", ".dll")
                )[:2000]
                return self._send_json({"files": files})
            if path in ("/api/brain", "/api/brain/stats"):
                mem = get_memory()
                return self._send_json(
                    {
                        "stats": mem.summarize(),
                        "count": len(mem.graph.memories),
                        "edges": sum(len(v) for v in mem.graph.edges.values()),
                    }
                )
            if path == "/api/brain/search":
                query = (q.get("q") or [""])[0]
                mem = get_memory()
                results = [
                    {"content": n.content, "tags": n.tags, "score": round(s, 4)}
                    for n, s in mem.search_text(query, limit=10)
                ]
                return self._send_json({"results": results, "query": query})
            if path == "/api/cost":
                cost = getattr(_AGENT, "_cost_tracker", None) if _AGENT else None
                return self._send_json(
                    {
                        "input_tokens": getattr(cost, "total_input_tokens", 0),
                        "output_tokens": getattr(cost, "total_output_tokens", 0),
                        "total_cost": getattr(cost, "total_cost", 0.0),
                    }
                )
            if path == "/api/settings":
                cfg = get_config()
                return self._send_json(
                    {
                        "provider": cfg.get("provider"),
                        "model": cfg.get("model"),
                        "base_url": cfg.get("base_url"),
                        "max_turns": cfg.get("max_turns"),
                    }
                )
            if path in ("/api/tasks", "/api/background"):
                with _TASKS_LOCK:
                    return self._send_json({"tasks": list(_TASKS.values())})
            if path == "/trust":
                return self._send_html(_TRUST_HTML)
            if path == "/api/audit":
                log = get_audit_log()
                return self._send_json(
                    {
                        "events": log.recent(
                            limit=int((q.get("limit") or ["100"])[0]),
                            tool=(q.get("tool") or [None])[0],
                            scope=(q.get("scope") or [None])[0],
                            decision=(q.get("decision") or [None])[0],
                        ),
                        "stats": log.stats(),
                    }
                )
            if path == "/api/trust/scopes":
                policy = get_policy()
                tools_by_scope: dict[str, list[str]] = {sid: [] for sid in SCOPES}
                for name in registry.list_tools():
                    tools_by_scope.setdefault(scope_of(name), []).append(name)
                return self._send_json(
                    {
                        "mode": policy.mode,
                        "scopes": [
                            {
                                "id": sid,
                                "title": meta["title"],
                                "desc": meta["desc"],
                                "policy": policy.scope_policy(sid),
                                "tools": sorted(tools_by_scope.get(sid, [])),
                            }
                            for sid, meta in SCOPES.items()
                        ],
                        "sites": policy.sites(),
                    }
                )
            if path == "/api/trust/policy":
                return self._send_json(get_policy().to_dict())
            if path == "/api/approvals/pending":
                hook = _approval_hook()
                return self._send_json({"pending": hook.pending_requests() if hook else []})
            if path == "/schedules":
                return self._send_html(_SCHEDULES_HTML)
            if path == "/api/schedules":
                return self._send_json({"schedules": [s.to_dict() for s in _sched_store().list()]})
            if path == "/api/schedules/runs":
                sid = (q.get("schedule_id") or [None])[0]
                limit = int((q.get("limit") or ["20"])[0])
                return self._send_json({"runs": _sched_store().history(sid, limit)})
            if path == "/api/schedules/digest":
                return self._send_json({"runs": _sched_store().digest()})
            if path == "/api/schedules/daemon":
                from core.schedule_daemon import get_service

                svc = get_service()
                return self._send_json(
                    {
                        "running": True,
                        "last_tick": svc.last_tick,
                        "last_error": svc.last_error,
                        "poll_sec": svc.poll_sec,
                    }
                )
            if path.startswith("/api/background/status/"):
                tid = path.rsplit("/", 1)[-1]
                with _TASKS_LOCK:
                    return self._send_json(_TASKS.get(tid, {"error": "not found"}))
            if path.startswith("/api/background/result/"):
                tid = path.rsplit("/", 1)[-1]
                with _TASKS_LOCK:
                    return self._send_json(_TASKS.get(tid, {"error": "not found"}))
            return self._send_json({"error": f"not found: {path}"}, code=404)
        except Exception as exc:
            return self._send_json({"error": f"{type(exc).__name__}: {exc}"}, code=500)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if not self._origin_ok():
            return self._send_json({"error": "forbidden origin"}, code=403)
        if not self._authorized():
            return self._send_json({"error": "unauthorized"}, code=401)
        body = self._body()
        try:
            if path in ("/api/chat", "/chat", "/api/orchestrate"):
                task = body.get("task") or body.get("message") or body.get("prompt") or ""
                if not task:
                    return self._send_json({"error": "task/message required"}, code=400)
                result = _run_async(_agent_run(task), timeout=600.0)
                return self._send_json({"result": result, "response": result})
            if path == "/api/parallel":
                task = body.get("task", "")
                result = _run_async(_agent_run(task), timeout=600.0)
                return self._send_json({"result": result})
            if path == "/api/background/start":
                task = body.get("task", "")
                if not task:
                    return self._send_json({"error": "task required"}, code=400)
                task_id = uuid.uuid4().hex[:12]
                with _TASKS_LOCK:
                    _TASKS[task_id] = {
                        "id": task_id,
                        "task": task,
                        "status": "queued",
                        "created": time.time(),
                    }
                threading.Thread(target=_bg_worker, args=(task_id, task), daemon=True).start()
                return self._send_json({"task_id": task_id, "id": task_id})
            if path == "/api/brain/search":
                query = body.get("query", "")
                mem = get_memory()
                results = [
                    {"content": n.content, "tags": n.tags, "score": round(s, 4)}
                    for n, s in mem.search_text(query, limit=10)
                ]
                return self._send_json({"results": results, "query": query})
            if path == "/api/read-file":
                rel = body.get("path", "")
                target = (PROJECT_DIR / rel).resolve()
                denied = self._path_denied(target)
                if denied:
                    return self._send_json({"error": denied}, code=403)
                return self._send_json(
                    {"content": target.read_text(encoding="utf-8", errors="replace")}
                )
            if path == "/api/write-file":
                rel = body.get("path", "")
                target = (PROJECT_DIR / rel).resolve()
                denied = self._path_denied(target)
                if denied:
                    return self._send_json({"error": denied}, code=403)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(body.get("content", ""), encoding="utf-8")
                return self._send_json({"ok": True, "path": rel})
            if path == "/api/clear":
                if _AGENT is not None:
                    _AGENT.messages.clear()
                return self._send_json({"ok": True})
            if path == "/api/trust/policy":
                policy = get_policy()
                action = body.get("action", "")
                try:
                    if action == "set_mode":
                        policy.set_mode(body.get("mode", ""))
                    elif action == "set_scope":
                        policy.set_scope_policy(body.get("scope", ""), body.get("policy", ""))
                    elif action == "set_site":
                        policy.set_site_policy(body.get("host", ""), body.get("policy", "allow"))
                    elif action == "clear_site":
                        policy.clear_site_policy(body.get("host", ""))
                    else:
                        return self._send_json({"error": f"unknown action: {action}"}, code=400)
                except ValueError as exc:
                    return self._send_json({"error": str(exc)}, code=400)
                return self._send_json({"ok": True, "policy": policy.to_dict()})
            if path == "/api/approvals/resolve":
                hook = _approval_hook()
                if hook is None:
                    return self._send_json({"error": "approval hook not wired"}, code=503)
                ok = hook.resolve_approval(
                    body.get("call_id", ""),
                    bool(body.get("approved", False)),
                    body.get("reason"),
                    body.get("remember", "once"),
                )
                if not ok:
                    return self._send_json({"error": "unknown or expired call_id"}, code=404)
                return self._send_json({"ok": True})
            if path == "/api/schedules":
                from tools.schedule_tools import ScheduleCreateTool

                # Explicit None checks (not `or` defaults): 0 is a legitimate
                # value for the numeric fields (e.g. max_retries=0).
                create_args = {
                    "name": body.get("name", ""),
                    "prompt": body.get("prompt", ""),
                    "cron": body.get("cron", ""),
                    "every_minutes": int(
                        body.get("every_minutes") if body.get("every_minutes") is not None else 0
                    ),
                    "daily_at": body.get("daily_at", ""),
                    "allow_scopes": json.dumps(body.get("allow_scopes") or []),
                    "max_turns": int(
                        body.get("max_turns") if body.get("max_turns") is not None else 25
                    ),
                    "max_runtime_minutes": int(
                        body.get("max_runtime_minutes")
                        if body.get("max_runtime_minutes") is not None
                        else 10
                    ),
                    "max_retries": int(
                        body.get("max_retries") if body.get("max_retries") is not None else 1
                    ),
                }
                if not self._require_schedule_approval("ScheduleCreate", create_args):
                    return
                out = _run_async(
                    ScheduleCreateTool().execute(**create_args),
                    timeout=30.0,
                )
                if out.error:
                    return self._send_json({"error": out.text}, code=400)
                return self._send_json({"ok": True, "schedule": out.metadata.get("schedule")})
            if path == "/api/schedules/digest/seen":
                _sched_store().mark_digest_seen()
                return self._send_json({"ok": True})
            if path.startswith("/api/schedules/"):
                parts = path.split("/")
                if len(parts) == 5:
                    sid, action = parts[3], parts[4]
                    from tools.schedule_tools import (
                        ScheduleDeleteTool,
                        ScheduleDisableTool,
                        ScheduleEnableTool,
                        ScheduleUpdateTool,
                    )

                    if action == "delete":
                        if not self._require_schedule_approval("ScheduleDelete", {"id": sid}):
                            return
                        out = _run_async(ScheduleDeleteTool().execute(sid), timeout=30.0)
                    elif action == "enable":
                        if not self._require_schedule_approval("ScheduleEnable", {"id": sid}):
                            return
                        out = _run_async(ScheduleEnableTool().execute(sid), timeout=30.0)
                    elif action == "disable":
                        if not self._require_schedule_approval("ScheduleDisable", {"id": sid}):
                            return
                        out = _run_async(ScheduleDisableTool().execute(sid), timeout=30.0)
                    elif action == "run":
                        if not self._require_schedule_approval("ScheduleRunNow", {"id": sid}):
                            return
                        threading.Thread(
                            target=_sched_run_background, args=(sid,), daemon=True
                        ).start()
                        return self._send_json({"ok": True, "started": True})
                    elif action == "update":
                        fields = {
                            k: body[k]
                            for k in (
                                "name",
                                "prompt",
                                "cron",
                                "every_minutes",
                                "daily_at",
                                "allow_scopes",
                                "max_turns",
                                "max_runtime_minutes",
                                "max_retries",
                            )
                            # Explicit None/"" checks: 0 is a legitimate value
                            # (e.g. max_retries=0) and must not be dropped.
                            if body.get(k) is not None and body.get(k) != ""
                        }
                        if "allow_scopes" in fields and isinstance(fields["allow_scopes"], list):
                            fields["allow_scopes"] = json.dumps(fields["allow_scopes"])
                        if not self._require_schedule_approval(
                            "ScheduleUpdate", {"id": sid, **fields}
                        ):
                            return
                        out = _run_async(ScheduleUpdateTool().execute(sid, **fields), timeout=30.0)
                    else:
                        return self._send_json({"error": f"unknown action: {action}"}, code=404)
                    if out.error:
                        return self._send_json({"error": out.text}, code=400)
                    return self._send_json({"ok": True, "result": out.text})
            return self._send_json({"error": f"not found: {path}"}, code=404)
        except Exception as exc:
            return self._send_json({"error": f"{type(exc).__name__}: {exc}"}, code=500)


# ── Trust dashboard (LuckyD 6.0 — "agentic with receipts") ──────────────────
_TRUST_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LuckyD Trust Center</title>
<style>
:root{--bg:#0b0e14;--panel:#141a26;--border:#243049;--text:#e6ebf5;--dim:#8b98b0;
--acc:#4f8cff;--ok:#3fb950;--warn:#d29922;--bad:#f85149}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:14px/1.5 system-ui,Segoe UI,Roboto,sans-serif;padding:0 0 60px}
header{padding:14px 22px;background:var(--panel);border-bottom:1px solid var(--border);
display:flex;align-items:center;gap:14px;position:sticky;top:0;z-index:5}
header b{color:var(--acc);font-size:17px}.pill{font-size:11px;padding:2px 10px;border-radius:99px;
background:#0e2417;color:var(--ok);border:1px solid #1d3a26}
main{max-width:1100px;margin:0 auto;padding:20px}
section{background:var(--panel);border:1px solid var(--border);border-radius:12px;
padding:18px;margin-bottom:18px}
h2{margin:0 0 4px;font-size:16px}.sub{color:var(--dim);font-size:12.5px;margin:0 0 14px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:var(--dim);font-weight:600;padding:8px;border-bottom:1px solid var(--border)}
td{padding:8px;border-bottom:1px solid var(--border);vertical-align:top}
select,input{background:var(--bg);border:1px solid var(--border);color:var(--text);
border-radius:8px;padding:7px 10px;font:inherit}
button{background:var(--acc);color:#fff;border:0;border-radius:8px;padding:8px 16px;
font:inherit;cursor:pointer;margin-right:8px}
button.deny{background:var(--bad)}button.ghost{background:transparent;border:1px solid var(--border)}
button:disabled{opacity:.5}
.risk{font-size:11px;padding:2px 8px;border-radius:99px;border:1px solid}
.risk.high{color:var(--bad);border-color:var(--bad)}
.risk.medium{color:var(--warn);border-color:var(--warn)}
.risk.low{color:var(--ok);border-color:var(--ok)}
.dec{font-size:11px;padding:2px 8px;border-radius:99px;background:var(--bg);border:1px solid var(--border)}
.dec.denied,.dec.blocked{color:var(--bad);border-color:var(--bad)}
.dec.approved,.dec.executed,.dec.auto{color:var(--ok)}
.mono{font-family:ui-monospace,Consolas,monospace;font-size:12px;color:var(--dim);
word-break:break-all;max-width:420px}
.appr{border:1px solid var(--warn);border-radius:10px;padding:14px;margin-bottom:12px;background:#141207}
.appr h3{margin:0 0 6px;font-size:14px}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.empty{color:var(--dim);font-style:italic}
#toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:var(--panel);
border:1px solid var(--border);padding:10px 18px;border-radius:10px;display:none}
</style></head><body>
<header><b>&#9670; LuckyD Trust Center</b><span class="pill">agentic with receipts</span>
<span style="flex:1"></span>
<label class="sub" style="margin:0">Mode&nbsp;
<select id="mode" onchange="setMode(this.value)">
<option value="ask">Ask me</option><option value="auto">Auto-approve</option>
<option value="step-through">Step-through</option></select></label></header>
<main>
<section><h2>&#9203; Pending approvals</h2>
<p class="sub">The agent is waiting on these. Approve once, for this session, always for the scope, or always for the site.</p>
<div id="pending"><p class="empty">Loading&hellip;</p></div></section>
<section><h2>&#128274; What the agent can touch</h2>
<p class="sub">Permission scopes. "Ask" pauses for your approval, "Allow" runs freely, "Deny" blocks outright.</p>
<table><thead><tr><th>Scope</th><th>Tools</th><th>Policy</th></tr></thead>
<tbody id="scopes"><tr><td colspan="3" class="empty">Loading&hellip;</td></tr></tbody></table></section>
<section><h2>&#127760; Site rules</h2>
<p class="sub">Browser-control rules per website. "Allow" = the agent can drive this site without asking.</p>
<div id="sites"></div>
<div class="row" style="margin-top:10px"><input id="sitehost" placeholder="example.com" style="width:220px">
<button onclick="addSite()">Add allow rule</button></div></section>
<section><h2>&#129534; Audit log</h2>
<p class="sub" id="stats"></p>
<div class="row" style="margin-bottom:10px"><label class="sub" style="margin:0">Scope&nbsp;
<select id="fScope" onchange="loadAudit()"><option value="">all</option></select></label>
<label class="sub" style="margin:0">Decision&nbsp;
<select id="fDec" onchange="loadAudit()"><option value="">all</option>
<option>approved</option><option>denied</option><option>blocked</option>
<option>executed</option><option>auto</option></select></label>
<button class="ghost" onclick="loadAudit()">Refresh</button></div>
<table><thead><tr><th>Time</th><th>Tool</th><th>Scope</th><th>Risk</th><th>Decision</th><th>Detail</th></tr></thead>
<tbody id="audit"><tr><td colspan="6" class="empty">Loading&hellip;</td></tr></tbody></table></section>
</main><div id="toast"></div>
<script>
const $=id=>document.getElementById(id);
function toast(m){const t=$('toast');t.textContent=m;t.style.display='block';
setTimeout(()=>t.style.display='none',2500);}
async function api(path,body){const r=await fetch(path,{method:body?'POST':'GET',
headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});
return r.json();}
function esc(s){return String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
async function loadPending(){const d=await api('/api/approvals/pending');
const box=$('pending');
if(!d.pending.length){box.innerHTML='<p class="empty">Nothing waiting. The agent will pause here when it needs you.</p>';return;}
box.innerHTML=d.pending.map(p=>`<div class="appr"><h3>${esc(p.tool)} <span class="risk ${p.risk}">${p.risk}</span>
<span class="dec">${esc(p.scope)}</span></h3><div class="mono">${esc(p.summary)}</div>
<div class="row" style="margin-top:10px"><select id="rm-${p.call_id}">
<option value="once">Just once</option><option value="session">This session</option>
<option value="always">Always (${esc(p.scope)})</option>
<option value="site">Always on this site</option></select>
<button onclick="resolve('${p.call_id}',true)">Approve</button>
<button class="deny" onclick="resolve('${p.call_id}',false)">Deny</button></div></div>`).join('');}
async function resolve(id,ok){const r=await api('/api/approvals/resolve',
{call_id:id,approved:ok,remember:$('rm-'+id).value});
if(r.ok){toast(ok?'Approved':'Denied');loadPending();loadAudit();}else toast('Error: '+(r.error||'?'));}
async function loadScopes(){const d=await api('/api/trust/scopes');
$('mode').value=d.mode;
$('scopes').innerHTML=d.scopes.map(s=>`<tr><td><b>${esc(s.title)}</b><br>
<span class="sub">${esc(s.desc)}</span></td>
<td><span class="mono">${s.tools.length} tools</span><br>
<span class="sub">${esc(s.tools.slice(0,6).join(', '))}${s.tools.length>6?'&hellip;':''}</span></td>
<td><select onchange="setScope('${s.id}',this.value)">
${['ask','allow','deny'].map(p=>`<option ${p===s.policy?'selected':''}>${p}</option>`).join('')}
</select></td></tr>`).join('');
const fs=$('fScope');const cur=fs.value;
fs.innerHTML='<option value="">all</option>'+d.scopes.map(s=>`<option value="${s.id}">${esc(s.title)}</option>`).join('');
fs.value=cur;
$('sites').innerHTML=Object.entries(d.sites).map(([h,p])=>
`<span class="dec">${esc(h)}: ${esc(p)}</span> <button class="ghost" onclick="delSite('${esc(h)}')">remove</button> `).join('')
||'<p class="empty">No site rules yet.</p>';}
async function setMode(m){const r=await api('/api/trust/policy',{action:'set_mode',mode:m});
toast(r.ok?'Mode updated':'Error: '+(r.error||'?'));}
async function setScope(s,p){const r=await api('/api/trust/policy',{action:'set_scope',scope:s,policy:p});
toast(r.ok?'Policy updated':'Error: '+(r.error||'?'));loadAudit();}
async function addSite(){const h=$('sitehost').value.trim();if(!h)return;
const r=await api('/api/trust/policy',{action:'set_site',host:h,policy:'allow'});
if(r.ok){$('sitehost').value='';loadScopes();}else toast('Error: '+(r.error||'?'));}
async function delSite(h){await api('/api/trust/policy',{action:'clear_site',host:h});loadScopes();}
async function loadAudit(){const s=$('fScope').value,d=$('fDec').value;
const q=new URLSearchParams({limit:100});if(s)q.set('scope',s);if(d)q.set('decision',d);
const r=await api('/api/audit?'+q);
$('stats').textContent=`${r.stats.total} events recorded · ${r.stats.denied} denied/blocked`;
$('audit').innerHTML=r.events.map(e=>`<tr><td class="mono">${esc(e.ts.slice(11,19))}</td>
<td><b>${esc(e.tool)}</b></td><td>${esc(e.scope)}</td>
<td><span class="risk ${e.risk}">${esc(e.risk)}</span></td>
<td><span class="dec ${esc(e.decision)}">${esc(e.decision)}</span></td>
<td class="mono">${esc(e.summary||JSON.stringify(e.args).slice(0,120))}</td></tr>`).join('')
||'<tr><td colspan="6" class="empty">No events yet.</td></tr>';}
loadPending();loadScopes();loadAudit();setInterval(loadPending,2000);
</script></body></html>"""


# ── Schedules dashboard (LuckyD 6.0 — "works while you rest") ───────────────
_SCHEDULES_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LuckyD — Scheduled Agents</title>
<style>
body{font-family:system-ui,sans-serif;max-width:960px;margin:0 auto;padding:20px;color:#222}
h1{font-size:22px}h2{font-size:17px;margin-top:28px}
.card{border:1px solid #ddd;border-radius:10px;padding:14px;margin:10px 0;background:#fafafa}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:8px;border-bottom:1px solid #eee;vertical-align:top}
button{border:1px solid #ccc;background:#fff;border-radius:6px;padding:5px 10px;cursor:pointer;margin:2px}
button:hover{background:#f0f0f0}.danger{color:#a00}
input,select,textarea{border:1px solid #ccc;border-radius:6px;padding:6px;margin:3px 0;width:100%;box-sizing:border-box}
textarea{height:70px}.row{display:flex;gap:8px}.row>div{flex:1}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px}
.ok{background:#e6f4ea}.bad{background:#fdecea}.run{background:#e8f0fe}.idle{background:#eee}
#digest{display:none;border-left:4px solid #f9ab00;background:#fff8e1}
.muted{color:#666;font-size:13px}
</style></head><body>
<h1>⏰ Scheduled Agents</h1>
<p class="muted">Background agents that work while you rest. Unattended runs can never use shell, desktop, or system tools — every run is audit-logged.</p>
<div id="digest" class="card"></div>
<h2>New schedule</h2>
<div class="card">
<div class="row"><div><label>Name<input id="f_name" placeholder="morning flight check"></label></div>
<div><label>Kind<select id="f_kind"><option value="daily">daily at…</option><option value="cron">cron</option><option value="every">every N min</option></select></label></div>
<div><label>When<input id="f_when" placeholder="07:30"></label></div></div>
<label>Task prompt<textarea id="f_prompt" placeholder="What should the agent do each run?"></textarea></label>
<div class="row"><div><label>Scopes (comma-sep)<input id="f_scopes" value="read,network,memory"></label></div>
<div><label>Max turns<input id="f_turns" type="number" value="25"></label></div>
<div><label>Max minutes<input id="f_mins" type="number" value="10"></label></div>
<div><label>Retries<input id="f_retries" type="number" value="1"></label></div></div>
<button onclick="createSched()">Create schedule</button>
<span id="createMsg" class="muted"></span>
<p class="muted">daily at → HH:MM (24h) · cron → 5 fields like <code>0 7 * * *</code> · every N min → number ≥ 5</p>
</div>
<h2>Schedules</h2>
<div id="scheds"></div>
<h2>Recent runs</h2>
<div id="runs" class="card muted">loading…</div>
<script>
async function api(p,o={}){const r=await fetch(p,{headers:{'Content-Type':'application/json'},...o});return r.json();}
function esc(s){return String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
async function loadDigest(){const d=await api('/api/schedules/digest');const el=document.getElementById('digest');
if(d.runs&&d.runs.length){el.style.display='block';const bad=d.runs.filter(r=>r.status!=='ok').length;
el.innerHTML='<b>☀️ Morning digest:</b> '+d.runs.length+' run(s) since you last checked'+(bad?' — <b>'+bad+' need attention</b>.':' — all good.')+
d.runs.map(r=>'<div>'+(r.status==='ok'?'✅':'⚠️')+' <b>'+esc(r.schedule_name)+'</b> — '+esc(r.status)+': '+esc((r.summary||r.error||'').slice(0,160))+'</div>').join('')+
'<button onclick="seenDigest()">Mark as read</button>';}else{el.style.display='none';}}
async function seenDigest(){await api('/api/schedules/digest/seen',{method:'POST'});loadDigest();}
async function loadScheds(){const d=await api('/api/schedules');const el=document.getElementById('scheds');
if(!d.schedules.length){el.innerHTML='<p class="muted">No schedules yet.</p>';return;}
el.innerHTML='<table><tr><th>Name</th><th>When</th><th>Next run</th><th>Scopes</th><th>Last</th><th></th></tr>'+
d.schedules.map(s=>{const when=s.kind==='cron'?('cron '+esc(s.cron)):s.kind==='every'?('every '+s.every_minutes+'m'):('daily '+esc(s.daily_at));
return '<tr><td><b>'+esc(s.name)+'</b><br><span class="muted">'+esc(s.id)+'</span></td><td>'+when+'</td><td>'+esc(s.next_run_at||'—')+'</td><td class="muted">'+esc(s.allow_scopes.join(','))+'</td><td><span class="badge '+(s.last_status==='ok'?'ok':s.last_status?'bad':'idle')+'">'+esc(s.last_status||'never')+'</span> '+(s.enabled?'':'⏸️')+'</td><td>'+
(s.enabled?'<button onclick="act(\\''+s.id+'\\',\\'disable\\')">Pause</button>':'<button onclick="act(\\''+s.id+'\\',\\'enable\\')">Enable</button>')+
'<button onclick="act(\\''+s.id+'\\',\\'run\\')">Run now</button>'+
'<button class="danger" onclick="act(\\''+s.id+'\\',\\'delete\\')">Delete</button></td></tr>';}).join('')+'</table>';}
async function act(id,a){if(a==='delete'&&!confirm('Delete this schedule and its history?'))return;await api('/api/schedules/'+id+'/'+a,{method:'POST'});loadScheds();}
async function createSched(){const kind=document.getElementById('f_kind').value,when=document.getElementById('f_when').value.trim();
const body={name:document.getElementById('f_name').value,prompt:document.getElementById('f_prompt').value,kind,
allow_scopes:document.getElementById('f_scopes').value.split(',').map(s=>s.trim()).filter(Boolean),
max_turns:+document.getElementById('f_turns').value,max_runtime_minutes:+document.getElementById('f_mins').value,
max_retries:+document.getElementById('f_retries').value};
if(kind==='cron')body.cron=when;else if(kind==='every')body.every_minutes=+when;else body.daily_at=when;
const r=await api('/api/schedules',{method:'POST',body:JSON.stringify(body)});
document.getElementById('createMsg').textContent=r.error?('Error: '+r.error):'Created!';
if(!r.error)loadScheds();}
async function loadRuns(){const d=await api('/api/schedules/runs?limit=15');const el=document.getElementById('runs');
el.innerHTML=d.runs.length?d.runs.map(r=>'<div><span class="badge '+(r.status==='ok'?'ok':r.status==='running'?'run':'bad')+'">'+esc(r.status)+'</span> <b>'+esc(r.schedule_name)+'</b> <span class="muted">'+esc(r.started_at||'')+' · '+Math.round(r.duration_sec||0)+'s · attempt '+r.attempt+'</span><br>'+esc((r.summary||r.error||'').slice(0,220))+'</div>').join(''):'No runs yet.';}
loadDigest();loadScheds();loadRuns();setInterval(()=>{loadScheds();loadRuns();},15000);
</script></body></html>"""


# ── minimal HQ landing page ──────────────────────────────────────────────────
_HQ_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Coding Agent</title>
<style>
:root{--bg:#0b0e14;--panel:#141a26;--border:#243049;--text:#e6ebf5;--dim:#8b98b0;--acc:#4f8cff;}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:600 14px/1.5 system-ui,Segoe UI,Roboto,sans-serif;display:flex;flex-direction:column;height:100vh}
header{padding:12px 18px;background:var(--panel);border-bottom:1px solid var(--border);
display:flex;align-items:center;gap:10px}
header b{color:var(--acc)}.pill{font-size:11px;padding:2px 8px;border-radius:99px;
background:#0e2417;color:#3fb950;border:1px solid #1d3a26}
#chat{flex:1;overflow-y:auto;padding:18px;display:flex;flex-direction:column;gap:12px}
.msg{max-width:80%;padding:10px 14px;border-radius:12px;white-space:pre-wrap;word-wrap:break-word}
.user{align-self:flex-end;background:var(--acc);color:#fff;border-bottom-right-radius:4px}
.agent{align-self:flex-start;background:var(--panel);border:1px solid var(--border);border-bottom-left-radius:4px}
.agent.thinking{color:#6CB6FF;font-style:italic}
#bar{display:flex;gap:10px;padding:14px;background:var(--panel);border-top:1px solid var(--border)}
#in{flex:1;background:var(--bg);border:1px solid var(--border);color:var(--text);
border-radius:8px;padding:11px 13px;font:inherit;resize:none}
#in:focus{outline:none;border-color:var(--acc)}
button{background:var(--acc);color:#fff;border:0;border-radius:8px;padding:0 20px;
font:inherit;cursor:pointer}button:disabled{opacity:.5;cursor:default}
.dim{color:var(--dim);font-size:12px}
</style></head><body>
<header><b>&#9670; Coding Agent</b><span class="pill">live source</span></header>
<div id="chat"><div class="msg agent">Online &mdash; ready to help. Ask me to
build, fix, or explore.</div></div>
<form id="bar"><textarea id="in" rows="1" placeholder="Message&hellip;"></textarea>
<button id="send" type="submit">Send</button></form>
<script>
const chat=document.getElementById('chat'),inp=document.getElementById('in'),
btn=document.getElementById('send');
function add(cls,text){const d=document.createElement('div');d.className='msg '+cls;
d.textContent=text;chat.appendChild(d);chat.scrollTop=chat.scrollHeight;return d;}
// Auth: HttpOnly luckyd_hq session cookie, sent automatically (4.0).
document.getElementById('bar').addEventListener('submit',async e=>{e.preventDefault();
const t=inp.value.trim();if(!t)return;inp.value='';add('user',t);btn.disabled=true;
const thinking=add('agent','\\u2026');thinking.classList.add('thinking');
try{const r=await fetch('/api/chat',{method:'POST',
headers:{'Content-Type':'application/json'},body:JSON.stringify({task:t})});
const d=await r.json();thinking.classList.remove('thinking');thinking.textContent=d.result||d.response||d.error||'(no reply)';}
catch(err){thinking.classList.remove('thinking');thinking.textContent='Error: '+err.message;}finally{btn.disabled=false;inp.focus();}});
</script></body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser(description="LuckyD Code Harness HQ web server")
    ap.add_argument("--web", action="store_true", help="accepted for exe CLI parity")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    args, _ = ap.parse_known_args()

    # Wire the same tiered approval system the CLI uses (core/approval_hook.py)
    # so this entry point isn't the one path that bypasses it. Previously
    # nothing ever called register_plugin() here, so ApprovalHook never even
    # loaded for this server — Bash/Write/Git ran with zero gating. The legacy
    # blanket auto_approve_all bypass is inert since 6.1: approvals follow the
    # trust policy (ask by default); the token+Origin checks above gate access
    # to this server, and pending approvals are resolved via /trust.
    hook = ApprovalHook(session_id="web-hq")
    register_plugin(hook)
    register_plugin(AuditHook(session_id="web-hq"))
    global _APPROVAL_HOOK
    _APPROVAL_HOOK = hook

    # Eagerly import agent so all ~98 tools register before the first request.
    try:
        import agent  # noqa: F401
    except Exception as exc:
        print(f"  [warn] tool registration failed: {exc}")

    # Start the scheduled-agents daemon (LuckyD 6.0 — "works while you rest").
    try:
        from core.schedule_daemon import get_service

        get_service()
        print("  scheduler daemon: running (see /schedules)")
    except Exception as exc:
        print(f"  [warn] scheduler failed to start: {exc}")

    server = ThreadingHTTPServer((args.host, args.port), HQHandler)
    server.daemon_threads = True
    print(f"LuckyD Code HQ (live source) on http://{args.host}:{args.port}")
    print(f"  tools registered: {registry.count}")
    print(f"  auth token: {_TOKEN_PATH}")
    print("  (open the URL above in a browser — the token is injected into the page for you)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        _LOOP.call_soon_threadsafe(_LOOP.stop)


if __name__ == "__main__":
    main()
