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
from core.hooks import register_plugin
from memory.store import get_memory
from tools.registry import registry

load_env()  # load .env before any agent/LLM construction

# ── Auth token (default-on) ──────────────────────────────────────────────
# Anyone who can reach this port and knows the token can run the full agent
# (Bash/PowerShell/Write/Git). Previously this server had NO auth at all and
# sent Access-Control-Allow-Origin: * on every response, so any webpage open
# in any browser on the machine could drive it. A random per-install token is
# generated once and persisted under .luckyd-code/ (already gitignored).
_TOKEN_PATH = PROJECT_DIR / ".luckyd-code" / "hq_token"


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


# ── in-memory background task registry ───────────────────────────────────────
_TASKS: dict[str, dict] = {}
_TASKS_LOCK = threading.Lock()


async def _agent_run(task: str) -> str:
    """Serialise agent runs — the shared agent cannot run two turns at once."""
    agent = _get_agent()
    async with _run_lock:
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
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionError, OSError):
            pass

    def _authorized(self) -> bool:
        """Require the per-install bearer token on every API call.

        The inline HQ page (served from '/') gets the token injected into its
        own JS so its same-origin fetches keep working without prompting the
        user for anything.
        """
        auth = self.headers.get("Authorization", "")
        return auth == f"Bearer {_TOKEN}"

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
            if path in ("/", "/index.html"):
                # The landing page itself doesn't need the token (it's just
                # static HTML+JS); its own fetch() calls carry the token so
                # they pass the check below like any other client.
                return self._send_html(_HQ_HTML.replace("__HQ_TOKEN__", _TOKEN))
            if not self._authorized():
                return self._send_json({"error": "unauthorized"}, code=401)
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
            return self._send_json({"error": f"not found: {path}"}, code=404)
        except Exception as exc:
            return self._send_json({"error": f"{type(exc).__name__}: {exc}"}, code=500)


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
const HQ_TOKEN='__HQ_TOKEN__';
document.getElementById('bar').addEventListener('submit',async e=>{e.preventDefault();
const t=inp.value.trim();if(!t)return;inp.value='';add('user',t);btn.disabled=true;
const thinking=add('agent','\\u2026');thinking.classList.add('thinking');
try{const r=await fetch('/api/chat',{method:'POST',
headers:{'Content-Type':'application/json','Authorization':'Bearer '+HQ_TOKEN},body:JSON.stringify({task:t})});
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
    # loaded for this server — Bash/Write/Git ran with zero gating. Mirrors
    # main.py's default (auto_approve_all=True): the token+Origin checks above
    # are what actually gates access to this server; this keeps the hook
    # consistently wired so it's live if that default is ever changed.
    hook = ApprovalHook(session_id="web-hq")
    hook.auto_approve_all = True
    register_plugin(hook)

    # Eagerly import agent so all ~98 tools register before the first request.
    try:
        import agent  # noqa: F401
    except Exception as exc:
        print(f"  [warn] tool registration failed: {exc}")

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
