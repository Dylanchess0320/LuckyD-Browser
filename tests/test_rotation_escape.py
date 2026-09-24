"""LuckyD 10.2.2 rotation hardening: 402 failover, escape legs, cline alias.

Covers the 402 dead-end fix — an exhausted Cline Credits balance used to
surface ``[API Error: 402]`` and stop, even with a working Ollama server
next door. Rotation now treats 402 like the other recoverable codes and
the cross-provider escape chain gained ClinePass (subscription quota)
and Ollama (local, free) legs. Also covers the ``cline`` →
``cline-usage`` canonicalization (the alias briefly shipped as a full
provider entry, duplicating the providers-table row).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

# Intentionally fake API key for tests (not a real secret).
TEST_API_KEY = "test-key-not-a-secret"

_ERR_402 = {"role": "assistant", "content": "[API Error: 402 — payment required] broke"}
_ERR_401 = {"role": "assistant", "content": "[API Error: 401 — authentication failed] nope"}
_GOOD = {"role": "assistant", "content": "OK"}


def _make_agent(**kwargs):
    with patch("llm.ProviderRouter"):
        from core.agent_loop import CodingAgent

        ag = CodingAgent(api_key=TEST_API_KEY, model="test-model", **kwargs)
        ag._extract_session_memories = AsyncMock()
        ag._result_handler = None
        return ag


@pytest.fixture
def clean_provider_env(monkeypatch):
    """Strip provider/auth env so rotation-guard tests are hermetic."""
    from core.contributor import CONTRIBUTOR_ENV

    for var in (
        "CODING_AGENT_PROVIDER",
        "CLINEPASS_API_KEY",
        "CLINE_USAGE_MODEL",
        "CLINEPASS_MODEL",
        "OLLAMA_HOST",
        "OLLAMA_MODEL",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        CONTRIBUTOR_ENV,
    ):
        monkeypatch.delenv(var, raising=False)


def _cline_usage_agent():
    ag = _make_agent()
    ag._provider_config.provider = "cline-usage"
    ag.model = "deepseek/deepseek-v4.1-flash"
    return ag


class TestRotateCodes:
    def test_402_rotates_but_auth_failures_do_not(self):
        from core.agent_loop import CodingAgent

        assert 402 in CodingAgent._ROTATE_CODES
        assert 401 not in CodingAgent._ROTATE_CODES
        assert 403 not in CodingAgent._ROTATE_CODES

    def test_api_error_code_parses_suffixed_messages(self):
        from core.agent_loop import CodingAgent

        assert CodingAgent._api_error_code("[API Error: 402 — payment required] broke") == 402
        assert CodingAgent._api_error_code("[API Error: 403 — authentication failed] nope") == 403
        assert CodingAgent._api_error_code("[API Error: 429 — rate limited] slow") == 429

    def test_clinepass_pool_is_subscription_ids(self):
        from core.agent_loop import CodingAgent

        assert CodingAgent._CLINEPASS_ROTATION_POOL
        assert all(m.startswith("cline-pass/") for m in CodingAgent._CLINEPASS_ROTATION_POOL)


class TestAutoRotate402:
    async def test_same_provider_success_pins_winner(self, clean_provider_env):
        ag = _cline_usage_agent()
        ag.llm_client.chat_stream = AsyncMock(side_effect=[dict(_ERR_402), dict(_GOOD)])

        msg = await ag._auto_rotate_model(dict(_ERR_402), [], None)

        assert msg is not None and msg["content"] == "OK"
        # First pool candidate failed, second pinned — provider untouched.
        assert ag.model == ag._CLINE_ROTATION_POOL[1]
        assert ag._provider_config.provider == "cline-usage"

    async def test_escapes_to_ollama_when_gateway_exhausted(self, clean_provider_env):
        from core.agent_loop import CodingAgent

        ag = _cline_usage_agent()
        calls = []

        async def _chat_stream(*_args, **_kwargs):
            calls.append(ag.model)
            if ag.model == "llama3.2:3b":
                return dict(_GOOD)
            return dict(_ERR_402)

        ollama_cfg = SimpleNamespace(
            api_key="",
            base_url="http://127.0.0.1:11434/v1",
            model="llama3.2:3b",
            temperature=0.0,
            max_tokens=8192,
            provider="ollama",
            thinking=False,
        )
        with (
            patch.object(CodingAgent, "_cline_gateway_usable", return_value=False),
            patch.object(CodingAgent, "_ollama_reachable", return_value=True),
            patch("core.providers.build_llm_config", return_value=ollama_cfg),
            # Class-level: the escape leg rebuilds ag.llm_client, so an
            # instance-level fake would not survive the provider switch.
            patch("core.llm_client.LLMClient.chat_stream", new=_chat_stream),
        ):
            msg = await ag._auto_rotate_model(dict(_ERR_402), [], None)

        assert msg is not None and msg["content"] == "OK"
        assert ag._provider_config.provider == "ollama"
        assert ag.model == "llama3.2:3b"
        assert calls[-1] == "llama3.2:3b"

    async def test_exhausted_rotation_restores_original(self, clean_provider_env):
        from core.agent_loop import CodingAgent

        ag = _cline_usage_agent()
        ag.llm_client.chat_stream = AsyncMock(return_value=dict(_ERR_402))
        with (
            patch.object(CodingAgent, "_cline_gateway_usable", return_value=False),
            patch.object(CodingAgent, "_ollama_reachable", return_value=False),
        ):
            msg = await ag._auto_rotate_model(dict(_ERR_402), [], None)

        assert msg is None
        assert ag.model == "deepseek/deepseek-v4.1-flash"
        assert ag._provider_config.provider == "cline-usage"

    async def test_401_does_not_rotate(self, clean_provider_env):
        ag = _cline_usage_agent()
        ag.llm_client.chat_stream = AsyncMock(return_value=dict(_GOOD))

        msg = await ag._auto_rotate_model(dict(_ERR_401), [], None)

        assert msg is None
        ag.llm_client.chat_stream.assert_not_called()


class TestClineAlias:
    def test_cline_missing_from_provider_sets(self):
        from core.providers import FREE_TIER_PROVIDERS, PROVIDER_ORDER, VALID_PROVIDERS

        assert "cline" not in VALID_PROVIDERS
        assert "cline" not in PROVIDER_ORDER
        assert "cline" not in FREE_TIER_PROVIDERS

    def test_normalize_maps_alias(self):
        from core.providers import normalize_provider_id

        assert normalize_provider_id("cline") == "cline-usage"
        assert normalize_provider_id("cline-pass") == "clinepass"
        assert normalize_provider_id("ollama") == "ollama"

    def test_build_config_canonicalizes_cline(self, clean_provider_env, monkeypatch):
        from core.providers import build_llm_config

        monkeypatch.setenv("CLINEPASS_API_KEY", TEST_API_KEY)
        assert build_llm_config("cline").provider == "cline-usage"

    def test_detect_provider_canonicalizes_cline(self, clean_provider_env, monkeypatch):
        from core.providers import detect_provider

        monkeypatch.setenv("CODING_AGENT_PROVIDER", "cline")
        assert detect_provider() == "cline-usage"

    def test_list_providers_has_no_alias_row(self, clean_provider_env):
        from core.providers import list_providers

        ids = [p["id"] for p in list_providers()]
        assert "cline" not in ids
        assert "cline-usage" in ids


def _status_error(code: int, base_url: str, detail: str) -> httpx.HTTPStatusError:
    from core.llm_client import LLMClient

    client = LLMClient(api_key=TEST_API_KEY, base_url=base_url, model="m")
    req = httpx.Request("POST", f"{base_url}/chat/completions")
    resp = httpx.Response(code, json={"error": {"code": "x", "message": detail}}, request=req)
    return client, httpx.HTTPStatusError(f"{code}", request=req, response=resp)


class TestErrorGuidance:
    def test_402_message_has_actionable_hint(self):
        client, err = _status_error(402, "https://api.cline.bot/api/v1", "broke")
        out = client._handle_http_error(err, attempt=0)
        assert out["content"].startswith("[API Error: 402 — payment required]")
        assert "/model ollama" in out["content"]

    def test_403_on_cline_gateway_skips_clinepass_hint(self):
        client, err = _status_error(
            403, "https://api.cline.bot/api/v1", "ENTITLEMENT_ERROR: no plan"
        )
        out = client._handle_http_error(err, attempt=0)
        assert "switch to ClinePass" not in out["content"]
        assert "free path" in out["content"]

    def test_403_off_gateway_keeps_clinepass_hint(self):
        client, err = _status_error(403, "https://api.openai.com/v1", "bad key")
        out = client._handle_http_error(err, attempt=0)
        assert "switch to ClinePass" in out["content"]
