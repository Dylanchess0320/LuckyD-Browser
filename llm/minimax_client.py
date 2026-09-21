"""MiniMax API client with streaming.

MiniMax's API is Anthropic-compatible: POST {base}/v1/messages with the
``x-api-key`` and ``anthropic-version: 2023-06-01`` headers, using the
Anthropic Messages request/response schema (system extraction, tool_use /
tool_result content blocks). This client mirrors llm/anthropic_client.py.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from . import LLMClient, LLMConfig, LLMResult


class MiniMaxClient(LLMClient):
    """MiniMax API client (Anthropic-compatible Messages API)."""

    DEFAULT_BASE_URL = "https://api.minimax.io/anthropic"
    DEFAULT_MODEL = "MiniMax-M3"

    def __init__(
        self,
        config: LLMConfig | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ):
        if config is None:
            config = LLMConfig(
                api_key=api_key or os.environ.get("MINIMAX_API_KEY", ""),
                base_url=base_url or os.environ.get("MINIMAX_BASE_URL", self.DEFAULT_BASE_URL),
                model=model or os.environ.get("MINIMAX_MODEL", self.DEFAULT_MODEL),
                provider="minimax",
            )
        super().__init__(config)

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResult:
        headers = {
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        system, claude_msgs = self._to_claude(messages)
        body = {
            "model": self.config.model,
            "messages": claude_msgs,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = self._convert_tools(tools)

        timeout = httpx.Timeout(connect=15.0, read=120.0, write=15.0, pool=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{self.config.base_url}/v1/messages",
                headers=headers,
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()

        content_blocks = data.get("content", [])
        text = ""
        tool_calls = []
        for block in content_blocks:
            if block.get("type") == "text":
                text += block.get("text", "")
            elif block.get("type") == "tool_use":
                tc = {
                    "id": block.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {})),
                    },
                }
                tool_calls.append(tc)

        usage = {
            "input_tokens": data.get("usage", {}).get("input_tokens", 0),
            "output_tokens": data.get("usage", {}).get("output_tokens", 0),
        }
        self.cost_tracker.add_usage(usage, self.config.model)
        return LLMResult(
            content=text,
            tool_calls=tool_calls if tool_calls else None,
            model=data.get("model", self.config.model),
            usage=usage,
            finish_reason=data.get("stop_reason", ""),
        )

    async def chat_stream(self, messages, tools=None, on_token=None, on_think=None) -> LLMResult:
        headers = {
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        system, claude_msgs = self._to_claude(messages)
        body = {
            "model": self.config.model,
            "messages": claude_msgs,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "stream": True,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = self._convert_tools(tools)

        result = LLMResult(model=self.config.model)
        content_buf = ""
        tool_calls = []

        timeout = httpx.Timeout(connect=15.0, read=300.0, write=15.0, pool=5.0)
        async with (
            httpx.AsyncClient(timeout=timeout) as client,
            client.stream(
                "POST",
                f"{self.config.base_url}/v1/messages",
                headers=headers,
                json=body,
            ) as resp,
        ):
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = json.loads(line[6:])
                etype = data.get("type", "")
                if etype == "content_block_delta":
                    delta = data.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        content_buf += text
                        if on_token:
                            on_token(text)
                elif etype == "content_block_start":
                    block = data.get("content_block", {})
                    if block.get("type") == "tool_use":
                        tool_calls.append(
                            {
                                "id": block.get("id", ""),
                                "type": "function",
                                "function": {"name": block.get("name", ""), "arguments": ""},
                            }
                        )
                elif etype == "message_delta":
                    delta = data.get("delta", {})
                    if delta.get("stop_reason"):
                        result.finish_reason = delta["stop_reason"]
                    usage = data.get("usage", {})
                    if usage:
                        self.cost_tracker.add_usage(usage, self.config.model)
                        result.usage = usage
                elif etype == "message_stop":
                    break

        result.content = content_buf
        result.tool_calls = tool_calls if tool_calls else None
        return result

    def _to_claude(self, messages: list[dict]) -> tuple[str, list[dict]]:
        """Convert OpenAI-format messages to Claude format."""
        system = ""
        claude_msgs = []
        for m in messages:
            role = m.get("role", "")
            content = m.get("content", "")
            if role == "system":
                system += content + "\n"
            elif role == "tool":
                claude_msgs.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m.get("tool_call_id", ""),
                                "content": content or "ok",
                            }
                        ],
                    }
                )
            elif role == "assistant":
                msg: dict[str, Any] = {"role": "assistant", "content": []}
                if content:
                    msg["content"].append({"type": "text", "text": content})
                for tc in m.get("tool_calls", []):
                    items = tc if isinstance(tc, list) else [tc]
                    for call in items:
                        if not isinstance(call, dict) or "function" not in call:
                            continue
                        fn = call.get("function", {})
                        msg["content"].append(
                            {
                                "type": "tool_use",
                                "id": call.get("id", ""),
                                "name": fn.get("name", ""),
                                "input": json.loads(fn.get("arguments", "{}")),
                            }
                        )
                claude_msgs.append(msg)
            else:
                claude_msgs.append({"role": role, "content": content})
        return system.strip(), claude_msgs

    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        """Convert OpenAI tool format to Anthropic format."""
        claude_tools = []
        for t in tools:
            fn = t.get("function", t)
            claude_tools.append(
                {
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {}),
                }
            )
        return claude_tools
