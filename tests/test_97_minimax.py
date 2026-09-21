"""LuckyD 9.7 — MiniMax provider (Anthropic-compatible Messages API).

No network calls: httpx.AsyncClient is replaced with a hand-written fake that
captures the request and returns canned Anthropic-shaped payloads.
"""

from __future__ import annotations

import os

import httpx
import pytest

from core.providers import (
    PROVIDER_DEFAULTS,
    PROVIDER_NAMES,
    VALID_PROVIDERS,
    detect_api_format,
    resolve_provider_config,
)
from llm import LLMClient, LLMConfig
from llm.minimax_client import MiniMaxClient

_ENV_KEYS = ("MINIMAX_API_KEY", "MINIMAX_BASE_URL", "MINIMAX_MODEL")


@pytest.fixture(autouse=True)
def _clean_minimax_env(monkeypatch: pytest.MonkeyPatch):
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)


# ── Fakes ──────────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


class _FakeStreamResponse:
    def __init__(self, lines: list[str]):
        self._lines = lines

    def raise_for_status(self) -> None:
        pass

    async def aiter_lines(self):
        for ln in self._lines:
            yield ln

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args) -> bool:
        return False


class _FakeAsyncClient:
    """Captures one POST and returns a canned Anthropic-messages payload."""

    def __init__(self, payload: dict, stream_lines: list[str] | None = None):
        self._payload = payload
        self._stream_lines = stream_lines
        self.calls: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args) -> bool:
        return False

    def _factory(self, *args, **kwargs):
        return self

    async def post(self, url, headers=None, json=None):
        self.calls.append(((url,), {"headers": headers, "json": json}))
        return _FakeResponse(self._payload)

    def stream(self, method, url, headers=None, json=None):
        self.calls.append(((method, url), {"headers": headers, "json": json}))
        return _FakeStreamResponse(self._stream_lines or [])


def _install_fake(monkeypatch: pytest.MonkeyPatch, payload: dict, stream_lines=None):
    fake = _FakeAsyncClient(payload, stream_lines)
    monkeypatch.setattr(httpx, "AsyncClient", fake._factory)
    return fake


def _client(**kwargs) -> MiniMaxClient:
    # Env-clean fixture gives default base/model; key comes via kwarg so
    # header assertions can check the exact value.
    return MiniMaxClient(api_key="sk-test", **kwargs)


