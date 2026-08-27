"""
LLM client wrapper — extracted from agent.py.
Handles HTTP transport, streaming, retries, and provider routing.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable

import httpx

from .context_manager import ContextManager

# Models whose thinking mode makes function-call thought signatures mandatory
_GEMINI_THINKING_RE = re.compile(r"gemini-[3-9]|thinking", re.IGNORECASE)

# Whitelist of JSON-schema keys Gemini function declarations accept
_SCHEMA_KEYS = {
    "type",
    "description",
    "enum",
    "items",
    "properties",
    "required",
    "format",
    "nullable",
    "minimum",
    "maximum",
}


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
        token_resolver: Callable[[], str | None] | None = None,
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
        # Optional callable that returns a fresh bearer token before each
        # request. Wired in for providers whose auth is backed by a live
        # session (e.g. ClinePass -> Cline CLI WorkOS token) so that a token
        # refreshed since startup actually gets used instead of the stale one.
        self.token_resolver = token_resolver

    def _auth_token(self) -> str:
        """Resolve the bearer token to use for this request."""
        if self.token_resolver is not None:
            try:
                fresh = (self.token_resolver() or "").strip()
                if fresh:
                    self.api_key = fresh
            except Exception:
                pass  # fall back to whatever key we already have
        return (self.api_key or "").strip()

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

    # ── Native Gemini transport ────────────────────────────────────────
    # The OpenAI-compat shim at /v1beta/openai drops function-call
    # thoughtSignature fields when history is replayed, which Gemini 3+
    # thinking models reject with HTTP 400. When the base URL points at the
    # native generativelanguage API we speak its format directly and round-
    # trip signatures via the "_gcalls" sidecar on assistant messages.

    def _is_google(self) -> bool:
        return "generativelanguage.googleapis.com" in (self.base_url or "")

    @classmethod
    def _clean_schema(cls, schema):
        """Strip JSON-schema keys Gemini doesn't accept."""
        if isinstance(schema, list):
            return [cls._clean_schema(s) for s in schema]
        if not isinstance(schema, dict):
            return schema
        out = {}
        for k, v in schema.items():
            if k not in _SCHEMA_KEYS:
                continue
            if k == "properties" and isinstance(v, dict):
                out[k] = {pk: cls._clean_schema(pv) for pk, pv in v.items()}
            elif k == "items":
                out[k] = cls._clean_schema(v)
            else:
                out[k] = v
        return out

    def _google_body(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """Convert OpenAI-style message history to native Gemini contents."""
        # Map tool_call_id -> function name so tool results can name their call
        id_to_name: dict[str, str] = {}
        for m in messages:
            for i, tc in enumerate(m.get("tool_calls") or []):
                f = tc.get("function", {})
                id_to_name[tc.get("id") or f"call_{i}"] = f.get("name", "")

        sys_texts: list[str] = []
        contents: list[dict] = []
        pending_responses: list[dict] = []

        def flush():
            nonlocal pending_responses
            if pending_responses:
                contents.append({"role": "user", "parts": pending_responses})
                pending_responses = []

        for m in messages:
            role = m.get("role")
            if role == "system":
                sys_texts.append(m.get("content", ""))
            elif role == "user":
                flush()
                contents.append({"role": "user", "parts": [{"text": m.get("content", "")}]})
            elif role == "assistant":
                flush()
                content = m.get("content") or ""
                gcalls = m.get("_gcalls")
                tcs = m.get("tool_calls") or []
                parts: list[dict] = []
                if content:
                    parts.append({"text": content})
                if gcalls:
                    for g in gcalls:
                        part = {"functionCall": {"name": g["name"], "args": g["args"]}}
                        if g.get("sig"):
                            part["thoughtSignature"] = g["sig"]
                        parts.append(part)
                elif tcs:
                    if _GEMINI_THINKING_RE.search(self.model or ""):
                        # Legacy history without signatures: thinking models
                        # reject bare functionCalls, so degrade to text.
                        lines = [content] if content else []
                        for tc in tcs:
                            f = tc.get("function", {})
                            lines.append(f"[Called tool {f.get('name')}({f.get('arguments', '')})]")
                        parts = [{"text": "\n".join(lines)}]
                    else:
                        for tc in tcs:
                            f = tc.get("function", {})
                            try:
                                args = json.loads(f.get("arguments") or "{}")
                            except json.JSONDecodeError:
                                args = {}
                            parts.append(
                                {"functionCall": {"name": f.get("name", ""), "args": args}}
                            )
                if parts:
                    contents.append({"role": "model", "parts": parts})
            elif role == "tool":
                name = m.get("name") or id_to_name.get(m.get("tool_call_id"), "tool")
                pending_responses.append(
                    {
                        "functionResponse": {
                            "name": name,
                            "response": {"result": m.get("content", "")},
                        }
                    }
                )
        flush()

        body: dict = {
            "contents": contents,
            "generationConfig": {
                "temperature": self.temperature,
                "maxOutputTokens": self.max_tokens,
            },
        }
        if _GEMINI_THINKING_RE.search(self.model or ""):
            body["generationConfig"]["maxOutputTokens"] = max(self.max_tokens, 32768)
        if sys_texts:
            body["systemInstruction"] = {"parts": [{"text": "\n\n".join(sys_texts)}]}
        if tools:
            decls = []
            for t in tools:
                f = t.get("function", t)
                params = f.get("parameters") or {"type": "object", "properties": {}}
                decls.append(
                    {
                        "name": f.get("name", ""),
                        "description": f.get("description", ""),
                        "parameters": self._clean_schema(params),
                    }
                )
            body["tools"] = [{"functionDeclarations": decls}]
        return body

    async def _google_chat(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        stream_callback=None,
        think_callback=None,
        stream: bool = True,
    ) -> dict | None:
        key = self._auth_token()
        base = (self.base_url or "").rstrip("/")
        verb = "streamGenerateContent?alt=sse&key=" if stream else "generateContent?key="
        url = f"{base}/models/{self.model}:{verb}{key}"
        body = self._google_body(messages, tools)

        for attempt in range(self.max_retries + 1):
            try:
                timeout = httpx.Timeout(connect=30.0, read=self.timeout_sec, write=30.0, pool=30.0)
                text_chunks: list[str] = []
                think_chunks: list[str] = []
                calls: list[dict] = []
                usage: dict = {}
                finish = ""
                async with httpx.AsyncClient(timeout=timeout) as client:
                    if stream:
                        async with client.stream("POST", url, json=body) as resp:
                            if resp.status_code >= 400:
                                await resp.aread()
                            resp.raise_for_status()
                            async for line in resp.aiter_lines():
                                if not line.startswith("data: "):
                                    continue
                                try:
                                    chunk = json.loads(line[6:])
                                except json.JSONDecodeError:
                                    continue
                                (
                                    text_chunks,
                                    think_chunks,
                                    calls,
                                    usage,
                                    finish,
                                ) = self._google_ingest_chunk(
                                    chunk,
                                    text_chunks,
                                    think_chunks,
                                    calls,
                                    usage,
                                    finish,
                                    stream_callback,
                                    think_callback,
                                )
                    else:
                        resp = await client.post(url, json=body)
                        resp.raise_for_status()
                        data = resp.json()
                        um = data.get("usageMetadata") or {}
                        usage = {
                            "prompt_tokens": um.get("promptTokenCount", 0),
                            "completion_tokens": um.get("candidatesTokenCount", 0),
                        }
                        cands = data.get("candidates") or []
                        if cands:
                            fr = cands[0].get("finishReason")
                            if fr:
                                finish = {"STOP": "stop", "MAX_TOKENS": "length"}.get(
                                    fr, fr.lower()
                                )
                            for part in (cands[0].get("content") or {}).get("parts", []):
                                fc = part.get("functionCall")
                                if fc:
                                    calls.append(
                                        {
                                            "name": fc.get("name", ""),
                                            "args": fc.get("args") or {},
                                            "sig": part.get("thoughtSignature", ""),
                                        }
                                    )
                                    continue
                                t = part.get("text") or ""
                                if not t:
                                    continue
                                if part.get("thought"):
                                    think_chunks.append(t)
                                    if think_callback:
                                        think_callback(t)
                                else:
                                    text_chunks.append(t)
                                    if stream_callback:
                                        stream_callback(t)

                msg: dict = {"role": "assistant", "content": "".join(text_chunks)}
                if think_chunks:
                    msg["reasoning_content"] = "".join(think_chunks)
                if calls:
                    msg["_gcalls"] = calls
                    msg["tool_calls"] = [
                        {
                            "id": f"call_{i}",
                            "type": "function",
                            "function": {
                                "name": c["name"],
                                "arguments": json.dumps(c["args"]),
                            },
                        }
                        for i, c in enumerate(calls)
                    ]
                if usage:
                    msg["_usage"] = usage
                if finish:
                    msg["_finish_reason"] = finish
                return msg
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                detail = self._extract_error_detail(e.response)
                if code in self.retryable_codes and attempt < self.max_retries:
                    delay = self._retry_delay(e.response, attempt)
                    print(
                        f"\n  [RETRY] HTTP {code} in {delay:.1f}s "
                        f"({attempt + 2}/{self.max_retries + 1})"
                    )
                    await asyncio.sleep(delay)
                    continue
                print(f"\n  [ERR] API Error ({code}): {detail}")
                return {
                    "role": "assistant",
                    "content": f"[API Error: {code}] {detail}",
                }
            except (
                httpx.ReadError,
                httpx.RemoteProtocolError,
                httpx.ConnectError,
                httpx.ReadTimeout,
                httpx.ConnectTimeout,
            ) as e:
                if attempt < self.max_retries:
                    delay = self._retry_delay(None, attempt)
                    print(f"\n  [RETRY] {type(e).__name__}: {e} in {delay:.1f}s")
                    await asyncio.sleep(delay)
                    continue
                if stream:
                    print("\n  [WARN] Streaming failed — falling back to non-streaming")
                    return await self._google_chat(
                        messages, tools, None, think_callback, stream=False
                    )
                print(f"\n  [ERR] Non-streaming fallback also failed: {e}")
                return None
        return None

    def _google_ingest_chunk(
        self, chunk, text_chunks, think_chunks, calls, usage, finish, scb, tcb
    ):
        """Fold one SSE chunk into running buffers; returns the updated tuple."""
        um = chunk.get("usageMetadata") or {}
        if um:
            usage = {
                "prompt_tokens": um.get("promptTokenCount", 0),
                "completion_tokens": um.get("candidatesTokenCount", 0),
                "total_tokens": um.get("totalTokenCount", 0),
            }
        cands = chunk.get("candidates") or []
        if not cands:
            return (text_chunks, think_chunks, calls, usage, finish)
        fr = cands[0].get("finishReason")
        if fr:
            finish = {"STOP": "stop", "MAX_TOKENS": "length"}.get(fr, fr.lower())
        for part in (cands[0].get("content") or {}).get("parts", []):
            fc = part.get("functionCall")
            if fc:
                calls.append(
                    {
                        "name": fc.get("name", ""),
                        "args": fc.get("args") or {},
                        "sig": part.get("thoughtSignature", ""),
                    }
                )
                continue
            t = part.get("text") or ""
            if not t:
                continue
            if part.get("thought"):
                think_chunks.append(t)
                if tcb:
                    tcb(t)
            else:
                text_chunks.append(t)
                if scb:
                    scb(t)
        return (text_chunks, think_chunks, calls, usage, finish)

    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        stream_callback: Callable[[str], None] | None = None,
        think_callback: Callable[[str], None] | None = None,
    ) -> dict | None:
        """Streaming LLM call. Returns the assembled assistant message."""
        if self._is_google():
            return await self._google_chat(messages, tools, stream_callback, think_callback)
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        _k = self._auth_token()
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
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        # Omit stream_options on free / non-standard OpenAI endpoints to prevent HTTP 400
        if stream and not (self.model.endswith("-free") or "free" in self.model.lower()):
            payload["stream_options"] = {"include_usage": True}
        if ("pro" in self.model.lower() or "reasoner" in self.model.lower()) and not (
            self.model.endswith("-free") or "free" in self.model.lower()
        ):
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
            for tc in delta.get("tool_calls") or []:
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
        if self._is_google():
            return await self._google_chat(
                messages, tools, stream_callback, think_callback, stream=False
            )
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        _k = self._auth_token()
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
