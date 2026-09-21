"""Night-2 tests: llm/ provider clients, cost tracking, message conversion.

httpx.AsyncClient is replaced by a capturing fake — no network. Covers:
CostTracker math/nulls, StreamingToolCallAccumulator merging,
LLMConfig.from_env provider selection, LLMClient.create factory,
ProviderRouter, LLMResult helpers, and per-provider chat() request
construction + response parsing (OpenAI envelope unwrap, Anthropic
system/tool_use mapping, DeepSeek thinking flag, Ollama endpoint).
"""

from __future__ import annotations

import json

import pytest

# ── fake httpx ───────────────────────────────────────────────────────


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeAsyncClient:
    """Captures requests; handler(url, body, headers) -> payload."""

    instances: list = []

    def __init__(self):
        FakeAsyncClient.instances.append(self)
        self.handler = None
        self.stream_lines = []
        self.captured = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        self.captured = {"url": url, "headers": headers or {}, "json": json or {}}
        return FakeResponse(self.handler(url, self.captured["json"], self.captured["headers"]))

    def stream(self, method, url, headers=None, json=None):
        client = self

        class _Stream:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def raise_for_status(self):
                pass

            async def aiter_lines(self):
                for line in client.stream_lines:
                    yield line

        self.captured = {"url": url, "headers": headers or {}, "json": json or {}}
        return _Stream()


@pytest.fixture
def mock_client(monkeypatch):
    """Replace httpx.AsyncClient with the capturing fake.

    Usage: mock_client(handler) or mock_client(handler, lines=[...]) for streams.
    """

    def _patch(handler=None, lines=None):
        def _make(*a, **k):
            c = FakeAsyncClient()
            c.handler = handler or (lambda u, b, h: {})
            c.stream_lines = lines or []
            return c

        monkeypatch.setattr("httpx.AsyncClient", _make)

    FakeAsyncClient.instances.clear()
    return _patch


def _cfg(provider, **kw):
    from llm import LLMConfig

    base = {
        "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
        "anthropic": ("https://api.anthropic.com/v1", "claude-3-haiku-20240307"),
        "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat"),
        "ollama": ("http://localhost:11434", "codellama"),
        "google": ("https://generativelanguage.googleapis.com/v1beta", "gemini-2.5-flash"),
        "zai": ("https://api.z.ai/api/paas/v4", "glm-4.5"),
        "mystery": ("https://example.invalid", "mystery-model"),
    }[provider]
    kw.setdefault("base_url", base[0])
    kw.setdefault("model", base[1])
    kw.setdefault("api_key", "sk-test")
    kw.setdefault("provider", provider)
    return LLMConfig(**kw)


def _last_sent():
    return FakeAsyncClient.instances[-1].captured


# ── CostTracker / accumulator / result ───────────────────────────────


