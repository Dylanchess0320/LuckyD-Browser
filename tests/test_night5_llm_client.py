"""Night-5 tests: core/llm_client.py (was ~0% covered).

Request building, retry/backoff math, provider error shaping, native Gemini
body conversion, SSE chunk assembly, and streaming/non-streaming chat paths —
all with a stubbed httpx.AsyncClient, never touching the network.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from core.llm_client import LLMClient

# ── fixtures & fakes ─────────────────────────────────────────────────────


@pytest.fixture()
def client():
    return LLMClient(
        api_key="test-key",
        base_url="https://api.example.com",
        model="test-model",
        max_retries=2,
        base_delay=0.001,  # keep retry sleeps tiny
    )


class _FakeReq:
    def __init__(self, url):
        self.url = url


class FakeStreamResp:
    """Mimics httpx.Response for the stream path."""

    def __init__(
        self, lines, status=200, headers=None, url="https://api.example.com/chat/completions"
    ):
        self.status_code = status
        self.headers = headers or {}
        self._lines = lines
        self.request = httpx.Request("POST", url)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=self.request, response=self
            )


class FakePostResp:
    def __init__(self, payload, status=200, url="https://api.example.com/chat/completions"):
        self.status_code = status
        self._payload = payload
        self.request = httpx.Request("POST", url)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=self.request, response=self
            )

    def json(self):
        return self._payload

    @property
    def text(self):
        return json.dumps(self._payload)

    @property
    def headers(self):
        return {}


class FakeAsyncClient:
    """Captures the request; serves scripted responses."""

    instances: list = []
    script: list = []  # queued: ("stream", resp) / ("post", resp) / ("raise", exc)

    def __init__(self, *a, **k):
        FakeAsyncClient.instances.append(self)
        self.captured = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _next(self, kind):
        for i, (k, _item) in enumerate(FakeAsyncClient.script):
            if k == kind or k == "raise":
                return FakeAsyncClient.script.pop(i)
        raise AssertionError(f"no scripted {kind} left")

    async def post(self, url, headers=None, json=None):
        self.captured = {"url": url, "headers": headers or {}, "json": json or {}}
        kind, item = self._next("post")
        if kind == "raise":
            raise item
        return item

    def stream(self, method, url, headers=None, json=None):
        self.captured = {"url": url, "headers": headers or {}, "json": json or {}}
        kind, item = self._next("stream")
        if kind == "raise":
            raise item
        return item


@pytest.fixture(autouse=True)
def _reset_fake():
    FakeAsyncClient.instances.clear()
    FakeAsyncClient.script.clear()
    yield
    FakeAsyncClient.instances.clear()
    FakeAsyncClient.script.clear()


def _sse(*payloads):
    lines = []
    for p in payloads:
        lines.append("data: " + json.dumps(p))
    lines.append("data: [DONE]")
    return lines


def _chunk(token="", tool_calls=None, finish=None, usage=None):
    delta = {}
    if token:
        delta["content"] = token
    if tool_calls:
        delta["tool_calls"] = tool_calls
    c = {"choices": [{"delta": delta}]}
    if finish:
        c["choices"][0]["finish_reason"] = finish
    if usage:
        c["usage"] = usage
    return c


# ── payload building ─────────────────────────────────────────────────────


def test_build_payload_basic(client):
    msgs = [{"role": "user", "content": "hi"}]
    p = client._build_payload(msgs, stream=True)
    assert p["model"] == "test-model"
    assert p["messages"] == msgs
    assert p["temperature"] == 0.0
    assert p["stream"] is True
    assert p["stream_options"] == {"include_usage": True}
    assert "tools" not in p
    assert "thinking" not in p


def test_build_payload_with_tools(client):
    tools = [{"type": "function", "function": {"name": "Read", "parameters": {}}}]
    p = client._build_payload([{"role": "user", "content": "x"}], tools=tools, stream=False)
    assert p["tools"] == tools
    assert p["tool_choice"] == "auto"
    assert "stream_options" not in p


def test_build_payload_free_model_skips_stream_options():
    c = LLMClient(api_key="k", base_url="u", model="qwen-free", max_retries=1, base_delay=0.001)
    p = c._build_payload([], stream=True)
    assert "stream_options" not in p


def test_build_payload_pro_model_gets_thinking():
    c = LLMClient(api_key="k", base_url="u", model="claude-pro", max_retries=1, base_delay=0.001)
    p = c._build_payload([], stream=True)
    assert p["thinking"] == {"type": "enabled"}
    assert p["reasoning_effort"] == "high"


# ── auth token ───────────────────────────────────────────────────────────


def test_auth_token_uses_resolver():
    c = LLMClient(
        api_key="test-key",
        base_url="u",
        model="m",
        token_resolver=lambda: "fresh-token",
    )
    assert c._auth_token() == "fresh-token"
    assert c.api_key == "fresh-token"  # refreshed into the stored key


def test_auth_token_resolver_failure_falls_back():
    def boom():
        raise RuntimeError("nope")

    c = LLMClient(api_key="old", base_url="u", model="m", token_resolver=boom)
    assert c._auth_token() == "old"


def test_auth_token_empty_resolver_keeps_existing():
    c = LLMClient(api_key="  kept  ", base_url="u", model="m", token_resolver=lambda: "")
    assert c._auth_token() == "kept"


# ── retry delay ──────────────────────────────────────────────────────────


def test_retry_delay_retry_after_seconds(client):
    resp = FakeStreamResp([], status=429, headers={"retry-after": "7"})
    d = client._retry_delay(resp, 0)
    assert 7.0 <= d <= 30.0


def test_retry_delay_retry_after_http_date(client):
    from email.utils import formatdate

    future = formatdate(timeval=None, localtime=False, usegmt=True)
    resp = FakeStreamResp([], status=429, headers={"retry-after": future})
    d = client._retry_delay(resp, 0)
    assert 0.0 <= d <= 30.0


def test_retry_delay_falls_back_to_exponential(client):
    resp = FakeStreamResp([], status=500)
    assert client._retry_delay(resp, 0) == pytest.approx(0.001)
    assert client._retry_delay(resp, 3) == pytest.approx(0.008)
    assert client._retry_delay(resp, 20) == 30.0  # capped
    assert client._retry_delay(None, 0) == pytest.approx(0.001)


def test_retry_delay_retry_after_capped_at_30(client):
    resp = FakeStreamResp([], status=429, headers={"retry-after": "9999"})
    assert client._retry_delay(resp, 0) == 30.0


# ── misc pure helpers ────────────────────────────────────────────────────


def test_is_google():
    g = LLMClient(api_key="k", base_url="https://generativelanguage.googleapis.com/x", model="m")
    assert g._is_google() is True
    c = LLMClient(api_key="k", base_url="https://api.example.com", model="m")
    assert c._is_google() is False


def test_clean_schema_strips_unknown_keys():
    schema = {
        "type": "object",
        "title": "drop me",
        "properties": {
            "x": {"type": "string", "default": "drop", "enum": ["a", "b"]},
        },
        "items": [{"type": "integer", "extra": 1}],
    }
    out = LLMClient._clean_schema(schema)
    assert out == {
        "type": "object",
        "properties": {"x": {"type": "string", "enum": ["a", "b"]}},
        "items": [{"type": "integer"}],
    }


def test_assemble_message_merges_chunks_and_tool_calls():
    msg = LLMClient._assemble_message(
        ["Hel", "lo"],
        ["think"],
        {
            1: {"id": "c1", "name": "Write", "arguments": '{"a":1}'},
            0: {"id": "", "name": "Read", "arguments": "{}"},
        },
    )
    assert msg == {
        "role": "assistant",
        "content": "Hello",
        "reasoning_content": "think",
        "tool_calls": [
            {"id": "call_0", "type": "function", "function": {"name": "Read", "arguments": "{}"}},
            {"id": "c1", "type": "function", "function": {"name": "Write", "arguments": '{"a":1}'}},
        ],
    }


def test_assemble_message_no_tool_calls():
    msg = LLMClient._assemble_message(["hi"], [], {})
    assert msg == {"role": "assistant", "content": "hi"}


def test_extract_error_detail_shapes():
    r = FakePostResp({"error": {"message": "bad key", "code": "invalid_key"}})
    assert LLMClient._extract_error_detail(r) == "invalid_key: bad key"
    r = FakePostResp({"error": {"message": "oops"}})
    assert LLMClient._extract_error_detail(r) == "oops"
    r = FakePostResp({"error": "plain string error"})
    assert LLMClient._extract_error_detail(r) == "plain string error"
    r = FakePostResp({"detail": "not found"})
    assert LLMClient._extract_error_detail(r) == "not found"
    r = FakePostResp({})
    assert LLMClient._extract_error_detail(r) == "{}"


def test_extract_error_detail_unparseable():
    class Weird:
        def json(self):
            raise ValueError("no")

        @property
        def text(self):
            raise RuntimeError("no")

    assert LLMClient._extract_error_detail(Weird()) == ""


def test_handle_http_error_retryable_returns_none(client):
    req = httpx.Request("POST", "https://api.example.com/x")
    resp = httpx.Response(503, request=req)
    e = httpx.HTTPStatusError("x", request=req, response=resp)
    assert client._handle_http_error(e, attempt=0) is None


def test_handle_http_error_auth_generic(client):
    req = httpx.Request("POST", "https://api.example.com/x")
    resp = httpx.Response(401, request=req, content=b"")
    e = httpx.HTTPStatusError("x", request=req, response=resp)
    out = client._handle_http_error(e, attempt=3)
    assert out["role"] == "assistant"
    assert "authentication failed" in out["content"]
    assert "API key was rejected" in out["content"]


def test_handle_http_error_auth_with_detail(client):
    req = httpx.Request("POST", "https://api.example.com/x")
    resp = httpx.Response(401, request=req, json={"error": {"message": "bad key", "code": "auth"}})
    e = httpx.HTTPStatusError("x", request=req, response=resp)
    out = client._handle_http_error(e, attempt=3)
    assert "auth: bad key" in out["content"]


def test_handle_http_error_auth_cline_bare_401():
    c = LLMClient(
        api_key="k", base_url="https://api.cline.bot/x", model="m", max_retries=1, base_delay=0.001
    )
    req = httpx.Request("POST", "https://api.cline.bot/x")
    resp = httpx.Response(401, request=req, text="")
    e = httpx.HTTPStatusError("x", request=req, response=resp)
    out = c._handle_http_error(e, attempt=9)
    assert "Cline session token was rejected" in out["content"]


def test_handle_http_error_429(client):
    req = httpx.Request("POST", "https://api.example.com/x")
    resp = httpx.Response(429, request=req, json={"error": {"message": "slow down"}})
    e = httpx.HTTPStatusError("x", request=req, response=resp)
    out = client._handle_http_error(e, attempt=9)
    assert "rate limited" in out["content"]
    assert "slow down" in out["content"]


def test_handle_http_error_generic(client):
    req = httpx.Request("POST", "https://api.example.com/x")
    resp = httpx.Response(500, request=req, json={"error": {"message": "boom"}})
    e = httpx.HTTPStatusError("x", request=req, response=resp)
    out = client._handle_http_error(e, attempt=9)
    assert out["content"] == "[API Error: 500] boom"


# ── native Gemini body ───────────────────────────────────────────────────


@pytest.fixture()
def gclient():
    return LLMClient(
        api_key="k",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model="gemini-2.5-pro",
        max_retries=1,
        base_delay=0.001,
    )


def test_google_body_role_mapping(gclient):
    messages = [
        {"role": "system", "content": "be nice"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "function": {"name": "Read", "arguments": '{"f":"a"}'}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "file text"},
    ]
    body = gclient._google_body(messages)
    assert body["systemInstruction"] == {"parts": [{"text": "be nice"}]}
    roles = [c["role"] for c in body["contents"]]
    assert roles == ["user", "model", "model", "user"]
    fc = body["contents"][2]["parts"][0]["functionCall"]
    assert fc == {"name": "Read", "args": {"f": "a"}}
    fr = body["contents"][3]["parts"][0]["functionResponse"]
    assert fr["name"] == "Read"
    assert fr["response"] == {"result": "file text"}


def test_google_body_gcalls_signature_roundtrip(gclient):
    messages = [
        {
            "role": "assistant",
            "content": "ok",
            "_gcalls": [{"name": "Edit", "args": {"a": 1}, "sig": "sigbytes"}],
        }
    ]
    body = gclient._google_body(messages)
    part = body["contents"][0]["parts"][1]
    assert part["functionCall"] == {"name": "Edit", "args": {"a": 1}}
    assert part["thoughtSignature"] == "sigbytes"


def test_google_body_thinking_model_degrades_bare_function_calls():
    c = LLMClient(
        api_key="k",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model="gemini-3-pro-thinking",
        max_retries=1,
        base_delay=0.001,
    )
    messages = [
        {
            "role": "assistant",
            "content": "doing it",
            "tool_calls": [{"id": "c1", "function": {"name": "Read", "arguments": "{}"}}],
        }
    ]
    body = c._google_body(messages)
    parts = body["contents"][0]["parts"]
    assert len(parts) == 1 and "text" in parts[0]
    assert "[Called tool Read({})]" in parts[0]["text"]
    assert body["generationConfig"]["maxOutputTokens"] == 32768


def test_google_body_tool_declarations_cleaned(gclient):
    tools = [
        {
            "type": "function",
            "function": {
                "name": "Read",
                "description": "read a file",
                "parameters": {
                    "type": "object",
                    "title": "drop",
                    "properties": {"path": {"type": "string", "default": "drop"}},
                },
            },
        }
    ]
    body = gclient._google_body([{"role": "user", "content": "x"}], tools=tools)
    decls = body["tools"][0]["functionDeclarations"]
    assert decls[0]["parameters"] == {
        "type": "object",
        "properties": {"path": {"type": "string"}},
    }


def test_google_ingest_chunk(gclient):
    texts, thinks, calls, usage = [], [], [], {}
    finish = ""
    seen_text, seen_think = [], []
    text_c, think_c, calls, usage, finish = gclient._google_ingest_chunk(
        {
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 4,
                "totalTokenCount": 14,
            },
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {
                        "parts": [
                            {"text": "hello"},
                            {"text": "musing", "thought": True},
                            {
                                "functionCall": {"name": "Read", "args": {"f": "a"}},
                                "thoughtSignature": "sig",
                            },
                        ]
                    },
                }
            ],
        },
        texts,
        thinks,
        calls,
        usage,
        finish,
        seen_text.append,
        seen_think.append,
    )
    # accumulators get exactly one entry per part (production wiring keeps
    # callbacks separate from the accumulators)
    assert text_c == ["hello"]
    assert think_c == ["musing"]
    assert seen_text == ["hello"]
    assert seen_think == ["musing"]
    assert calls == [{"name": "Read", "args": {"f": "a"}, "sig": "sig"}]
    assert usage == {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}
    assert finish == "stop"


def test_google_ingest_chunk_no_candidates(gclient):
    out = gclient._google_ingest_chunk({}, [], [], [], {}, "", None, None)
    assert out[0] == [] and out[3] == {} and out[4] == ""


# ── streaming chat path ──────────────────────────────────────────────────


async def test_chat_stream_success_assembles_message(client):
    lines = _sse(
        _chunk("Hel"),
        _chunk(
            "lo",
            tool_calls=[
                {"index": 0, "id": "c9", "function": {"name": "Read", "arguments": '{"f":'}}
            ],
        ),
        _chunk("", tool_calls=[{"index": 0, "function": {"arguments": '"a"}'}}]),
        _chunk("", finish="tool_calls", usage={"prompt_tokens": 5, "completion_tokens": 3}),
    )
    FakeAsyncClient.script.append(("stream", FakeStreamResp(lines)))
    seen = []
    with patch("core.llm_client.httpx.AsyncClient", FakeAsyncClient):
        msg = await client.chat_stream(
            [{"role": "user", "content": "hi"}], stream_callback=seen.append
        )
    assert msg["content"] == "Hello"
    assert msg["tool_calls"][0]["id"] == "c9"
    assert msg["tool_calls"][0]["function"]["arguments"] == '{"f":"a"}'
    assert msg["_finish_reason"] == "tool_calls"
    assert msg["_usage"] == {"prompt_tokens": 5, "completion_tokens": 3}
    assert seen == ["Hel", "lo"]
    captured = FakeAsyncClient.instances[0].captured
    assert captured["url"] == "https://api.example.com/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["json"]["stream"] is True


async def test_chat_stream_no_auth_header_without_key():
    c = LLMClient(
        api_key="", base_url="https://api.example.com", model="m", max_retries=1, base_delay=0.001
    )
    FakeAsyncClient.script.append(("stream", FakeStreamResp(_sse(_chunk("ok")))))
    with patch("core.llm_client.httpx.AsyncClient", FakeAsyncClient):
        msg = await c.chat_stream([{"role": "user", "content": "hi"}])
    assert msg["content"] == "ok"
    assert "Authorization" not in FakeAsyncClient.instances[0].captured["headers"]


async def test_chat_stream_retries_429_then_succeeds(client):
    retry_resp = FakeStreamResp(_sse(), status=429, headers={"retry-after": "0"})
    ok_resp = FakeStreamResp(_sse(_chunk("recovered")))
    FakeAsyncClient.script.append(("stream", retry_resp))
    FakeAsyncClient.script.append(("stream", ok_resp))
    with patch("core.llm_client.httpx.AsyncClient", FakeAsyncClient):
        msg = await client.chat_stream([{"role": "user", "content": "hi"}])
    assert msg["content"] == "recovered"


async def test_chat_stream_401_returns_error_message(client):
    resp = FakeStreamResp(_sse(), status=401)
    FakeAsyncClient.script.append(("stream", resp))
    with patch("core.llm_client.httpx.AsyncClient", FakeAsyncClient):
        msg = await client.chat_stream([{"role": "user", "content": "hi"}])
    assert "authentication failed" in msg["content"]


async def test_chat_stream_think_and_reasoning_chunks(client):
    lines = _sse(
        {"choices": [{"delta": {"reasoning_content": "r1", "content": ""}}]},
        {"choices": [{"delta": {"reasoning_content": "r2"}}]},
        _chunk("done", finish="stop"),
    )
    FakeAsyncClient.script.append(("stream", FakeStreamResp(lines)))
    thinks = []
    with patch("core.llm_client.httpx.AsyncClient", FakeAsyncClient):
        msg = await client.chat_stream(
            [{"role": "user", "content": "hi"}], think_callback=thinks.append
        )
    assert msg["content"] == "done"
    assert msg["reasoning_content"] == "r1r2"
    assert thinks == ["r1", "r2"]


async def test_chat_stream_bad_json_line_skipped(client):
    lines = ["data: {not json", "not-a-data-line", "", *_sse(_chunk("fine"))[0:2], "data: [DONE]"]
    FakeAsyncClient.script.append(("stream", FakeStreamResp(lines)))
    with patch("core.llm_client.httpx.AsyncClient", FakeAsyncClient):
        msg = await client.chat_stream([{"role": "user", "content": "hi"}])
    assert msg["content"] == "fine"


async def test_chat_stream_connection_error_falls_back_to_nonstreaming(client):
    # max_retries=2 -> 3 failed streaming attempts, then the non-streaming fallback
    for _ in range(3):
        FakeAsyncClient.script.append(("raise", httpx.ConnectError("down")))
    FakeAsyncClient.script.append(
        (
            "post",
            FakePostResp(
                {
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "fallback ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1},
                }
            ),
        )
    )
    with patch("core.llm_client.httpx.AsyncClient", FakeAsyncClient):
        msg = await client.chat_stream([{"role": "user", "content": "hi"}])
    assert msg["content"] == "fallback ok"
    assert msg["_finish_reason"] == "stop"
    assert msg["_usage"] == {"prompt_tokens": 1}


# ── non-streaming path ───────────────────────────────────────────────────


async def test_chat_nonstreaming_success(client):
    payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "hello",
                    "reasoning_content": "why",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 2, "completion_tokens": 1},
    }
    FakeAsyncClient.script.append(("post", FakePostResp(payload)))
    thinks, streamed = [], []
    with patch("core.llm_client.httpx.AsyncClient", FakeAsyncClient):
        msg = await client.chat_nonstreaming(
            [{"role": "user", "content": "hi"}],
            stream_callback=streamed.append,
            think_callback=thinks.append,
        )
    assert msg["content"] == "hello"
    assert msg["_usage"]["prompt_tokens"] == 2
    assert msg["_finish_reason"] == "stop"
    assert thinks == ["why"]
    assert streamed == ["hello"]
    assert FakeAsyncClient.instances[0].captured["json"]["stream"] is False


async def test_chat_nonstreaming_unwraps_data_envelope(client):
    inner = {"choices": [{"message": {"role": "assistant", "content": "wrapped"}}]}
    FakeAsyncClient.script.append(("post", FakePostResp({"data": inner})))
    with patch("core.llm_client.httpx.AsyncClient", FakeAsyncClient):
        msg = await client.chat_nonstreaming([{"role": "user", "content": "hi"}])
    assert msg["content"] == "wrapped"


async def test_chat_nonstreaming_failure_returns_none(client):
    FakeAsyncClient.script.append(("raise", httpx.ConnectError("down")))
    FakeAsyncClient.script.append(("raise", httpx.ConnectError("down")))
    FakeAsyncClient.script.append(("raise", httpx.ConnectError("down")))
    with patch("core.llm_client.httpx.AsyncClient", FakeAsyncClient):
        msg = await client.chat_nonstreaming([{"role": "user", "content": "hi"}])
    assert msg is None


# ── _http_post retry behavior ────────────────────────────────────────────


async def test_http_post_retries_connection_then_succeeds(client):
    url = "https://api.example.com/chat/completions"
    FakeAsyncClient.script.append(("raise", httpx.ConnectError("flaky")))
    FakeAsyncClient.script.append(("post", FakePostResp({"choices": []})))
    with patch("core.llm_client.httpx.AsyncClient", FakeAsyncClient):
        resp = await client._http_post(url, {}, {"model": "m"})
    assert resp.json() == {"choices": []}


async def test_http_post_exhausts_retries_and_raises():
    c = LLMClient(api_key="k", base_url="u", model="m", max_retries=1, base_delay=0.001)
    FakeAsyncClient.script.append(("raise", httpx.ConnectError("down")))
    FakeAsyncClient.script.append(("raise", httpx.ConnectError("down")))
    with (
        patch("core.llm_client.httpx.AsyncClient", FakeAsyncClient),
        pytest.raises(httpx.ConnectError),
    ):
        await c._http_post("https://u/x", {}, {})


async def test_http_post_retries_retryable_status(client):
    url = "https://api.example.com/chat/completions"
    FakeAsyncClient.script.append(("post", FakePostResp({}, status=503)))
    FakeAsyncClient.script.append(("post", FakePostResp({"ok": True})))
    with patch("core.llm_client.httpx.AsyncClient", FakeAsyncClient):
        resp = await client._http_post(url, {}, {})
    assert resp.json() == {"ok": True}


async def test_http_post_non_retryable_raises_immediately(client):
    FakeAsyncClient.script.append(("post", FakePostResp({}, status=400)))
    with (
        patch("core.llm_client.httpx.AsyncClient", FakeAsyncClient),
        pytest.raises(httpx.HTTPStatusError),
    ):
        await client._http_post("https://api.example.com/x", {}, {})


# ── Gemini transport (_google_chat) ────────────────────────────────────────
# (uses the gclient fixture above: gemini-2.5-pro, max_retries=1)


def _g_sse_line(obj):
    return "data: " + json.dumps(obj)


def _g_text_chunk(text, finish="STOP", usage=None):
    chunk = {
        "candidates": [
            {
                "content": {"parts": [{"text": text}]},
                "finishReason": finish,
            }
        ]
    }
    if usage is not None:
        chunk["usageMetadata"] = usage
    return chunk


async def test_google_stream_text_and_usage(gclient):
    lines = [
        _g_sse_line(_g_text_chunk("Hello", usage=None)),
        _g_sse_line(
            _g_text_chunk(
                " world",
                usage={
                    "promptTokenCount": 10,
                    "candidatesTokenCount": 3,
                    "totalTokenCount": 13,
                },
            )
        ),
    ]
    FakeAsyncClient.script.append(("stream", FakeStreamResp(lines)))
    seen = []
    with patch("core.llm_client.httpx.AsyncClient", FakeAsyncClient):
        msg = await gclient._google_chat(
            [{"role": "user", "content": "hi"}], None, seen.append, None, stream=True
        )
    assert msg["role"] == "assistant"
    assert msg["content"] == "Hello world"
    assert seen == ["Hello", " world"]
    assert msg["_usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 3,
        "total_tokens": 13,
    }
    assert msg["_finish_reason"] == "stop"
    url = FakeAsyncClient.instances[-1].captured["url"]
    assert "streamGenerateContent?alt=sse" in url
    assert "key=k" in url


async def test_google_stream_thought_parts(gclient):
    lines = [
        _g_sse_line(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "pondering", "thought": True},
                                {"text": "answer"},
                            ]
                        }
                    }
                ]
            }
        )
    ]
    FakeAsyncClient.script.append(("stream", FakeStreamResp(lines)))
    think_seen = []
    with patch("core.llm_client.httpx.AsyncClient", FakeAsyncClient):
        msg = await gclient._google_chat(
            [{"role": "user", "content": "hi"}], None, None, think_seen.append, stream=True
        )
    assert msg["content"] == "answer"
    assert msg["reasoning_content"] == "pondering"
    assert think_seen == ["pondering"]


async def test_google_stream_function_call(gclient):
    lines = [
        _g_sse_line(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "functionCall": {
                                        "name": "search",
                                        "args": {"q": "weather"},
                                    },
                                    "thoughtSignature": "sig1",
                                }
                            ]
                        },
                        "finishReason": "STOP",
                    }
                ]
            }
        )
    ]
    FakeAsyncClient.script.append(("stream", FakeStreamResp(lines)))
    with patch("core.llm_client.httpx.AsyncClient", FakeAsyncClient):
        msg = await gclient._google_chat(
            [{"role": "user", "content": "hi"}], None, None, None, stream=True
        )
    assert msg["tool_calls"][0]["id"] == "call_0"
    assert msg["tool_calls"][0]["function"]["name"] == "search"
    assert json.loads(msg["tool_calls"][0]["function"]["arguments"]) == {"q": "weather"}
    assert msg["_gcalls"][0]["sig"] == "sig1"


async def test_google_stream_skips_non_data_and_malformed_lines(gclient):
    lines = [
        ": keep-alive comment",
        "data: not-json{{{",
        "data: [DONE]",
        _g_sse_line(_g_text_chunk("ok")),
    ]
    FakeAsyncClient.script.append(("stream", FakeStreamResp(lines)))
    with patch("core.llm_client.httpx.AsyncClient", FakeAsyncClient):
        msg = await gclient._google_chat(
            [{"role": "user", "content": "hi"}], None, None, None, stream=True
        )
    assert msg["content"] == "ok"


async def test_google_stream_http_400_no_retry(gclient):
    FakeAsyncClient.script.append(("stream", FakeStreamResp([], status=400)))
    with patch("core.llm_client.httpx.AsyncClient", FakeAsyncClient):
        msg = await gclient._google_chat(
            [{"role": "user", "content": "hi"}], None, None, None, stream=True
        )
    assert msg["content"].startswith("[API Error: 400]")
    assert len(FakeAsyncClient.instances) == 1  # single attempt, no retry


async def test_google_stream_500_retries_then_error(gclient):
    for _ in range(2):  # max_retries=1 -> 2 attempts
        FakeAsyncClient.script.append(("stream", FakeStreamResp([], status=500)))
    with patch("core.llm_client.httpx.AsyncClient", FakeAsyncClient):
        msg = await gclient._google_chat(
            [{"role": "user", "content": "hi"}], None, None, None, stream=True
        )
    assert msg["content"].startswith("[API Error: 500]")
    assert len(FakeAsyncClient.instances) == 2


async def test_google_stream_connect_error_falls_back_to_nonstreaming():
    c = LLMClient(
        api_key="k",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model="gemini-2.5-flash",
        max_retries=0,
        base_delay=0.001,
    )
    FakeAsyncClient.script.append(("raise", httpx.ConnectError("down")))
    FakeAsyncClient.script.append(
        (
            "post",
            FakePostResp(
                {
                    "candidates": [
                        {
                            "content": {"parts": [{"text": "fallback ok"}]},
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 2},
                }
            ),
        )
    )
    with patch("core.llm_client.httpx.AsyncClient", FakeAsyncClient):
        msg = await c._google_chat(
            [{"role": "user", "content": "hi"}], None, None, None, stream=True
        )
    assert msg["content"] == "fallback ok"
    assert msg["_usage"] == {"prompt_tokens": 1, "completion_tokens": 2}
    urls = [i.captured["url"] for i in FakeAsyncClient.instances]
    assert any("streamGenerateContent" in u for u in urls)
    assert any("generateContent?" in u and "alt=sse" not in u for u in urls)


async def test_google_nonstreaming_max_tokens_maps_to_length(gclient):
    FakeAsyncClient.script.append(
        (
            "post",
            FakePostResp(
                {
                    "candidates": [
                        {
                            "content": {"parts": [{"text": "cut off"}]},
                            "finishReason": "MAX_TOKENS",
                        }
                    ]
                }
            ),
        )
    )
    with patch("core.llm_client.httpx.AsyncClient", FakeAsyncClient):
        msg = await gclient._google_chat(
            [{"role": "user", "content": "hi"}], None, None, None, stream=False
        )
    assert msg["content"] == "cut off"
    assert msg["_finish_reason"] == "length"
    assert msg["_usage"] == {"prompt_tokens": 0, "completion_tokens": 0}


async def test_google_nonstreaming_empty_candidates(gclient):
    FakeAsyncClient.script.append(("post", FakePostResp({"candidates": []})))
    with patch("core.llm_client.httpx.AsyncClient", FakeAsyncClient):
        msg = await gclient._google_chat(
            [{"role": "user", "content": "hi"}], None, None, None, stream=False
        )
    assert msg["content"] == ""
    assert "_finish_reason" not in msg


async def test_google_nonstreaming_connect_error_returns_none():
    c = LLMClient(
        api_key="k",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model="gemini-2.5-flash",
        max_retries=0,
        base_delay=0.001,
    )
    FakeAsyncClient.script.append(("raise", httpx.ConnectError("down")))
    with patch("core.llm_client.httpx.AsyncClient", FakeAsyncClient):
        assert (
            await c._google_chat(
                [{"role": "user", "content": "hi"}], None, None, None, stream=False
            )
            is None
        )


async def test_google_chat_stream_dispatches_to_google_transport(gclient):
    lines = [_g_sse_line(_g_text_chunk("via dispatch"))]
    FakeAsyncClient.script.append(("stream", FakeStreamResp(lines)))
    with patch("core.llm_client.httpx.AsyncClient", FakeAsyncClient):
        msg = await gclient.chat_stream([{"role": "user", "content": "hi"}])
    assert msg["content"] == "via dispatch"


# ── retry-after / misc unit paths ───────────────────────────────────────────


def test_retry_delay_honors_retry_after_seconds(client):
    resp = SimpleNamespace(headers={"retry-after": "5"})
    assert client._retry_delay(resp, 0) == 5.0


def test_retry_delay_honors_retry_after_http_date(client):
    future = datetime.now(timezone.utc) + timedelta(seconds=20)
    resp = SimpleNamespace(headers={"retry-after": format_datetime(future, usegmt=True)})
    delay = client._retry_delay(resp, 0)
    assert 15.0 < delay <= 20.0


def test_retry_delay_garbage_retry_after_falls_back_to_backoff(client):
    resp = SimpleNamespace(headers={"retry-after": "not-a-date"})
    assert client._retry_delay(resp, 1) == pytest.approx(0.001 * 2)


def test_retry_delay_broken_headers_falls_back_to_backoff(client):
    class BadHeaders(dict):
        def get(self, *a, **k):
            raise RuntimeError("boom")

    resp = SimpleNamespace(headers=BadHeaders())
    assert client._retry_delay(resp, 0) == pytest.approx(0.001)


def test_clean_schema_passthrough_non_dict(client):
    assert LLMClient._clean_schema("just a string") == "just a string"
    assert LLMClient._clean_schema(42) == 42
    assert LLMClient._clean_schema(None) is None


def test_log_payload_size_debug_on_and_off(client, capsys):
    payload = {"messages": [{"role": "user", "content": "hi"}], "tools": []}
    os.environ.pop("CODING_AGENT_DEBUG", None)
    LLMClient._log_payload_size(payload)
    assert capsys.readouterr().out == ""
    os.environ["CODING_AGENT_DEBUG"] = "1"
    try:
        LLMClient._log_payload_size(payload)
    finally:
        del os.environ["CODING_AGENT_DEBUG"]
    assert "[DBG] payload:" in capsys.readouterr().out


# ── _google_body history replay ────────────────────────────────────────────


def test_google_body_replays_gcalls_with_signature(gclient):
    body = gclient._google_body(
        [
            {
                "role": "assistant",
                "content": "thinking",
                "_gcalls": [{"name": "search", "args": {"q": "x"}, "sig": "sig9"}],
            },
            {"role": "tool", "name": "search", "content": "result"},
        ]
    )
    model_msg = body["contents"][0]
    fc_part = next(p for p in model_msg["parts"] if "functionCall" in p)
    assert fc_part["thoughtSignature"] == "sig9"
    tool_msg = body["contents"][1]
    assert tool_msg["parts"][0]["functionResponse"]["name"] == "search"


def test_google_body_thinking_model_degrades_legacy_tool_calls_to_text():
    c = LLMClient(
        api_key="k",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model="gemini-3-pro",
    )
    body = c._google_body(
        [
            {
                "role": "assistant",
                "content": "ok",
                "tool_calls": [
                    {
                        "id": "call_0",
                        "function": {"name": "search", "arguments": '{"q": "x"}'},
                    }
                ],
            }
        ]
    )
    parts = body["contents"][0]["parts"]
    assert len(parts) == 1 and "text" in parts[0]
    assert "[Called tool search" in parts[0]["text"]


def test_google_body_non_thinking_model_rebuilds_function_calls(gclient):
    body = gclient._google_body(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_0",
                        "function": {"name": "search", "arguments": "not-json{{{}}}"},
                    }
                ],
            }
        ]
    )
    fc = body["contents"][0]["parts"][0]["functionCall"]
    assert fc == {"name": "search", "args": {}}


# ── _extract_error_detail shapes ───────────────────────────────────────────


def test_extract_error_detail_openai_shape(client):
    resp = FakePostResp({"error": {"message": "bad key", "code": "invalid_api_key"}})
    assert client._extract_error_detail(resp) == "invalid_api_key: bad key"


def test_extract_error_detail_message_only(client):
    resp = FakePostResp({"error": {"message": "oops"}})
    assert client._extract_error_detail(resp) == "oops"


def test_extract_error_detail_string_error(client):
    resp = FakePostResp({"error": "something broke"})
    assert client._extract_error_detail(resp) == "something broke"


def test_extract_error_detail_detail_key(client):
    resp = FakePostResp({"detail": "rate limited"})
    assert client._extract_error_detail(resp) == "rate limited"


def test_extract_error_detail_non_dict_falls_back_to_text(client):
    resp = FakePostResp(["not", "a", "dict"])
    assert client._extract_error_detail(resp) == json.dumps(["not", "a", "dict"])


def test_extract_error_detail_json_raises_uses_text(client):
    resp = SimpleNamespace(
        text="plain text error",
        json=lambda: (_ for _ in ()).throw(ValueError("no json")),
    )
    assert client._extract_error_detail(resp) == "plain text error"


def test_extract_error_detail_everything_raises_returns_empty(client):
    resp2 = SimpleNamespace()
    resp2.json = lambda: (_ for _ in ()).throw(ValueError("no json"))
    resp2.__dict__["text"] = property(lambda self: (_ for _ in ()).throw(RuntimeError()))
    assert client._extract_error_detail(resp2) == ""


# ── chat_nonstreaming extras ───────────────────────────────────────────────


async def test_chat_nonstreaming_unwraps_data_gateway(client):
    FakeAsyncClient.script.append(
        (
            "post",
            FakePostResp(
                {
                    "data": {
                        "choices": [
                            {
                                "message": {"role": "assistant", "content": "wrapped"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 2},
                    }
                }
            ),
        )
    )
    with patch("core.llm_client.httpx.AsyncClient", FakeAsyncClient):
        msg = await client.chat_nonstreaming([{"role": "user", "content": "hi"}])
    assert msg["content"] == "wrapped"
    assert msg["_finish_reason"] == "stop"
    assert msg["_usage"] == {"prompt_tokens": 2}


async def test_chat_nonstreaming_dispatches_to_google(gclient):
    FakeAsyncClient.script.append(
        (
            "post",
            FakePostResp(
                {
                    "candidates": [
                        {
                            "content": {"parts": [{"text": "g-answer"}]},
                            "finishReason": "STOP",
                        }
                    ]
                }
            ),
        )
    )
    with patch("core.llm_client.httpx.AsyncClient", FakeAsyncClient):
        msg = await gclient.chat_nonstreaming([{"role": "user", "content": "hi"}])
    assert msg["content"] == "g-answer"
    assert msg["_finish_reason"] == "stop"


# ── _read_stream tool-call assembly ────────────────────────────────────────


def _oa_chunk(**kw):
    base = {"choices": [{"delta": {}}]}
    base["choices"][0].update(kw)
    return "data: " + json.dumps(base)


async def test_read_stream_assembles_fragmented_tool_calls(client):
    lines = [
        _oa_chunk(
            delta={
                "tool_calls": [
                    {"index": 0, "id": "t1", "function": {"name": "se", "arguments": ""}}
                ]
            }
        ),
        _oa_chunk(
            delta={
                "tool_calls": [{"index": 0, "function": {"name": "arch", "arguments": '{"q": "x'}}]
            }
        ),
        _oa_chunk(
            delta={"tool_calls": [{"index": 0, "function": {"arguments": '"}'}}]},
            finish_reason="tool_calls",
        ),
        "data: [DONE]",
    ]
    resp = FakeStreamResp(lines)
    msg = await client._read_stream(resp, None, None)
    tc = msg["tool_calls"][0]
    assert tc["id"] == "t1"
    assert tc["function"]["name"] == "search"
    assert json.loads(tc["function"]["arguments"]) == {"q": "x"}
    assert msg["_finish_reason"] == "tool_calls"


async def test_read_stream_captures_usage_reasoning_and_done(client):
    lines = [
        _oa_chunk(delta={"content": "hi"}),
        _oa_chunk(delta={"reasoning_content": "hmm"}),
        "data: " + json.dumps({"choices": [], "usage": {"prompt_tokens": 5}}),
        "data: [DONE]",
        _oa_chunk(delta={"content": "after done ignored"}),
    ]
    seen, think = [], []
    resp = FakeStreamResp(lines)
    msg = await client._read_stream(resp, seen.append, think.append)
    assert msg["content"] == "hi"
    assert msg["reasoning_content"] == "hmm"
    assert msg["_usage"] == {"prompt_tokens": 5}
    assert seen == ["hi"] and think == ["hmm"]


async def test_read_stream_skips_empty_and_malformed(client):
    lines = ["", "event: ping", "data: {{{bad", _oa_chunk(delta={"content": "ok"})]
    resp = FakeStreamResp(lines)
    msg = await client._read_stream(resp, None, None)
    assert msg["content"] == "ok"


async def test_read_stream_chunk_without_choices(client):
    resp = FakeStreamResp([_oa_chunk(delta={"content": "x"}).replace('"choices"', '"nada"')])
    msg = await client._read_stream(resp, None, None)
    assert msg["content"] == ""


async def test_google_nonstreaming_mixed_parts(gclient):
    FakeAsyncClient.script.append(
        (
            "post",
            FakePostResp(
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {"text": ""},
                                    {"text": "quiet thought", "thought": True},
                                    {"functionCall": {"name": "f", "args": {}}},
                                    {"text": "visible"},
                                ]
                            },
                            "finishReason": "STOP",
                        }
                    ]
                }
            ),
        )
    )
    with patch("core.llm_client.httpx.AsyncClient", FakeAsyncClient):
        msg = await gclient._google_chat(
            [{"role": "user", "content": "hi"}], None, None, None, stream=False
        )
    assert msg["content"] == "visible"
    assert msg["reasoning_content"] == "quiet thought"
    assert msg["tool_calls"][0]["function"]["name"] == "f"
    assert msg["_gcalls"][0]["sig"] == ""


async def test_google_stream_chunks_without_callbacks(gclient):
    lines = [
        _g_sse_line(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "t1", "thought": True},
                                {"text": "plain"},
                            ]
                        }
                    }
                ]
            }
        )
    ]
    FakeAsyncClient.script.append(("stream", FakeStreamResp(lines)))
    with patch("core.llm_client.httpx.AsyncClient", FakeAsyncClient):
        msg = await gclient._google_chat(
            [{"role": "user", "content": "hi"}], None, None, None, stream=True
        )
    assert msg["content"] == "plain"
    assert msg["reasoning_content"] == "t1"


async def test_google_stream_connect_error_retries_then_falls_back(gclient):
    FakeAsyncClient.script.append(("raise", httpx.ConnectError("down1")))
    FakeAsyncClient.script.append(("raise", httpx.ConnectError("down2")))
    FakeAsyncClient.script.append(
        (
            "post",
            FakePostResp({"candidates": [{"content": {"parts": [{"text": "recovered"}]}}]}),
        )
    )
    with patch("core.llm_client.httpx.AsyncClient", FakeAsyncClient):
        msg = await gclient._google_chat(
            [{"role": "user", "content": "hi"}], None, None, None, stream=True
        )
    assert msg["content"] == "recovered"
    assert len(FakeAsyncClient.instances) == 3  # 2 stream attempts + 1 fallback


async def test_read_stream_tool_call_without_id_uses_index_fallback(client):
    lines = [
        _oa_chunk(delta={"tool_calls": [{"index": 2, "function": {"name": "f"}}]}),
        "data: [DONE]",
    ]
    resp = FakeStreamResp(lines)
    msg = await client._read_stream(resp, None, None)
    assert msg["tool_calls"][0]["id"] == "call_2"


def test_extract_error_detail_empty_string_error_falls_through(client):
    resp = FakePostResp({"error": "", "message": "top-level msg"})
    assert client._extract_error_detail(resp) == "top-level msg"


def test_extract_error_detail_code_only(client):
    resp = FakePostResp({"error": {"code": "E1"}})
    assert client._extract_error_detail(resp) == "E1"


async def test_chat_nonstreaming_basic_success(client):
    FakeAsyncClient.script.append(
        (
            "post",
            FakePostResp(
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "hello",
                                "reasoning_content": "why",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 3},
                }
            ),
        )
    )
    seen, think = [], []
    with patch("core.llm_client.httpx.AsyncClient", FakeAsyncClient):
        msg = await client.chat_nonstreaming(
            [{"role": "user", "content": "hi"}], None, seen.append, think.append
        )
    assert msg["content"] == "hello"
    assert seen == ["hello"] and think == ["why"]
    assert msg["_finish_reason"] == "stop"
    assert msg["_usage"] == {"prompt_tokens": 3}
    headers = FakeAsyncClient.instances[-1].captured["headers"]
    assert headers["Authorization"] == "Bearer test-key"
