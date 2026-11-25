"""Minimal Agent Client Protocol stdio server (LuckyD 9.8)."""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import traceback
from typing import Any

ACP_VERSION = "0.1"
AGENT_NAME = "LuckyD Code"
AGENT_VERSION = "v10.9.3"

_TRUTHY = ("1", "true", "yes", "on")


def _log(msg: str) -> None:
    """Diagnostics go to stderr only — stdout is reserved for responses."""
    print(f"[acp] {msg}", file=sys.stderr, flush=True)


def _acp_approval(request: Any) -> Any:
    """Approval callback for ACP sessions.

    ACP is a programmatic stdio protocol with no interactive approval
    channel, so a dangerous tool cannot wait for a human click. Deny by
    default with an actionable message; the operator can opt in explicitly
    with ``LUCKYD_ACP_AUTO_APPROVE=1`` or by configuring the Trust policy
    (mode ``auto`` or an ``allow`` rule for the tool's scope, both of which
    ApprovalHook applies before this callback runs).
    """
    from core.trust import scope_of
    from core.types import ToolApprovalResult

    if (os.environ.get("LUCKYD_ACP_AUTO_APPROVE", "") or "").strip().lower() in _TRUTHY:
        return ToolApprovalResult(
            approved=True,
            reason="ACP auto-approve enabled by LUCKYD_ACP_AUTO_APPROVE",
        )
    scope = scope_of(request.tool_name)
    return ToolApprovalResult(
        approved=False,
        reason=(
            f"ACP sessions have no interactive approval channel. Allow the "
            f"'{scope}' scope in the Trust policy, set trust mode to auto, or "
            f"set LUCKYD_ACP_AUTO_APPROVE=1 to permit tool calls."
        ),
    )


def _ensure_approval_hook(session_id: str) -> None:
    """Register exactly one ACP ApprovalHook for this process."""
    from core.approval_hook import ApprovalHook
    from core.hooks import get_hooks, register_plugin

    for hook in get_hooks().before_tool:
        target = getattr(hook, "__self__", hook)
        if isinstance(target, ApprovalHook) and target.session_id == session_id:
            return
    register_plugin(ApprovalHook(approval_callback=_acp_approval, session_id=session_id))


def _ok(result: Any, msg_id: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _err(code: int, message: str, msg_id: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


class AcpServer:
    """JSON-RPC-ish stdio server: {method, params} in, {result|error} out."""

    def __init__(self, stdin: Any = None, stdout: Any = None) -> None:
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self._agent: Any = None
        self._initialized = False

    def _get_agent(self) -> Any:
        if self._agent is None:
            from agent import CodingAgent

            self._agent = CodingAgent()
            # Without this hook the ACP entry point would be the one agent
            # surface that executes Write/Bash/Git with no approval gate.
            _ensure_approval_hook(getattr(self._agent, "conversation_id", "") or "acp")
        return self._agent

    def _emit(self, payload: dict[str, Any]) -> None:
        self.stdout.write(json.dumps(payload, default=str) + "\n")
        self.stdout.flush()

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Handle one decoded message; None means no response (notify)."""
        if not isinstance(message, dict):
            return _err(-32600, "invalid request: expected an object", None)
        method = message.get("method", "")
        params = message.get("params", {})
        if not isinstance(params, dict):
            params = {}
        msg_id = message.get("id")
        if method == "initialize":
            self._initialized = True
            return _ok(
                {
                    "agent": {"name": AGENT_NAME, "version": AGENT_VERSION},
                    "acpVersion": ACP_VERSION,
                    "capabilities": {"prompt": True, "goal": True, "steer": True},
                },
                msg_id,
            )
        if method == "shutdown":
            return _ok({"ok": True}, msg_id)
        if not self._initialized and method != "ping":
            return _err(-32002, "server not initialized (send initialize first)", msg_id)
        if method == "ping":
            return _ok({"ok": True}, msg_id)
        if method == "prompt":
            return self._on_prompt(params, msg_id)
        if method == "goal":
            return self._on_goal(params, msg_id)
        if method == "steer":
            return self._on_steer(params, msg_id)
        return _err(-32601, f"unknown method: {method!r}", msg_id)

    def _on_prompt(self, params: dict[str, Any], msg_id: Any) -> dict[str, Any]:
        text = str(params.get("text", "") or params.get("prompt", "") or "")
        if not text.strip():
            return _err(-32602, "prompt requires params.text", msg_id)
        try:
            agent = self._get_agent()
            result = agent.run(text)
            if asyncio.iscoroutine(result):
                result = asyncio.run(result)
            goal = None
            try:
                goal = agent.goals.describe()
            except Exception:
                goal = None
            return _ok({"text": str(result), "goal": goal}, msg_id)
        except Exception as exc:
            _log(f"prompt failed: {exc}")
            return _err(-32603, f"prompt failed: {exc}", msg_id)

    def _on_goal(self, params: dict[str, Any], msg_id: Any) -> dict[str, Any]:
        action = str(params.get("action", "status") or "status")
        value = str(params.get("value", "") or params.get("text", "") or "")
        try:
            agent = self._get_agent()
            store = getattr(agent, "goals", None)
            if store is None:
                return _err(-32603, "agent has no goal store", msg_id)
            if action == "status":
                text = store.describe()
            else:
                line = value if action in ("set", "edit", "budget") else ""
                text = store.handle_command(f"{action} {line}".strip())
            return _ok({"text": text}, msg_id)
        except Exception as exc:
            return _err(-32603, f"goal failed: {exc}", msg_id)

    def _on_steer(self, params: dict[str, Any], msg_id: Any) -> dict[str, Any]:
        text = str(params.get("text", "") or "")
        if not text.strip():
            return _err(-32602, "steer requires params.text", msg_id)
        try:
            agent = self._get_agent()
            fn = getattr(agent, "steer", None)
            if fn is None:
                return _err(-32603, "agent does not support steer", msg_id)
            return _ok({"text": str(fn(text))}, msg_id)
        except Exception as exc:
            return _err(-32603, f"steer failed: {exc}", msg_id)

    def serve_forever(self) -> int:
        """Read stdin lines, write stdout responses. Returns exit code."""
        _log(f"{AGENT_NAME} {AGENT_VERSION} ACP server on stdio")
        stream = self.stdin
        if hasattr(stream, "buffer"):
            try:
                stream = io.TextIOWrapper(stream.buffer, encoding="utf-8", errors="replace")
            except Exception:
                stream = self.stdin
        for raw in stream:
            line = raw.strip() if isinstance(raw, str) else ""
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                self._emit(_err(-32700, "parse error: expected one JSON object per line", None))
                continue
            try:
                response = self.handle(message)
            except Exception:
                _log(f"handler crashed:\n{traceback.format_exc()}")
                msg_id = message.get("id") if isinstance(message, dict) else None
                response = _err(-32603, "internal error", msg_id)
            if response is not None:
                try:
                    self._emit(response)
                except Exception as exc:
                    _log(f"write failed: {exc}")
                    return 1
            if isinstance(message, dict) and message.get("method") == "shutdown":
                return 0
        return 0


def main() -> int:
    """Entry point for `luckyd-code --acp`."""
    return AcpServer().serve_forever()


if __name__ == "__main__":
    raise SystemExit(main())