class TestCostTracker:
    def test_add_usage_math(self):
        from llm import CostTracker

        t = CostTracker()
        t.add_usage({"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}, "gpt-4o-mini")
        assert t.total_input_tokens == 1_000_000
        assert t.total_output_tokens == 1_000_000
        assert t.total_cost == pytest.approx(0.15 + 0.60)
        assert "$0.7500" in t.summary()
        assert t.to_dict()["cost"] == round(0.75, 6)
        t.reset()
        assert t.total_cost == 0.0

    def test_null_token_counts(self):
        from llm import CostTracker

        t = CostTracker()
        t.add_usage({"prompt_tokens": None, "completion_tokens": None}, "gpt-4o-mini")
        assert t.total_input_tokens == 0
        assert "free" in t.summary()

    def test_unknown_model_free(self):
        from llm import CostTracker

        t = CostTracker()
        t.add_usage({"prompt_tokens": 100, "completion_tokens": 50}, "mystery-model")
        assert t.total_cost == 0.0

    def test_input_tokens_alias(self):
        from llm import CostTracker

        t = CostTracker()
        t.add_usage({"input_tokens": 10, "output_tokens": 5}, "gpt-4o-mini")
        assert t.total_input_tokens == 10 and t.total_output_tokens == 5

    def test_model_switch_mid_session(self):
        from llm import CostTracker

        t = CostTracker()
        t.add_usage({"prompt_tokens": 1_000_000, "completion_tokens": 0}, "gpt-4o-mini")
        t.add_usage({"prompt_tokens": 1_000_000, "completion_tokens": 0}, "gpt-4o")
        assert t.total_cost == pytest.approx(0.15 + 2.50)


class TestStreamingAccumulator:
    def test_merge_fragments(self):
        from llm import StreamingToolCallAccumulator

        a = StreamingToolCallAccumulator()
        a.add(
            [
                {
                    "index": 0,
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "read", "arguments": '{"pa'},
                }
            ]
        )
        a.add([{"index": 0, "function": {"arguments": 'th": "x"}'}}])
        a.add([{"index": 1, "id": "c2", "function": {"name": "write", "arguments": "{}"}}])
        calls = a.calls()
        assert len(calls) == 2
        assert calls[0]["function"]["name"] == "read"
        assert json.loads(calls[0]["function"]["arguments"]) == {"path": "x"}
        assert calls[1]["id"] == "c2"

    def test_none_and_non_dict_ignored(self):
        from llm import StreamingToolCallAccumulator

        a = StreamingToolCallAccumulator()
        a.add(None)
        a.add(["garbage", 42])
        assert a.calls() == []


class TestLLMResult:
    def test_get_and_to_dict(self):
        from llm import LLMResult

        r = LLMResult(content="hi", model="m", finish_reason="stop")
        assert r.get("content") == "hi"
        assert r.get("nope", "d") == "d"
        assert r.to_dict() == {"role": "assistant", "content": "hi"}

    def test_to_dict_with_tools(self):
        from llm import LLMResult

        r = LLMResult(tool_calls=[{"id": "1"}])
        d = r.to_dict()
        assert d["role"] == "assistant" and "content" not in d
        assert d["tool_calls"] == [{"id": "1"}]


# ── config / factory / router ────────────────────────────────────────


class TestLLMConfig:
    def test_from_env_explicit(self, monkeypatch):
        from llm import LLMConfig

        monkeypatch.setenv("CODING_AGENT_PROVIDER", "anthropic")
        cfg = LLMConfig.from_env()
        assert cfg.provider == "anthropic"
        assert cfg.base_url == "https://api.anthropic.com/v1"

    def test_from_env_defaults_deepseek(self, monkeypatch):
        from llm import LLMConfig

        for k in (
            "CODING_AGENT_PROVIDER",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GOOGLE_API_KEY",
            "OLLAMA_MODEL",
            "ZAI_API_KEY",
            "OPENROUTER_API_KEY",
            "CLINEPASS_API_KEY",
        ):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("CODING_AGENT_TEMP", "0.5")
        monkeypatch.setenv("CODING_AGENT_MAX_TOKENS", "100")
        cfg = LLMConfig.from_env()
        assert cfg.provider == "deepseek"
        assert cfg.temperature == 0.5 and cfg.max_tokens == 100

    def test_from_env_zai(self, monkeypatch):
        from llm import LLMConfig

        monkeypatch.setenv("CODING_AGENT_PROVIDER", "zai")
        monkeypatch.setenv("ZAI_API_KEY", "x")
        assert LLMConfig.from_env().provider == "zai"

    def test_from_env_openai_implicit(self, monkeypatch):
        from llm import LLMConfig

        monkeypatch.delenv("CODING_AGENT_PROVIDER", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        cfg = LLMConfig.from_env()
        assert cfg.provider == "openai"
        assert cfg.api_key == "sk-x"


class TestFactory:
    def test_creates_right_client(self):
        from llm import LLMClient
        from llm.anthropic_client import AnthropicClient
        from llm.deepseek_client import DeepSeekClient
        from llm.google_client import GoogleClient
        from llm.ollama_client import OllamaClient
        from llm.openai_client import OpenAIClient

        assert isinstance(LLMClient.create(_cfg("openai")), OpenAIClient)
        assert isinstance(LLMClient.create(_cfg("anthropic")), AnthropicClient)
        assert isinstance(LLMClient.create(_cfg("google")), GoogleClient)
        assert isinstance(LLMClient.create(_cfg("ollama")), OllamaClient)
        assert isinstance(LLMClient.create(_cfg("deepseek")), DeepSeekClient)
        # zai/openrouter/clinepass ride the OpenAI-compatible client
        assert isinstance(LLMClient.create(_cfg("zai")), OpenAIClient)
        # unknown provider falls back to DeepSeek
        assert isinstance(LLMClient.create(_cfg("mystery")), DeepSeekClient)


class TestProviderRouter:
    async def test_switch_replaces_client(self):
        from llm import ProviderRouter

        r = ProviderRouter(_cfg("openai"))
        first = r.client
        r.switch(_cfg("anthropic"))
        assert r.client is not first
        assert r.config.provider == "anthropic"
        assert r.cost_tracker is r.client.cost_tracker


# ── OpenAI client ────────────────────────────────────────────────────


def _openai_payload(**kw):
    p = {
        "id": "c",
        "choices": [
            {
                "message": {"role": "assistant", "content": "hello"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "model": "gpt-4o-mini",
    }
    p.update(kw)
    return p


class TestOpenAIClient:
    async def test_chat_request_shape(self, mock_client):
        from llm.openai_client import OpenAIClient

        mock_client(lambda u, b, h: _openai_payload())
        client = OpenAIClient(_cfg("openai"))
        r = await client.chat([{"role": "user", "content": "hi"}], tools=[{"type": "f"}])

        sent = _last_sent()
        assert sent["url"].endswith("/chat/completions")
        assert sent["headers"]["Authorization"] == "Bearer sk-test"
        assert sent["json"]["model"] == "gpt-4o-mini"
        assert sent["json"]["messages"] == [{"role": "user", "content": "hi"}]
        assert sent["json"]["tools"] == [{"type": "f"}]
        assert "stream" not in sent["json"]
        assert r.content == "hello"
        assert r.finish_reason == "stop"
        assert r.usage["prompt_tokens"] == 10
        assert client.cost_tracker.total_input_tokens == 10

    async def test_no_auth_header_without_key(self, mock_client):
        from llm.openai_client import OpenAIClient

        mock_client(lambda u, b, h: _openai_payload())
        await OpenAIClient(_cfg("openai", api_key="")).chat([{"role": "user", "content": "hi"}])
        assert "Authorization" not in _last_sent()["headers"]

    async def test_envelope_unwrap(self, mock_client):
        from llm.openai_client import OpenAIClient

        mock_client(lambda u, b, h: {"success": True, "data": _openai_payload()})
        r = await OpenAIClient(_cfg("openai")).chat([{"role": "user", "content": "hi"}])
        assert r.content == "hello"

    def test_tool_message_format(self):
        from llm.openai_client import OpenAIClient

        client = OpenAIClient(_cfg("openai"))
        msgs = [
            {"role": "tool", "content": "", "tool_call_id": "c1"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "function": {"name": "x"}}],
            },
        ]
        formatted = client._fmt(msgs)
        assert formatted[0] == {"role": "tool", "content": "ok", "tool_call_id": "c1"}
        assert formatted[1]["content"] is None

    async def test_chat_stream(self, mock_client):
        from llm.openai_client import OpenAIClient

        lines = [
            'data: {"choices": [{"delta": {"content": "he"}}]}',
            "",
            "not a data line",
            'data: {"choices": [{"delta": {"content": "llo"}, "finish_reason": "stop"}]}',
            'data: {"usage": {"prompt_tokens": 3, "completion_tokens": 2}}',
            "data: [DONE]",
        ]
        mock_client(lambda u, b, h: {}, lines=lines)
        client = OpenAIClient(_cfg("openai"))
        seen = []
        r = await client.chat_stream([{"role": "user", "content": "hi"}], on_token=seen.append)
        assert r.content == "hello"
        assert seen == ["he", "llo"]
        assert r.finish_reason == "stop"
        assert r.usage["prompt_tokens"] == 3
        assert _last_sent()["json"]["stream"] is True

    async def test_chat_stream_tool_call_fragments(self, mock_client):
        from llm.openai_client import OpenAIClient

        lines = [
            'data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "rea"}}]}}]}',
            'data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{}\\""}}]}}]}',
            "data: [DONE]",
        ]
        mock_client(lambda u, b, h: {}, lines=lines)
        r = await OpenAIClient(_cfg("openai")).chat_stream([{"role": "user", "content": "hi"}])
        assert r.tool_calls[0]["function"]["name"] == "rea"
        assert r.tool_calls[0]["id"] == "c1"


# ── Anthropic client ─────────────────────────────────────────────────


def _anth_payload():
    return {
        "id": "m",
        "model": "claude-3-haiku-20240307",
        "stop_reason": "end_turn",
        "content": [
            {"type": "text", "text": "hi there"},
            {"type": "tool_use", "id": "t1", "name": "read", "input": {"path": "x.py"}},
        ],
        "usage": {"input_tokens": 8, "output_tokens": 4},
    }


class TestAnthropicClient:
    async def test_chat(self, mock_client):
        from llm.anthropic_client import AnthropicClient

        mock_client(lambda u, b, h: _anth_payload())
        client = AnthropicClient(_cfg("anthropic"))
        r = await client.chat(
            [
                {"role": "system", "content": "be nice"},
                {"role": "user", "content": "hi"},
            ],
            tools=[{"type": "function", "function": {"name": "read"}}],
        )
        sent = _last_sent()
        assert sent["url"].endswith("/messages")
        assert sent["headers"]["x-api-key"] == "sk-test"
        assert sent["headers"]["anthropic-version"] == "2023-06-01"
        assert sent["json"]["system"] == "be nice"
        assert all(m["role"] != "system" for m in sent["json"]["messages"])
        assert sent["json"]["tools"][0]["name"] == "read"
        assert r.content == "hi there"
        assert len(r.tool_calls) == 1
        assert r.tool_calls[0]["function"]["name"] == "read"
        assert json.loads(r.tool_calls[0]["function"]["arguments"]) == {"path": "x.py"}
        assert r.finish_reason == "end_turn"
        assert client.cost_tracker.total_input_tokens == 8

    def test_to_claude_tool_message(self):
        from llm.anthropic_client import AnthropicClient

        c = AnthropicClient(_cfg("anthropic"))
        system, msgs = c._to_claude(
            [
                {"role": "system", "content": "s1"},
                {"role": "system", "content": "s2"},
                {"role": "tool", "content": "out", "tool_call_id": "t9"},
                {
                    "role": "assistant",
                    "content": "thinking",
                    "tool_calls": [
                        {"id": "a", "function": {"name": "f", "arguments": '{"k":1}'}},
                        "not-a-dict",
                    ],
                },
            ]
        )
        assert system == "s1\ns2"
        assert msgs[0]["content"][0]["type"] == "tool_result"
        assert msgs[0]["content"][0]["tool_use_id"] == "t9"
        blocks = msgs[1]["content"]
        assert blocks[0] == {"type": "text", "text": "thinking"}
        assert blocks[1]["type"] == "tool_use"
        assert blocks[1]["input"] == {"k": 1}
        assert len(blocks) == 2  # malformed call skipped

    def test_convert_tools(self):
        from llm.anthropic_client import AnthropicClient

        c = AnthropicClient(_cfg("anthropic"))
        out = c._convert_tools(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "read",
                        "description": "d",
                        "parameters": {"type": "object"},
                    },
                }
            ]
        )
        assert out[0] == {"name": "read", "description": "d", "input_schema": {"type": "object"}}

    async def test_chat_stream(self, mock_client):
        from llm.anthropic_client import AnthropicClient

        lines = [
            'data: {"type": "content_block_start", "content_block": {"type": "tool_use", "id": "t1", "name": "read"}}',
            'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "yo"}}',
            'data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"input_tokens": 2, "output_tokens": 1}}',
            'data: {"type": "message_stop"}',
        ]
        mock_client(lambda u, b, h: {}, lines=lines)
        client = AnthropicClient(_cfg("anthropic"))
        tokens = []
        r = await client.chat_stream([{"role": "user", "content": "hi"}], on_token=tokens.append)
        assert r.content == "yo"
        assert tokens == ["yo"]
        assert r.finish_reason == "end_turn"
        assert r.tool_calls[0]["function"]["name"] == "read"
        assert _last_sent()["json"]["stream"] is True


