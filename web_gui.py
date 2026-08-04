#!/usr/bin/env python3
"""
LuckyD Code — Web GUI server (FastAPI + WebSocket).

Serves the browser chat UI (``static/index.html``) and bridges it to the shared
``CodingAgent`` over a WebSocket::

    Browser (static/*)  ⇄  WS /ws  ⇄  ui.WebUI events  →  CodingAgent

Client → server messages:
    {"type": "message", "text": "..."}                     chat or /slash command
    {"type": "approval", "id": "...", "decision": "y|n|a"} tool approval response
    {"type": "auto_approve", "value": true}                toggle auto-approve

Server → client events are emitted by ``ui.WebUI`` (token / thinking /
tool_start / tool_result / status / markdown / approval_request / session /
models / help / tools / done / goodbye).

NOTE: this is intentionally separate from ``web_server.py``, the Harness HQ
backend used by LuckyDBrowser — do not merge the two.

Launch with::

    python main.py --web                 # http://127.0.0.1:8787
    python main.py --web --port 9000
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from types import SimpleNamespace

STATIC_DIR = Path(__file__).resolve().parent / "static"


# ── Approval manager ─────────────────────────────────────────────────────────


class ApprovalManager:
    """Tracks pending tool-approval requests.

    The approval callback runs in a hook worker thread (agent loop calls hooks
    via ``asyncio.to_thread``) and blocks on a ``threading.Event``; the
    WebSocket receive loop resolves it when the user clicks Approve / Deny /
    Always in the browser.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, dict] = {}

    def open(self) -> tuple[str, threading.Event]:
        rid = uuid.uuid4().hex[:12]
        ev = threading.Event()
        with self._lock:
            self._pending[rid] = {"event": ev, "decision": "n"}
        return rid, ev

    def resolve(self, rid: str, decision: str) -> None:
        with self._lock:
            entry = self._pending.get(rid)
            if entry is not None:
                entry["decision"] = decision
                entry["event"].set()

    def wait(self, rid: str, ev: threading.Event, timeout: float = 300.0) -> str:
        try:
            if not ev.wait(timeout):
                return "timeout"
            with self._lock:
                return self._pending.get(rid, {}).get("decision", "n")
        finally:
            with self._lock:
                self._pending.pop(rid, None)


def make_approval_callback(ui, approvals: ApprovalManager, hook):
    """Build an ApprovalHook-compatible callback that asks the browser.

    Mirrors main._console_approval: y → approve once, n → deny, a → always
    allow (flips the tool's permission to ALWAYS_ALLOW on the hook).
    """
    from core.types import ToolApprovalResult, ToolPermissionLevel

    def _callback(request) -> ToolApprovalResult:
        rid, ev = approvals.open()
        ui._emit(
            {
                "type": "approval_request",
                "id": rid,
                "tool": request.tool_name,
                "args": ui._clean_args(request.tool_args),
            }
        )
        decision = approvals.wait(rid, ev, timeout=300.0)
        if decision == "a":
            if hook is not None and hasattr(hook, "set_permission"):
                hook.set_permission(request.tool_name, ToolPermissionLevel.ALWAYS_ALLOW)
            ui.success(f"{request.tool_name}: auto-approved for future calls")
            return ToolApprovalResult(approved=True, reason="always allow")
        if decision == "y":
            return ToolApprovalResult(approved=True)
        reason = "denied by user" if decision == "n" else "approval timed out"
        ui.warn(f"{request.tool_name}: {reason}")
        return ToolApprovalResult(approved=False, reason=reason)

    return _callback


# ── Tool display plugin ──────────────────────────────────────────────────────

import contextlib

from core.hooks import AgentPlugin


class WebToolDisplay(AgentPlugin):
    """Agent plugin that forwards tool activity to the WebUI as cards.

    Uses the same before_tool / after_tool hook points as the agent loop, but
    unlike the raw TOOL_END agent event it also carries the real elapsed time
    and a result preview. Registered after the approval hook, so a card only
    appears once the call is actually permitted to run.
    """

    name = "web-tool-display"

    def __init__(self, ui) -> None:
        self._ui = ui
        self._starts: dict[str, float] = {}

    def before_tool(self, tool_name, tool_args, ctx):
        self._starts[tool_args.get("_id", "")] = time.monotonic()
        self._ui.tool_call_start(tool_name, tool_args)
        return None

    def after_tool(self, tool_name, tool_args, result, ctx):
        start = self._starts.pop(tool_args.get("_id", ""), None)
        elapsed = (time.monotonic() - start) if start else 0.0
        content = result.get("content", "") if isinstance(result, dict) else str(result)
        ok = not str(content).startswith("Error")
        preview = str(content)[:300].replace("\n", " ").strip()
        self._ui.tool_call_result(tool_name, elapsed, ok, preview)
        return result


# ── Server ───────────────────────────────────────────────────────────────────


