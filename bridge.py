#!/usr/bin/env python3
"""
LuckyD Code — VSCode Bridge

JSON-RPC-style stdin/stdout bridge between the VSCode extension
and the Python coding agent. Handles one-shot prompts, streaming
responses, and tool execution with VSCode-provided context.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback
from pathlib import Path

# Ensure coding-agent is on path
AGENT_DIR = Path(__file__).parent
sys.path.insert(0, str(AGENT_DIR))

from agent import CodingAgent
from config import get_config
from memory.store import get_memory
from model_resolver import resolve_model

# Overridden by vscode-bridge.py (thin shim) to identify itself on the wire.
READY_MESSAGE = "LuckyD Code bridge ready"


async def run_agent(prompt: str, *, model: str = "auto", thinking: bool = False) -> dict:
    """Run the agent with the given prompt and return a result dict."""
    cfg = get_config()

    resolved_model = resolve_model(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        preferred=model,
        thinking=thinking,
    )

    agent = CodingAgent(model=resolved_model)

    try:
        response = await agent.run(prompt)
        return {
            "type": "response",
            "content": response,
            "model": resolved_model,
        }
    except Exception as e:
        return {
            "type": "error",
            "content": str(e),
            "traceback": traceback.format_exc(),
        }


async def run_agent_stream(prompt: str, *, model: str = "auto", thinking: bool = False):
    """Run the agent with streaming, yielding partial messages."""
    cfg = get_config()

    resolved_model = resolve_model(
        api_key=cfg["api_key"],
        base_url=cfg.get("base_url"),
        preferred=model,
        thinking=thinking,
    )

    agent = CodingAgent(model=resolved_model)

    try:
        async for chunk in agent.stream(prompt):
            yield json.dumps({"type": "chunk", "content": chunk})
    except Exception as e:
        yield json.dumps({"type": "error", "content": str(e), "traceback": traceback.format_exc()})


async def handle_request(request: dict) -> dict:
    """Handle a single JSON-RPC request."""
    method = request.get("method", "")
    params = request.get("params", {})
    req_id = request.get("id", "")

    try:
        if method == "chat":
            prompt = params.get("prompt", "")
            model = params.get("model", "auto")
            thinking = params.get("thinking", False)
            result = await run_agent(prompt, model=model, thinking=thinking)
            result["id"] = req_id
            return result

        elif method == "chat_stream":
            prompt = params.get("prompt", "")
            model = params.get("model", "auto")
            thinking = params.get("thinking", False)
            async for chunk_json in run_agent_stream(prompt, model=model, thinking=thinking):
                chunk = json.loads(chunk_json)
                chunk["id"] = req_id
                print(json.dumps(chunk), flush=True)
            return {"type": "done", "id": req_id}

        elif method == "get_context":
            memory = get_memory()
            cfg = get_config()
            return {
                "type": "context",
                "id": req_id,
                "content": {
                    "model": cfg.get("model", "auto"),
                    "cwd": os.getcwd(),
                    "memory_summary": memory.summarize() if memory else "",
                },
            }

        elif method == "reset":
            return {"type": "ok", "id": req_id, "content": "Agent reset"}

        else:
            return {"type": "error", "id": req_id, "content": f"Unknown method: {method}"}

    except Exception as e:
        return {
            "type": "error",
            "id": req_id,
            "content": str(e),
            "traceback": traceback.format_exc(),
        }


async def main():
    """Main loop: read JSON requests from stdin, write JSON responses to stdout."""
    print(json.dumps({"type": "ready", "content": READY_MESSAGE}), flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError as e:
            print(json.dumps({"type": "error", "content": f"Invalid JSON: {e}"}), flush=True)
            continue

        result = await handle_request(request)

        # For streaming, the handle_request already printed chunks
        if result.get("type") == "done":
            continue

        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