# ── DeepSeek client ────────────────────────────────────────────────


class TestDeepSeekClient:
    async def test_thinking_flag_in_body(self, mock_client):
        from llm.deepseek_client import DeepSeekClient

        mock_client(lambda u, b, h: _openai_payload())
        await DeepSeekClient(_cfg("deepseek", thinking=True)).chat(
            [{"role": "user", "content": "hi"}]
        )
        assert _last_sent()["json"]["thinking"] == {"type": "enabled"}

    async def test_no_thinking_flag_by_default(self, mock_client):
        from llm.deepseek_client import DeepSeekClient

        mock_client(lambda u, b, h: _openai_payload())
        await DeepSeekClient(_cfg("deepseek")).chat([{"role": "user", "content": "hi"}])
        assert "thinking" not in _last_sent()["json"]

    async def test_chat_stream_thinking(self, mock_client):
        from llm.deepseek_client import DeepSeekClient

        lines = [
            'data: {"choices": [{"delta": {"thinking": "hmm ", "content": "ok"}}]}',
            "data: [DONE]",
        ]
        mock_client(lambda u, b, h: {}, lines=lines)
        client = DeepSeekClient(_cfg("deepseek"))
        thinks = []
        r = await client.chat_stream([{"role": "user", "content": "hi"}], on_think=thinks.append)
        assert r.thinking == "hmm "
        assert r.content == "ok"
        assert thinks == ["hmm "]


