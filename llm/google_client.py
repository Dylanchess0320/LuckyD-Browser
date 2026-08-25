"""Google Gemini API client with streaming."""

from __future__ import annotations

import json

import httpx

from . import LLMClient, LLMResult


class GoogleClient(LLMClient):
    """Google Gemini API client."""

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResult:
        url = f"{self.config.base_url}/models/{self.config.model}:generateContent?key={self.config.api_key}"
        body = self._to_google(messages, tools)

        timeout = httpx.Timeout(connect=15.0, read=120.0, write=15.0, pool=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=body)
            resp.raise_for_status()
            data = resp.json()

        text = ""
        tool_calls = []
        candidates = data.get("candidates", [])
        if candidates:
            content = candidates[0].get("content", {})
            for part in content.get("parts", []):
                if "text" in part:
                    text += part["text"]
                if "functionCall" in part:
                    fc = part["functionCall"]
                    tool_calls.append(
                        {
                            "id": fc.get("name", "call_1"),
                            "type": "function",
                            "function": {
                                "name": fc.get("name", ""),
                                "arguments": json.dumps(fc.get("args", {})),
                            },
                        }
                    )
        usage = {
            "input_tokens": data.get("usageMetadata", {}).get("promptTokenCount", 0),
            "output_tokens": data.get("usageMetadata", {}).get("candidatesTokenCount", 0),
        }
        self.cost_tracker.add_usage(usage, self.config.model)
        return LLMResult(
            content=text,
            tool_calls=tool_calls if tool_calls else None,
            model=self.config.model,
            usage=usage,
        )

    async def chat_stream(self, messages, tools=None, on_token=None, on_think=None) -> LLMResult:
        url = f"{self.config.base_url}/models/{self.config.model}:streamGenerateContent?key={self.config.api_key}&alt=sse"
        body = self._to_google(messages, tools)

        result = LLMResult(model=self.config.model)
        content_buf = ""
        tool_calls: list[dict] = []

        timeout = httpx.Timeout(connect=15.0, read=300.0, write=15.0, pool=5.0)
        async with (
            httpx.AsyncClient(timeout=timeout) as client,
            client.stream("POST", url, json=body) as resp,
        ):
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        for part in parts:
                            text = part.get("text", "")
                            if text:
                                content_buf += text
                                if on_token:
                                    on_token(text)
                            # BUG FIX: streaming previously only read `text` parts
                            # and silently dropped `functionCall` parts, so Gemini
                            # could never invoke a tool (Bash/Read/Write/etc.) in
                            # the normal streaming REPL path -- only in the
                            # unused non-streaming chat() method above.
                            if "functionCall" in part:
                                fc = part["functionCall"]
                                tool_calls.append(
                                    {
                                        "id": fc.get("name", f"call_{len(tool_calls) + 1}"),
                                        "type": "function",
                                        "function": {
                                            "name": fc.get("name", ""),
                                            "arguments": json.dumps(fc.get("args", {})),
                                        },
                                    }
                                )
                    usage = data.get("usageMetadata", {})
                    if usage:
                        self.cost_tracker.add_usage(usage, self.config.model)
                        result.usage = usage

        result.content = content_buf
        result.tool_calls = tool_calls if tool_calls else None
        return result

    def _to_google(self, messages: list[dict], tools=None) -> dict:
        """Convert to Google Gemini format."""
        contents = []
        system_instruction = ""
        for m in messages:
            role = m.get("role", "")
            content = m.get("content", "")
            if role == "system":
                system_instruction += content + "\n"
            elif role == "user":
                contents.append({"role": "user", "parts": [{"text": content}]})
            elif role == "assistant":
                contents.append({"role": "model", "parts": [{"text": content}]})
            elif role == "tool":
                contents.append(
                    {
                        "role": "function",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": m.get("name", ""),
                                    "response": {"content": content},
                                }
                            }
                        ],
                    }
                )
        body = {"contents": contents}
        if system_instruction:
            body["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        if tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t.get("function", t).get("name", ""),
                            "description": t.get("function", t).get("description", ""),
                            "parameters": t.get("function", t).get("parameters", {}),
                        }
                        for t in tools
                    ]
                }
            ]
        return body
