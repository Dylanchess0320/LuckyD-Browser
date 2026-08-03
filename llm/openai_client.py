"""OpenAI API client with streaming support."""

from __future__ import annotations

import json

import httpx

from . import LLMClient, LLMResult


class OpenAIClient(LLMClient):
    """OpenAI API client with streaming."""

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResult:
        headers = {"Content-Type": "application/json"}
        _k = (self.config.api_key or "").strip()
        if _k:
            headers["Authorization"] = f"Bearer {_k}"
        body = {
            "model": self.config.model,
            "messages": self._fmt(messages),
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if tools:
            body["tools"] = tools

        timeout = httpx.Timeout(connect=15.0, read=120.0, write=15.0, pool=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{self.config.base_url}/chat/completions",
                headers=headers,
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
            # Some gateways (e.g. the ClinePass/OpenRouter-style aggregator) wrap
            # the standard chat-completion payload in a top-level envelope:
            # {"success": true, "data": {...}}. Unwrap it when present.
            if "choices" not in data and isinstance(data.get("data"), dict):
                data = data["data"]
            choice = data["choices"][0]
            msg = choice.get("message", {})
            self.cost_tracker.add_usage(data.get("usage", {}), self.config.model)
            return LLMResult(
                content=msg.get("content", "") or "",
                tool_calls=msg.get("tool_calls"),
                model=data.get("model", self.config.model),
                usage=data.get("usage", {}),
                finish_reason=choice.get("finish_reason", ""),
            )

    async def chat_stream(self, messages, tools=None, on_token=None, on_think=None) -> LLMResult:
        headers = {"Content-Type": "application/json"}
        _k = (self.config.api_key or "").strip()
        if _k:
            headers["Authorization"] = f"Bearer {_k}"
        body = {
            "model": self.config.model,
            "messages": self._fmt(messages),
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = tools

        result = LLMResult(model=self.config.model)
        content_buf = ""

        timeout = httpx.Timeout(connect=15.0, read=300.0, write=15.0, pool=5.0)
        async with (
            httpx.AsyncClient(timeout=timeout) as client,
            client.stream(
                "POST",
                f"{self.config.base_url}/chat/completions",
                headers=headers,
                json=body,
            ) as resp,
        ):
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if chunk.get("usage"):
                    self.cost_tracker.add_usage(chunk["usage"], self.config.model)
                    result.usage = chunk["usage"]
                choice = chunk.get("choices", [{}])[0]
                delta = choice.get("delta", {})
                content = delta.get("content", "")
                if content:
                    content_buf += content
                    if on_token:
                        on_token(content)
                if delta.get("tool_calls"):
                    if not result.tool_calls:
                        result.tool_calls = []
                    result.tool_calls.append(delta["tool_calls"])
                finish = choice.get("finish_reason", "")
                if finish:
                    result.finish_reason = finish

        result.content = content_buf
        return result

    def _fmt(self, messages: list[dict]) -> list[dict]:
        """Ensure OpenAI-compatible message format."""
        formatted = []
        for m in messages:
            role = m.get("role", "")
            content = m.get("content", "")
            msg = {"role": role}
            # OpenAI requires non-empty content for tool messages
            if role == "tool":
                msg["content"] = content or "ok"
                msg["tool_call_id"] = m.get("tool_call_id", "")
            elif role == "assistant" and m.get("tool_calls"):
                msg["content"] = content or None
                msg["tool_calls"] = m["tool_calls"]
            else:
                msg["content"] = content
            formatted.append(msg)
        return formatted
