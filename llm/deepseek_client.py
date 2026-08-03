"""DeepSeek API client with streaming and thinking support."""

from __future__ import annotations

import json

import httpx

from . import LLMClient, LLMResult


class DeepSeekClient(LLMClient):
    """DeepSeek API client with streaming."""

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResult:
        headers = {"Content-Type": "application/json"}
        _k = (self.config.api_key or "").strip()
        if _k:
            headers["Authorization"] = f"Bearer {_k}"
        body = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if tools:
            body["tools"] = tools
        if self.config.thinking:
            body["thinking"] = {"type": "enabled"}

        timeout = httpx.Timeout(connect=15.0, read=120.0, write=15.0, pool=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{self.config.base_url}/chat/completions",
                headers=headers,
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
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

    async def chat_stream(
        self,
        messages,
        tools=None,
        on_token=None,
        on_think=None,
    ) -> LLMResult:
        headers = {"Content-Type": "application/json"}
        _k = (self.config.api_key or "").strip()
        if _k:
            headers["Authorization"] = f"Bearer {_k}"
        body = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = tools
        if self.config.thinking:
            body["thinking"] = {"type": "enabled"}

        result = LLMResult(model=self.config.model)
        think_buf = ""
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

                # Thinking content (DeepSeek-specific)
                if "thinking" in delta:
                    think_text = delta["thinking"] or ""
                    think_buf += think_text
                    if on_think:
                        on_think(think_text)

                # Regular content
                content = delta.get("content", "")
                if content:
                    content_buf += content
                    if on_token:
                        on_token(content)

                # Tool calls
                if delta.get("tool_calls"):
                    if not result.tool_calls:
                        result.tool_calls = []
                    result.tool_calls.append(delta["tool_calls"])

                finish = choice.get("finish_reason", "")
                if finish:
                    result.finish_reason = finish

        result.content = content_buf
        result.thinking = think_buf
        return result