def _payload(**extra):
    p = {
        "id": "msg_1",
        "model": "MiniMax-M3",
        "content": [{"type": "text", "text": "hello world"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 12, "output_tokens": 4},
    }
    p.update(extra)
    return p


# ── Construction / env ───────────────────────────────────────────────


def test_constructor_defaults_from_env_absent():
    c = MiniMaxClient()
    assert c.config.base_url == "https://api.minimax.io/anthropic"
    assert c.config.model == "MiniMax-M3"
    assert c.config.provider == "minimax"


def test_constructor_explicit_kwargs_win(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-env")
    monkeypatch.setenv("MINIMAX_BASE_URL", "https://env.example/anthropic")
    monkeypatch.setenv("MINIMAX_MODEL", "env-model")
    c = MiniMaxClient(api_key="sk-explicit", base_url="https://x.example", model="m-explicit")
    assert c.config.api_key == "sk-explicit"
    assert c.config.base_url == "https://x.example"
    assert c.config.model == "m-explicit"


def test_constructor_env_fallback(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-env")
    monkeypatch.setenv("MINIMAX_BASE_URL", "https://env.example/anthropic")
    monkeypatch.setenv("MINIMAX_MODEL", "MiniMax-M2.7")
    c = MiniMaxClient()
    assert c.config.api_key == "sk-env"
    assert c.config.base_url == "https://env.example/anthropic"
    assert c.config.model == "MiniMax-M2.7"


def test_constructor_from_llmconfig():
    cfg = LLMConfig(api_key="sk-x", base_url="https://x.example", model="MiniMax-M3")
    c = MiniMaxClient(cfg)
    assert c.config.api_key == "sk-x"


# ── Headers & request shape ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_headers_and_url(monkeypatch):
    fake = _install_fake(monkeypatch, _payload())
    c = _client()
    result = await c.chat([{"role": "user", "content": "hi"}])

    assert len(fake.calls) == 1
    (args, kwargs) = fake.calls[0]
    url = args[0]
    assert url == "https://api.minimax.io/anthropic/v1/messages"
    headers = kwargs["headers"]
    assert headers["x-api-key"] == "sk-test"
    assert headers["anthropic-version"] == "2023-06-01"

    body = kwargs["json"]
    assert body["model"] == "MiniMax-M3"
    assert body["messages"] == [{"role": "user", "content": "hi"}]

    assert result.content == "hello world"
    assert result.model == "MiniMax-M3"
    assert result.finish_reason == "end_turn"
    assert result.usage == {"input_tokens": 12, "output_tokens": 4}


@pytest.mark.asyncio
async def test_chat_tool_use_blocks_parsed(monkeypatch):
    payload = _payload(
        content=[
            {"type": "text", "text": "calling it"},
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "run_shell",
                "input": {"command": "ls"},
            },
        ]
    )
    _install_fake(monkeypatch, payload)
    c = _client()
    result = await c.chat([{"role": "user", "content": "go"}])
    assert result.content == "calling it"
    assert result.tool_calls == [
        {
            "id": "toolu_1",
            "type": "function",
            "function": {"name": "run_shell", "arguments": '{"command": "ls"}'},
        }
    ]


@pytest.mark.asyncio
async def test_chat_stream_collects_tokens_and_headers(monkeypatch):
    lines = [
        'data: {"type": "content_block_start", "content_block": '
        '{"type": "tool_use", "id": "toolu_9", "name": "read_file"}}',
        'data: {"type": "content_block_delta", "delta": '
        '{"type": "text_delta", "text": "streamed"}}',
        'data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, '
        '"usage": {"input_tokens": 5, "output_tokens": 3}}',
        'data: {"type": "message_stop"}',
        "event: ping",
    ]
    fake = _install_fake(monkeypatch, _payload(), stream_lines=lines)
    c = _client()
    seen = []
    result = await c.chat_stream([{"role": "user", "content": "hi"}], on_token=seen.append)

    assert result.content == "streamed"
    assert seen == ["streamed"]
    assert result.finish_reason == "end_turn"
    assert result.tool_calls == [
        {"id": "toolu_9", "type": "function", "function": {"name": "read_file", "arguments": ""}}
    ]

    (args, kwargs) = fake.calls[0]
    assert args[0] == "POST"
    assert args[1] == "https://api.minimax.io/anthropic/v1/messages"
    assert kwargs["headers"]["x-api-key"] == "sk-test"
    assert kwargs["headers"]["anthropic-version"] == "2023-06-01"
    assert kwargs["json"]["stream"] is True


# ── Message-format conversion ────────────────────────────────────────


def test_to_claude_extracts_system_and_shapes():
    c = _client()
    messages = [
        {"role": "system", "content": "you are LuckyD"},
        {"role": "user", "content": "run ls"},
        {
            "role": "assistant",
            "content": "on it",
            "tool_calls": [
                {
                    "id": "tc_1",
                    "type": "function",
                    "function": {"name": "run_shell", "arguments": '{"command": "ls"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "tc_1", "content": "a.txt"},
    ]
    system, claude_msgs = c._to_claude(messages)

    assert system == "you are LuckyD"
    assert claude_msgs[0] == {"role": "user", "content": "run ls"}

    assistant = claude_msgs[1]
    assert assistant["role"] == "assistant"
    assert assistant["content"][0] == {"type": "text", "text": "on it"}
    tool_use = assistant["content"][1]
    assert tool_use == {
        "type": "tool_use",
        "id": "tc_1",
        "name": "run_shell",
        "input": {"command": "ls"},
    }

    tool_result = claude_msgs[2]
    assert tool_result == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "tc_1", "content": "a.txt"}],
    }


def test_convert_tools_to_anthropic_schema():
    c = _client()
    tools = [
        {
            "type": "function",
            "function": {
                "name": "run_shell",
                "description": "run a shell command",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    converted = c._convert_tools(tools)
    assert converted == [
        {
            "name": "run_shell",
            "description": "run a shell command",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]


# ── Provider wiring ──────────────────────────────────────────────────


def test_minimax_in_provider_registry():
    assert "minimax" in VALID_PROVIDERS
    assert PROVIDER_NAMES["minimax"] == "MiniMax"
    assert detect_api_format("minimax") == "anthropic"
    defaults = PROVIDER_DEFAULTS["minimax"]
    assert defaults["env_key"] == "MINIMAX_API_KEY"
    assert defaults["env_base"] == "MINIMAX_BASE_URL"
    assert defaults["env_model"] == "MINIMAX_MODEL"
    assert defaults["default_base"] == "https://api.minimax.io/anthropic"
    assert defaults["default_model"] == "MiniMax-M3"


def test_resolve_provider_config_defaults():
    cfg = resolve_provider_config("minimax")
    assert cfg["provider"] == "minimax"
    assert cfg["base_url"] == "https://api.minimax.io/anthropic"
    assert cfg["model"] == "MiniMax-M3"
    assert cfg["api_key"] == ""


def test_resolve_provider_config_env_overrides(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-env-key")
    monkeypatch.setenv("MINIMAX_BASE_URL", "https://proxy.example/anthropic")
    monkeypatch.setenv("MINIMAX_MODEL", "MiniMax-M2.7-highspeed")
    cfg = resolve_provider_config("minimax")
    assert cfg["api_key"] == "sk-env-key"
    assert cfg["base_url"] == "https://proxy.example/anthropic"
    assert cfg["model"] == "MiniMax-M2.7-highspeed"


def test_create_factory_returns_minimax_client():
    client = LLMClient.create(LLMConfig(provider="minimax", api_key="sk-x"))
    assert isinstance(client, MiniMaxClient)


def test_from_env_explicit_minimax(monkeypatch):
    monkeypatch.setenv("CODING_AGENT_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-env-key")
    monkeypatch.setenv("MINIMAX_MODEL", "MiniMax-M2.7")
    cfg = LLMConfig.from_env()
    assert cfg.provider == "minimax"
    assert cfg.api_key == "sk-env-key"
    assert cfg.base_url == "https://api.minimax.io/anthropic"
    assert cfg.model == "MiniMax-M2.7"
    assert isinstance(LLMClient.create(cfg), MiniMaxClient)


def test_from_env_auto_detect_minimax(monkeypatch):
    monkeypatch.delenv("CODING_AGENT_PROVIDER", raising=False)
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-env-key")
    # Conftest plants placeholder keys for other providers; a real machine
    # runs only the keys it actually uses, so remove the competitors to
    # mirror that and let auto-detection reach the minimax branch.
    for k in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "ZAI_API_KEY",
        "OPENROUTER_API_KEY",
        "OLLAMA_MODEL",
        "CLINEPASS_API_KEY",
        "CLINE_USAGE_MODEL",
    ):
        monkeypatch.delenv(k, raising=False)
    cfg = LLMConfig.from_env()
    assert cfg.provider == "minimax"


def test_mesh_command_is_valid_provider():
    # main.py --provider routes through VALID_PROVIDERS; resolve must work.
    cfg = resolve_provider_config("minimax")
    assert cfg["provider"] == "minimax"
    assert os.environ.get("MINIMAX_API_KEY", "") == cfg["api_key"]
