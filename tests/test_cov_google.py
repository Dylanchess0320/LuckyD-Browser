"""Coverage push for llm/google_client.py — chat, chat_stream, and
_to_google. HTTP is faked at the httpx.AsyncClient seam; no network."""

from __future__ import annotations

import json

import httpx

from llm import LLMConfig
from llm.google_client import GoogleClient

# ── fakes ────────────────────────────────────────────────────────────────


def _cfg(**kw):
    base = {
        "api_key": "test-key",
        "base_url": "https://example.com",
        "model": "gemini-2.0-flash",
        "provider": "google",
    }
    base.update(kw)
    return LLMConfig(**base)


class _FakeResp:
    """Hand-written fake for an httpx response / streaming response."""

    def __init__(self, payload=None, lines=(), status=200):
        self._payload = payload if payload is not None else {}
        self._lines = list(lines)
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _client_class(payload=None, lines=(), status=200, record=None):
    """Build a fake httpx.AsyncClient class capturing request details."""

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None):
            if record is not None:
                record["post"] = {"url": url, "json": json}
            return _FakeResp(payload, status=status)

        def stream(self, method, url, json=None):
            if record is not None:
                record["stream"] = {"method": method, "url": url, "json": json}
            return _FakeResp(payload, lines=lines, status=status)

    return _FakeAsyncClient


def _patch_client(monkeypatch, **kw):
    record = {}
    monkeypatch.setattr(httpx, "AsyncClient", _client_class(record=record, **kw))
    return record


def _candidate_payload(*parts, prompt=10, completion=5):
    return {
        "candidates": [{"content": {"parts": list(parts)}}],
        "usageMetadata": {"promptTokenCount": prompt, "candidatesTokenCount": completion},
    }


# ── chat ─────────────────────────────────────────────────────────────────


class TestChat:
    async def test_chat_text_and_tool_call(self, monkeypatch):
        payload = _candidate_payload(
            {"text": "Hello"},
            {"functionCall": {"name": "get_weather", "args": {"city": "Paris"}}},
        )
        record = _patch_client(monkeypatch, payload=payload)
        client = GoogleClient(_cfg())

        result = await client.chat([{"role": "user", "content": "hi"}])

        assert result.content == "Hello"
        assert result.model == "gemini-2.0-flash"
        assert result.tool_calls is not None
        call = result.tool_calls[0]
        assert call["id"] == "get_weather"
        assert call["type"] == "function"
        assert call["function"]["name"] == "get_weather"
        assert json.loads(call["function"]["arguments"]) == {"city": "Paris"}
        assert result.usage == {"input_tokens": 10, "output_tokens": 5}
        assert client.cost_tracker.total_input_tokens == 10
        assert client.cost_tracker.total_output_tokens == 5
        # request shape
        assert ":generateContent?key=test-key" in record["post"]["url"]
        assert record["post"]["json"]["contents"][0]["role"] == "user"

    async def test_chat_no_candidates(self, monkeypatch):
        _patch_client(monkeypatch, payload={})
        client = GoogleClient(_cfg())
        result = await client.chat([{"role": "user", "content": "hi"}])
        assert result.content == ""
        assert result.tool_calls is None
        assert result.usage == {"input_tokens": 0, "output_tokens": 0}

    async def test_chat_empty_parts(self, monkeypatch):
        _patch_client(monkeypatch, payload=_candidate_payload())
        client = GoogleClient(_cfg())
        result = await client.chat([{"role": "user", "content": "hi"}])
        assert result.content == ""
        assert result.tool_calls is None

    async def test_chat_multiple_text_parts_concatenated(self, monkeypatch):
        payload = _candidate_payload({"text": "Hello, "}, {"text": "world"})
        _patch_client(monkeypatch, payload=payload)
        client = GoogleClient(_cfg())
        result = await client.chat([{"role": "user", "content": "hi"}])
        assert result.content == "Hello, world"

    async def test_chat_no_usage_metadata(self, monkeypatch):
        payload = {"candidates": [{"content": {"parts": [{"text": "x"}]}}]}
        _patch_client(monkeypatch, payload=payload)
        client = GoogleClient(_cfg())
        result = await client.chat([{"role": "user", "content": "hi"}])
        assert result.usage == {"input_tokens": 0, "output_tokens": 0}


# ── chat_stream ──────────────────────────────────────────────────────────