# ── Ollama client ──────────────────────────────────────────────────


class TestOllamaClient:
    async def test_chat_endpoint(self, mock_client):
        from llm.ollama_client import OllamaClient

        mock_client(
            lambda u, b, h: {
                "model": "codellama",
                "message": {"role": "assistant", "content": "local answer"},
                "done_reason": "stop",
            }
        )
        client = OllamaClient(_cfg("ollama"))
        r = await client.chat([{"role": "user", "content": "hi"}], tools=[{"type": "f"}])
        sent = _last_sent()
        assert sent["url"].endswith("/api/chat")
        assert sent["json"]["stream"] is False
        assert sent["json"]["tools"] == [{"type": "f"}]
        assert "Authorization" not in sent["headers"]
        assert r.content == "local answer"
        assert r.finish_reason == "stop"

    async def test_chat_stream(self, mock_client):
        from llm.ollama_client import OllamaClient

        lines = [
            '{"message": {"content": "a"}, "done": false}',
            "junk",
            '{"message": {"content": "b"}, "done": false}',
            '{"done": true}',
        ]
        mock_client(lambda u, b, h: {}, lines=lines)
        r = await OllamaClient(_cfg("ollama")).chat_stream([{"role": "user", "content": "hi"}])
        assert r.content == "ab"


# ── Google client (factory smoke) ──────────────────────────────────


class TestGoogleClientImport:
    def test_factory_returns_google(self):
        from llm import LLMClient
        from llm.google_client import GoogleClient

        assert isinstance(LLMClient.create(_cfg("google")), GoogleClient)