def _find_port(host: str, preferred: int, attempts: int = 10) -> int:
    """First bindable port at or above ``preferred`` (avoids HQ server clashes)."""
    for port in range(preferred, preferred + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
            except OSError:
                continue
            return port
    return preferred


def _make_send(out_q: asyncio.Queue, loop: asyncio.AbstractEventLoop):
    """Thread-safe event sink handed to WebUI.attach()."""

    def send(event: dict) -> None:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            out_q.put_nowait(event)
        else:
            loop.call_soon_threadsafe(out_q.put_nowait, event)

    return send


async def serve(
    *,
    agent,
    ui,
    host: str,
    port: int,
    command_handler,
    catalog_fn,
    hook=None,
    open_browser: bool = True,
) -> None:
    """Run the web GUI server until Ctrl+C or /quit.

    All dependencies are injected (agent, WebUI, slash-command handler, model
    catalog callable, approval hook) so this module never imports main.py.
    """
    try:
        import uvicorn
        from fastapi import FastAPI, WebSocket, WebSocketDisconnect
        from fastapi.responses import FileResponse, JSONResponse
        from fastapi.staticfiles import StaticFiles
    except ImportError as exc:
        print(
            "  [ERR] Web mode needs fastapi + uvicorn:\n"
            f"        pip install fastapi uvicorn   ({exc})"
        )
        return

    from core.hooks import register_plugin
    from tools.registry import registry
    from ui import SLASH_COMMANDS

    # Agent → WebUI wiring: token streams + tool activity cards.
    agent.stream_callback = ui.stream_token
    agent.think_callback = ui.stream_think_token
    register_plugin(WebToolDisplay(ui))

    approvals = ApprovalManager()
    if hook is not None:
        hook.approval_callback = make_approval_callback(ui, approvals, hook)

    app = FastAPI(
        title="LuckyD Code Web GUI",
        version="2.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    inbox: asyncio.Queue = asyncio.Queue()
    state = SimpleNamespace(server=None)

    def _request_shutdown() -> None:
        if state.server is not None:
            state.server.should_exit = True

    # ── worker: serializes agent runs (the shared agent is stateful) ──
    async def worker() -> None:
        while True:
            kind, text = await inbox.get()
            try:
                if kind == "command":
                    should_exit = await command_handler(agent, text)
                    ui._emit({"type": "done"})
                    if should_exit:
                        cost = (
                            agent.cost_tracker.summary() if hasattr(agent, "cost_tracker") else ""
                        )
                        ui.goodbye(cost_summary=cost)
                        asyncio.get_running_loop().call_later(0.6, _request_shutdown)
                else:
                    ui.start_streaming()
                    result = ""
                    try:
                        result = await agent.run(text)
                    except Exception as exc:
                        ui.error(f"Agent error: {exc}")
                        result = ""
                    finally:
                        ui.end_streaming()
                    if result and not ui.streamed_chars:
                        ui.markdown(result)
                    with contextlib.suppress(Exception):
                        ui.set_session_info(cost=agent.cost_tracker.summary())
                    ui._emit({"type": "done"})
            except Exception as exc:  # never let the worker die silently
                try:
                    ui.error(f"Internal error: {type(exc).__name__}: {exc}")
                    ui._emit({"type": "done"})
                except Exception:
                    pass

    worker_task = asyncio.create_task(worker(), name="web-worker")

    # ── HTTP routes ───────────────────────────────────────────────

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/commands")
    async def commands():
        return {"commands": [{"cmd": c, "desc": d} for c, d in SLASH_COMMANDS]}

    @app.get("/api/models")
    async def models():
        try:
            return JSONResponse(catalog_fn())
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.get("/api/tools")
    async def tools():
        return {"tools": registry.list_with_descriptions(), "count": registry.count}

    @app.post("/api/command")
    async def run_command(payload: dict):
        cmd = (payload.get("cmd") or payload.get("text") or "").strip()
        if not cmd:
            return JSONResponse({"error": "cmd required"}, status_code=400)
        if not cmd.startswith("/"):
            cmd = "/" + cmd
        await inbox.put(("command", cmd))
        return {"ok": True}

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # ── WebSocket endpoint ────────────────────────────────────────

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        loop = asyncio.get_running_loop()
        out_q: asyncio.Queue = asyncio.Queue()
        ui.attach(_make_send(out_q, loop))

        async def sender() -> None:
            while True:
                event = await out_q.get()
                await ws.send_json(event)

        sender_task = asyncio.create_task(sender())
        # Greet the client with the current session state.
        ui.set_session_info()
        ui.info("Connected to LuckyD Code.")
        try:
            while True:
                data = await ws.receive_json()
                mtype = data.get("type")
                if mtype == "message":
                    text = (data.get("text") or "").strip()
                    if not text:
                        continue
                    await inbox.put(("command" if text.startswith("/") else "message", text))
                elif mtype == "approval":
                    approvals.resolve(str(data.get("id", "")), str(data.get("decision", "n")))
                elif mtype == "auto_approve":
                    value = bool(data.get("value"))
                    if hook is not None:
                        hook.auto_approve_all = value
                    ui._emit({"type": "auto_approve", "value": value})
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            ui.detach()
            sender_task.cancel()

    # ── run uvicorn ───────────────────────────────────────────────

    actual_port = _find_port(host, port)
    url = f"http://{host}:{actual_port}"
    config = uvicorn.Config(app, host=host, port=actual_port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    state.server = server

    print(f"  LuckyD Code Web GUI on {url}")
    print(f"  tools registered: {registry.count}  ·  Ctrl+C to stop")
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()

    try:
        await server.serve()
    finally:
        worker_task.cancel()
        ui.detach()