class TestChatStream:
    async def test_stream_text_usage_and_callbacks(self, monkeypatch):
        lines = [
            'data: {"candidates": [{"content": {"parts": [{"text": "Hi"}]}}]}',
            "data: not-json{",
            ": keep-alive comment",
            "plain line",
            'data: {"usageMetadata": {"promptTokenCount": 3}}',
            'data: {"candidates": []}',
        ]
        record = _patch_client(monkeypatch, lines=lines)
        client = GoogleClient(_cfg())
        tokens = []

        result = await client.chat_stream(
            [{"role": "user", "content": "hi"}], on_token=tokens.append
        )

        assert result.content == "Hi"
        assert tokens == ["Hi"]
        assert result.usage == {"promptTokenCount": 3}
        assert result.tool_calls is None
        assert "streamGenerateContent" in record["stream"]["url"]
        assert record["stream"]["method"] == "POST"

    async def test_stream_function_call_dedupe(self, monkeypatch):
        fc = {"name": "get_weather", "args": {"city": "Paris"}}
        fc2 = {"args": {"x": 1}}  # no "name" -> generated id fallback
        chunk = {"candidates": [{"content": {"parts": [{"functionCall": fc}]}}]}
        lines = [
            "data: " + json.dumps(chunk),  # first sighting
            "data: " + json.dumps(chunk),  # repeated across chunks -> deduped
            "data: "
            + json.dumps({"candidates": [{"content": {"parts": [{"functionCall": fc2}]}}]}),
            'data: {"candidates": [{"content": {"parts": [{"functionCall": "nope"}]}}]}',
        ]
        _patch_client(monkeypatch, lines=lines)
        client = GoogleClient(_cfg())

        result = await client.chat_stream([{"role": "user", "content": "hi"}])

        assert result.tool_calls is not None
        assert len(result.tool_calls) == 2
        assert result.tool_calls[0]["function"]["name"] == "get_weather"
        assert result.tool_calls[0]["id"] == "get_weather"
        # second distinct call (no name) gets a generated id
        assert result.tool_calls[1]["id"] == "call_2"
        assert result.tool_calls[1]["function"]["name"] == ""

    async def test_stream_no_candidates_no_usage(self, monkeypatch):
        _patch_client(monkeypatch, lines=['data: {"foo": 1}'])
        client = GoogleClient(_cfg())
        result = await client.chat_stream([{"role": "user", "content": "hi"}])
        assert result.content == ""
        assert result.tool_calls is None
        assert result.usage == {}

    async def test_stream_without_on_token(self, monkeypatch):
        lines = ['data: {"candidates": [{"content": {"parts": [{"text": "abc"}]}}]}']
        _patch_client(monkeypatch, lines=lines)
        client = GoogleClient(_cfg())
        result = await client.chat_stream([{"role": "user", "content": "hi"}])
        assert result.content == "abc"


# ── _to_google ────────────────────────────────────────────────────────────


class TestToGoogle:
    def test_roles_mapped(self):
        client = GoogleClient(_cfg())
        body = client._to_google(
            [
                {"role": "system", "content": "Be nice."},
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
                {"role": "tool", "name": "get_weather", "content": "sunny"},
                {"role": "developer", "content": "ignored"},  # unknown role skipped
            ]
        )
        assert body["systemInstruction"] == {"parts": [{"text": "Be nice.\n"}]}
        roles = [c["role"] for c in body["contents"]]
        assert roles == ["user", "model", "function"]
        assert body["contents"][1] == {"role": "model", "parts": [{"text": "Hi there"}]}
        fn = body["contents"][2]["parts"][0]["functionResponse"]
        assert fn == {"name": "get_weather", "response": {"content": "sunny"}}
        assert "tools" not in body

    def test_no_system_no_tools(self):
        client = GoogleClient(_cfg())
        body = client._to_google([{"role": "user", "content": "hi"}])
        assert "systemInstruction" not in body
        assert "tools" not in body

    def test_tools_without_function_wrapper(self):
        client = GoogleClient(_cfg())
        tools = [
            {"name": "get_weather", "description": "Weather", "parameters": {"type": "object"}}
        ]
        body = client._to_google([{"role": "user", "content": "hi"}], tools)
        decls = body["tools"][0]["functionDeclarations"]
        assert decls == [
            {
                "name": "get_weather",
                "description": "Weather",
                "parameters": {"type": "object"},
            }
        ]

    def test_tools_with_function_wrapper(self):
        client = GoogleClient(_cfg())
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_time",
                    "description": "Time",
                    "parameters": {},
                },
            }
        ]
        body = client._to_google([{"role": "user", "content": "hi"}], tools)
        decls = body["tools"][0]["functionDeclarations"]
        assert decls[0]["name"] == "get_time"
        assert decls[0]["description"] == "Time"
