"""
LLM client wrapper — extracted from agent.py.
Handles HTTP transport, streaming, retries, and provider routing.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import httpx

from .context_manager import ContextManager


class LLMClient:
    """Handles LLM API calls with streaming, retry, and backoff.

    Extracted from agent.py's _call_llm, _call_llm_nonstreaming, and _post_with_retry.
    Uses ContextManager for message compaction.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 8192,
        timeout_sec: int = 120,
        max_retries: int = 3,
        base_delay: float = 1.0,
        context_manager: ContextManager | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_sec = timeout_sec
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.retryable_codes = {429, 500, 502, 503, 504}
        self.context_manager = context_manager or ContextManager()

    def _retry_delay(self, resp, attempt: int) -> float:
        """Seconds to wait before retrying a retryable HTTP error.

        Honors the server's ``Retry-After`` header (delta-seconds or HTTP
        date) when present — essential for 429 rate limits, where a fixed
        short backoff retries too soon and burns all attempts. Falls back to
        exponential backoff, capped so a single retry never hangs too long.
        """
        wait = 0.0
        if resp is not None:
            try:
                ra = (resp.headers.get("retry-after") or "").strip()
            except Exception:
                ra = ""
            if ra:
                try:
                    wait = float(ra)  # delta-seconds form
                except ValueError:
                    try:
                        from datetime import datetime, timezone
                        from email.utils import parsedate_to_datetime

                        dt = parsedate_to_datetime(ra)
                        if dt is not None:
                            wait = max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
                    except Exception:
                        wait = 0.0
        backoff = self.base_delay * (2**attempt)
        return min(max(wait, backoff), 30.0)

    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        stream_callback: Callable[[str], None] | None = None,
        think_callback: Callable[[str], None] | None = None,
    ) -> dict | None:
        """Streaming LLM call. Returns the assembled assistant message."""
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        _k = (self.api_key or "").strip()
        if _k:
            headers["Authorization"] = f"Bearer {_k}"

        messages = await self.context_manager.compact(messages)
        payload = self._build_payload(messages, tools, stream=True)
        self._log_payload_size(payload)

        for attempt in range(self.max_retries + 1):
            try:
                return await self._try_stream(
                    payload, url, headers, attempt, stream_callback, think_callback
                )
            except httpx.HTTPStatusError as e:
                result = self._handle_http_error(e, attempt)
                if result is not None:
                    return result
            except (
                httpx.ReadError,
                httpx.RemoteProtocolError,
                httpx.ConnectError,
                httpx.ReadTimeout,
                httpx.ConnectTimeout,
            ) as e:
                if attempt < self.max_retries:
                    delay = self._retry_delay(None, attempt)
                    print(
                        f"\n  [RETRY] {type(e).__name__}: {e} in {delay:.1f}s ({attempt + 2}/{self.max_retries + 1})"
                    )
                    await asyncio.sleep(delay)
                else:
                    print(f"\n  [WARN] Streaming failed after {self.max_retries + 1} attempts")
                    return await self.chat_nonstreaming(
                        messages, tools, stream_callback, think_callback
                    )

        return await self.chat_nonstreaming(messages, tools, stream_callback, think_callback)

    def _build_payload(
        self, messages: list[dict], tools: list[dict] | None = None, stream: bool = True
    ) -> dict:
        """Build the API request payload."""
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "tools": tools or [],
            "tool_choice": "auto",
            "stream": stream,
        }
        if stream:
            payload["stream_options"] = {"include_usage": True}
        if "pro" in self.model.lower() or "reasoner" in self.model.lower():
            payload["thinking"] = {"type": "enabled"}
            payload["reasoning_effort"] = "high"
        return payload

    @staticmethod
    def _log_payload_size(payload: dict):
        import os

        if not os.environ.get("CODING_AGENT_DEBUG"):
            return
        msg_bytes = sum(len(json.dumps(m, ensure_ascii=False)) for m in payload.get("messages", []))
        tools_bytes = sum(len(json.dumps(t, ensure_ascii=False)) for t in payload.get("tools", []))
        total_bytes = sum(
            len(json.dumps(v, ensure_ascii=False)) for v in payload.values() if v is not None
        )
        print(
            f"\n  [DBG] payload: msgs={msg_bytes:,}B  tools={tools_bytes:,}B  total={total_bytes:,}B"
        )

    async def _try_stream(
        self, payload, url, headers, attempt, stream_callback, think_callback
    ) -> dict | None:
        """Execute one streaming attempt."""
        timeout = httpx.Timeout(connect=30.0, read=self.timeout_sec, write=30.0, pool=30.0)
        async with (
            httpx.AsyncClient(timeout=timeout) as client,
            client.stream("POST", url, headers=headers, json=payload) as resp,
        ):
            if resp.status_code in self.retryable_codes and attempt < self.max_retries:
                delay = self._retry_delay(resp, attempt)
                print(
                    f"\n  [RETRY] HTTP {resp.status_code} in {delay:.1f}s ({attempt + 2}/{self.max_retries + 1})"
                )
                await asyncio.sleep(delay)
                raise httpx.HTTPStatusError(
                    f"Retryable {resp.status_code}", request=resp.request, response=resp
                )
            if resp.status_code >= 400:
                await resp.aread()
            resp.raise_for_status()
            return await self._read_stream(resp, stream_callback, think_callback)

    async def _read_stream(self, resp, stream_callback, think_callback) -> dict | None:
        """Read and parse a streaming response, capturing usage."""
        content_chunks: list[str] = []
        reasoning_chunks: list[str] = []
        tool_calls_map: dict[int, dict] = {}
        usage: dict = {}
        finish_reason: str = ""
        async for line in resp.aiter_lines():
            if not line or not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            # Capture usage from any chunk (final chunk with include_usage)
            if chunk.get("usage"):
                usage = chunk["usage"]
            choices = chunk.get("choices", [])
            if not choices:
                continue
            choice0 = choices[0]
            # Capture finish_reason when present (e.g. "stop", "length", "tool_calls")
            fr = choice0.get("finish_reason")
            if fr:
                finish_reason = fr
            delta = choice0.get("delta", {})
            think_token = delta.get("reasoning_content", "")
            if think_token:
                reasoning_chunks.append(think_token)
                if think_callback:
                    think_callback(think_token)
            token = delta.get("content", "")
            if token:
                content_chunks.append(token)
                if stream_callback:
                    stream_callback(token)
            for tc in delta.get("tool_calls", []):
                idx = tc.get("index", 0)
                if idx not in tool_calls_map:
                    tool_calls_map[idx] = {"id": "", "name": "", "arguments": ""}
                if tc.get("id"):
                    tool_calls_map[idx]["id"] = tc["id"]
                func = tc.get("function", {})
                if "name" in func:
                    tool_calls_map[idx]["name"] += func["name"]
                if "arguments" in func:
                    tool_calls_map[idx]["arguments"] += func["arguments"]
        msg = self._assemble_message(content_chunks, reasoning_chunks, tool_calls_map)
        if usage:
            msg["_usage"] = usage
        if finish_reason:
            msg["_finish_reason"] = finish_reason
        return msg

    @staticmethod
    def _assemble_message(content_chunks, reasoning_chunks, tool_calls_map) -> dict:
        content = "".join(content_chunks)
        reasoning = "".join(reasoning_chunks)
        message: dict = {"role": "assistant", "content": content}
        if reasoning:
            message["reasoning_content"] = reasoning
        if tool_calls_map:
            message["tool_calls"] = []
            for idx in sorted(tool_calls_map.keys()):
                tc = tool_calls_map[idx]
                message["tool_calls"].append(
                    {
                        "id": tc["id"] or f"call_{idx}",
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    }
                )
        return message

    @staticmethod
    def _extract_error_detail(resp) -> str:
        """Best-effort human-readable message from a provider error response.

        Providers disagree on shape: OpenAI uses {"error":{"message","code"}},
        others {"message"}/{"detail"}/{"error":"..."}. Returns "" when nothing
        useful is found so callers can fall back to the raw status code.
        """
        try:
            data = resp.json()
        except Exception:
            try:
                return (resp.text or "").strip()[:300]
            except Exception:
                return ""
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict):
                msg = str(err.get("message") or "").strip()
                ecode = str(err.get("code") or "").strip()
                if msg and ecode:
                    return f"{ecode}: {msg}"[:300]
                if msg or ecode:
                    return (msg or ecode)[:300]
            if isinstance(err, str) and err.strip():
                return err.strip()[:300]
            for k in ("message", "detail", "error_description"):
                v = data.get(k)
                if v and str(v).strip():
                    return str(v).strip()[:300]
        try:
            return (resp.text or "").strip()[:300]
        except Exception:
            return ""

    def _handle_http_error(self, e: httpx.HTTPStatusError, attempt: int) -> dict | None:
        if e.response.status_code in self.retryable_codes and attempt < self.max_retries:
            delay = self._retry_delay(e.response, attempt)
            print(
                f"\n  [RETRY] HTTP {e.response.status_code} in {delay:.1f}s ({attempt + 2}/{self.max_retries + 1})"
            )
            return None
        code = e.response.status_code
        detail = self._extract_error_detail(e.response)
        try:
            err_text = e.response.text[:500]
        except Exception:
            err_text = "(unknown - response not read)"
        print(f"\n  [ERR] API Error ({code}): {detail or err_text}")
        if code in (401, 403):
            if "cline.bot" in (self.base_url or "") and not detail:
                # Bare 401 from api.cline.bot almost always means the Cline
                # session token was rejected/expired (often because
                # fresh_token() failed silently upstream in providers.py).
                why = "The Cline session token was rejected or has expired."
                hint = (
                    "Run `cline` (or `cline auth`) to re-login, or set "
                    "CLINEPASS_API_KEY in .env."
                )
            else:
                why = detail or "The API key was rejected by the provider."
                hint = (
                    "Check the key in your repo .env, or switch to ClinePass "
                    "(CODING_AGENT_PROVIDER=clinepass) to use your logged-in Cline account."
                )
            return {
                "role": "assistant",
                "content": f"[API Error: {code} — authentication failed] {why} {hint}",
            }
        if code == 429:
            why = f"{detail} " if detail else ""
            return {
                "role": "assistant",
                "content": (
                    f"[API Error: 429 — rate limited] {why}"
                    "Wait and retry, or use /model to switch to a different model."
                ),
            }
        why = f" {detail}" if detail else ""
        return {"role": "assistant", "content": f"[API Error: {code}]{why}"}

    async def chat_nonstreaming(
        self, messages, tools=None, stream_callback=None, think_callback=None
    ) -> dict | None:
        """Non-streaming fallback"""
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        _k = (self.api_key or "").strip()
        if _k:
            headers["Authorization"] = f"Bearer {_k}"
        messages = await self.context_manager.compact(messages)
        payload = self._build_payload(messages, tools, stream=False)
        try:
            resp = await self._http_post(url, headers, payload)
            data = resp.json()
            # Some gateways (e.g. ClinePass) wrap the completion in {"data": {...}}.
            if "choices" not in data and isinstance(data.get("data"), dict):
                data = data["data"]
            choice = data.get("choices", [{}])[0]
            msg = choice.get("message", {})
            reasoning = msg.get("reasoning_content", "")
            if reasoning and think_callback:
                think_callback(reasoning)
            content = msg.get("content", "")
            if content and stream_callback:
                stream_callback(content)
            # Attach usage for cost tracking
            usage = data.get("usage", {})
            if usage:
                msg["_usage"] = usage
            fr = choice.get("finish_reason")
            if fr:
                msg["_finish_reason"] = fr
            return msg
        except Exception as e:
            print(f"\n  [ERR] Non-streaming fallback also failed: {e}")
            return None

    async def _http_post(self, url: str, headers: dict, payload: dict) -> httpx.Response:
        """HTTP POST with retry for non-streaming calls."""
        last_exc = None
        for attempt in range(self.max_retries + 1):
            try:
                timeout = httpx.Timeout(connect=30.0, read=self.timeout_sec, write=30.0, pool=30.0)
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(url, headers=headers, json=payload)
                    if resp.status_code in self.retryable_codes and attempt < self.max_retries:
                        delay = self._retry_delay(resp, attempt)
                        print(
                            f"\n  [RETRY] HTTP {resp.status_code} in {delay:.1f}s ({attempt + 2}/{self.max_retries + 1})"
                        )
                        await asyncio.sleep(delay)
                        continue
                    resp.raise_for_status()
                    return resp
            except (
                httpx.RemoteProtocolError,
                httpx.ConnectError,
                httpx.ReadTimeout,
                httpx.ConnectTimeout,
            ) as e:
                last_exc = e
                if attempt < self.max_retries:
                    delay = self._retry_delay(None, attempt)
                    print(
                        f"\n  [RETRY] Connection: {e} in {delay:.1f}s ({attempt + 2}/{self.max_retries + 1})"
                    )
                    await asyncio.sleep(delay)
                else:
                    raise
            except httpx.HTTPStatusError as e:
                if (
                    e.response.status_code not in self.retryable_codes
                    or attempt >= self.max_retries
                ):
                    print(
                        f"\n  [ERR] API Error ({e.response.status_code}): {e.response.text[:500]}"
                    )
                    raise
        if last_exc:
            raise last_exc
