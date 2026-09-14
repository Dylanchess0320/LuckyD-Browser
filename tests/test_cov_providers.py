"""Coverage push for core/providers.py — detection, settings mirroring,
ClinePass session fallback, Ollama normalization, DeepSeek auto-resolve,
and build_llm_config. No network: httpx and session modules are faked."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

import core.providers as providers
from core.providers import (
    _assistant_browser_settings,
    build_llm_config,
    detect_provider,
    resolve_provider_config,
)

# ── helpers ──────────────────────────────────────────────────────────────

_PROVIDER_ENV_VARS = [
    "CODING_AGENT_PROVIDER",
    "CLINEPASS_API_KEY",
    "CLINEPASS_BASE_URL",
    "CLINEPASS_MODEL",
    "CLINE_USAGE_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "GOOGLE_API_KEY",
    "GOOGLE_BASE_URL",
    "GOOGLE_MODEL",
    "ZAI_API_KEY",
    "ZAI_BASE_URL",
    "ZAI_MODEL",
    "OPENCODE_API_KEY",
    "OPENCODE_BASE_URL",
    "OPENCODE_MODEL",
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL",
    "OPENROUTER_MODEL",
    "OLLAMA_MODEL",
    "OLLAMA_HOST",
    "GROQ_API_KEY",
    "DEEPSEEK_API_KEY",
    "CODING_AGENT_API_KEY",
    "CODING_AGENT_BASE_URL",
    "CODING_AGENT_MODEL",
    "CODING_AGENT_THINKING",
    "CLINE_DATA_DIR",
    "CLINE_SESSION_PROVIDER",
]


@pytest.fixture
def clean_env(monkeypatch):
    """Remove every provider-related env var so detection is deterministic."""
    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


class _FakeHttpxResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


def _fake_httpx_get(status_code=200, payload=None, exc=None):
    def _get(url, **kwargs):
        if exc is not None:
            raise exc
        return _FakeHttpxResponse(status_code=status_code, payload=payload)

    return _get


class _FakeClineSession:
    """Hand-written fake for the cline_session module."""

    def __init__(self, token="tok-abc", exc=None):
        self._token = token
        self._exc = exc

    def fresh_token(self):
        if self._exc is not None:
            raise self._exc
        return self._token


class _FakeModelResolver:
    """Hand-written fake for the model_resolver module."""

    def __init__(self):
        self.calls = []

    def resolve_model(self, api_key="", base_url="", preferred="auto", thinking=False):
        self.calls.append(
            {"api_key": api_key, "base_url": base_url, "preferred": preferred, "thinking": thinking}
        )
        return "deepseek-v4-flash"


def _write_settings(tmp_path: Path, payload: str) -> Path:
    settings = tmp_path / "browser" / "data"
    settings.mkdir(parents=True, exist_ok=True)
    path = settings / "settings.json"
    path.write_text(payload, encoding="utf-8")
    return path


def _freeze_sys(monkeypatch, tmp_path: Path):
    """Pretend to be the frozen exe rooted at tmp_path."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "LuckyD.exe"), raising=False)


# ── detect_provider ──────────────────────────────────────────────────────


class TestDetectProvider:
    def test_explicit_provider_wins(self, clean_env):
        clean_env.setenv("CODING_AGENT_PROVIDER", "  OpenAI ")
        assert detect_provider() == "openai"

    def test_explicit_unknown_provider_falls_through(self, clean_env, monkeypatch):
        clean_env.setenv("CODING_AGENT_PROVIDER", "mystery")
        monkeypatch.setattr(httpx, "get", _fake_httpx_get(exc=ConnectionError("refused")))
        assert detect_provider() is None

    def test_priority_order_clinepass_first(self, clean_env):
        clean_env.setenv("CLINEPASS_API_KEY", "cp-key")
        clean_env.setenv("OPENAI_API_KEY", "sk-x")
        assert detect_provider() == "clinepass"

    def test_priority_order_cline_usage(self, clean_env):
        clean_env.setenv("CLINE_USAGE_MODEL", "deepseek/deepseek-chat")
        clean_env.setenv("OPENAI_API_KEY", "sk-x")
        assert detect_provider() == "cline-usage"

    def test_openai_key(self, clean_env):
        clean_env.setenv("OPENAI_API_KEY", "sk-x")
        assert detect_provider() == "openai"

    def test_deepseek_key(self, clean_env):
        clean_env.setenv("DEEPSEEK_API_KEY", "sk-d")
        assert detect_provider() == "deepseek"

    def test_coding_agent_key_counts_as_deepseek(self, clean_env):
        clean_env.setenv("CODING_AGENT_API_KEY", "sk-ca")
        assert detect_provider() == "deepseek"

    def test_ollama_autodetect_success(self, clean_env, monkeypatch):
        monkeypatch.setattr(httpx, "get", _fake_httpx_get(status_code=200))
        assert detect_provider() == "ollama"

    def test_ollama_autodetect_non_200(self, clean_env, monkeypatch):
        monkeypatch.setattr(httpx, "get", _fake_httpx_get(status_code=404))
        assert detect_provider() is None

    def test_ollama_autodetect_connection_error(self, clean_env, monkeypatch):
        monkeypatch.setattr(httpx, "get", _fake_httpx_get(exc=ConnectionError("refused")))
        assert detect_provider() is None

    def test_nothing_detected_returns_none(self, clean_env, monkeypatch):
        monkeypatch.setattr(httpx, "get", _fake_httpx_get(exc=ConnectionError("refused")))
        assert detect_provider() is None


# ── _assistant_browser_settings ──────────────────────────────────────────


class TestAssistantBrowserSettings:
    def test_frozen_missing_file_returns_empty(self, clean_env, monkeypatch, tmp_path):
        _freeze_sys(monkeypatch, tmp_path)
        assert _assistant_browser_settings() == {}

    def test_frozen_reads_settings_json(self, clean_env, monkeypatch, tmp_path):
        _freeze_sys(monkeypatch, tmp_path)
        _write_settings(tmp_path, json.dumps({"ai_provider": "openai"}))
        assert _assistant_browser_settings() == {"ai_provider": "openai"}

    def test_frozen_non_dict_json_returns_empty(self, clean_env, monkeypatch, tmp_path):
        _freeze_sys(monkeypatch, tmp_path)
        _write_settings(tmp_path, "[1, 2, 3]")
        assert _assistant_browser_settings() == {}

    def test_frozen_corrupt_json_returns_empty(self, clean_env, monkeypatch, tmp_path):
        _freeze_sys(monkeypatch, tmp_path)
        _write_settings(tmp_path, "{not valid json")
        assert _assistant_browser_settings() == {}


# ── resolve_provider_config ──────────────────────────────────────────────


class TestResolveProviderConfig:
    def test_explicit_env_provider(self, clean_env):
        clean_env.setenv("CODING_AGENT_PROVIDER", "google")
        clean_env.setenv("GOOGLE_API_KEY", "g-key")
        cfg = resolve_provider_config()
        assert cfg["provider"] == "google"
        assert cfg["api_key"] == "g-key"

    def test_explicit_arg_beats_env(self, clean_env):
        clean_env.setenv("CODING_AGENT_PROVIDER", "google")
        clean_env.setenv("OPENAI_API_KEY", "sk-x")
        cfg = resolve_provider_config("openai")
        assert cfg["provider"] == "openai"

    def test_unknown_provider_falls_back_to_deepseek_defaults(self, clean_env, monkeypatch):
        monkeypatch.setitem(sys.modules, "model_resolver", None)  # force ImportError
        cfg = resolve_provider_config("mystery")
        assert cfg["provider"] == "mystery"
        assert cfg["base_url"] == "https://api.deepseek.com/v1"
        assert cfg["model"] == "deepseek-chat"

    def test_mirror_browser_model_override(self, clean_env, monkeypatch):
        monkeypatch.setattr(
            providers,
            "_assistant_browser_settings",
            lambda: {
                "ai_provider": "openai",
                "ai_model_overrides": {"openai": "gpt-4o-mini"},
            },
        )
        clean_env.setenv("OPENAI_API_KEY", "sk-x")
        cfg = resolve_provider_config()
        assert cfg["provider"] == "openai"
        assert cfg["model"] == "gpt-4o-mini"

    def test_mirror_overrides_not_a_dict(self, clean_env, monkeypatch):
        monkeypatch.setattr(
            providers,
            "_assistant_browser_settings",
            lambda: {"ai_provider": "openai", "ai_model_overrides": "nope"},
        )
        clean_env.setenv("OPENAI_API_KEY", "sk-x")
        cfg = resolve_provider_config()
        assert cfg["provider"] == "openai"
        assert cfg["model"] == "gpt-4o"  # default, no mirror model applied

    def test_mirror_env_model_beats_browser_pick(self, clean_env, monkeypatch):
        monkeypatch.setattr(
            providers,
            "_assistant_browser_settings",
            lambda: {
                "ai_provider": "openai",
                "ai_model_overrides": {"openai": "gpt-4o-mini"},
            },
        )
        clean_env.setenv("OPENAI_API_KEY", "sk-x")
        clean_env.setenv("OPENAI_MODEL", "gpt-4o-turbo")
        cfg = resolve_provider_config()
        assert cfg["model"] == "gpt-4o-turbo"

    def test_mirror_invalid_provider_falls_back_to_detect(self, clean_env, monkeypatch):
        monkeypatch.setattr(
            providers, "_assistant_browser_settings", lambda: {"ai_provider": "mystery"}
        )
        monkeypatch.setattr(httpx, "get", _fake_httpx_get(exc=ConnectionError("refused")))
        monkeypatch.setitem(sys.modules, "model_resolver", None)
        cfg = resolve_provider_config()
        assert cfg["provider"] == "deepseek"

    def test_mirror_overrides_missing_provider_key(self, clean_env, monkeypatch):
        monkeypatch.setattr(
            providers,
            "_assistant_browser_settings",
            lambda: {"ai_provider": "openai", "ai_model_overrides": {"anthropic": "x"}},
        )
        clean_env.setenv("OPENAI_API_KEY", "sk-x")
        cfg = resolve_provider_config()
        assert cfg["provider"] == "openai"
        assert cfg["model"] == "gpt-4o"  # no override for this provider

    def test_clinepass_import_fallback_inserts_path(self, clean_env, monkeypatch):
        # bc NOT pre-added: the fallback must insert it into sys.path.
        monkeypatch.setitem(sys.modules, "cline_session", None)
        bc = str(Path(providers.__file__).resolve().parent.parent / "browser" / "browser_core")
        saved = list(sys.path)
        try:
            sys.path[:] = [p for p in sys.path if p != bc]
            monkeypatch.setitem(sys.modules, "model_resolver", None)
            cfg = resolve_provider_config("clinepass")
        finally:
            sys.path[:] = saved
        assert cfg["provider"] == "deepseek"
        assert cfg["api_key"] == ""

    def test_clinepass_with_api_key_skips_session(self, clean_env):
        clean_env.setenv("CLINEPASS_API_KEY", "cp-key")
        cfg = resolve_provider_config("clinepass")
        assert cfg["api_key"] == "cp-key"
        assert cfg["provider"] == "clinepass"

    def test_clinepass_session_token_success(self, clean_env, monkeypatch):
        monkeypatch.setitem(sys.modules, "cline_session", _FakeClineSession("tok-abc"))
        cfg = resolve_provider_config("clinepass")
        assert cfg["api_key"] == "tok-abc"
        assert cfg["provider"] == "clinepass"

    def test_clinepass_session_failure_explicit_warns(self, clean_env, monkeypatch, capsys):
        # Force `import cline_session` to raise ImportError on both attempts.
        monkeypatch.setitem(sys.modules, "cline_session", None)
        bc = str(Path(providers.__file__).resolve().parent.parent / "browser" / "browser_core")
        monkeypatch.syspath_prepend(bc)
        clean_env.setenv("CODING_AGENT_PROVIDER", "clinepass")
        cfg = resolve_provider_config("clinepass")
        assert cfg["api_key"] == ""
        assert cfg["provider"] == "clinepass"
        assert "[AUTH]" in capsys.readouterr().out

    def test_clinepass_session_failure_falls_back_to_deepseek(self, clean_env, monkeypatch):
        monkeypatch.setitem(sys.modules, "cline_session", None)
        bc = str(Path(providers.__file__).resolve().parent.parent / "browser" / "browser_core")
        monkeypatch.syspath_prepend(bc)
        monkeypatch.setitem(sys.modules, "model_resolver", None)
        cfg = resolve_provider_config("clinepass")
        assert cfg["provider"] == "deepseek"
        assert cfg["api_key"] == ""

    def test_ollama_normalizes_base_and_picks_model(self, clean_env, monkeypatch):
        clean_env.setenv("OLLAMA_HOST", "http://localhost:11434")
        payload = {"data": [{"id": "qwen2.5:7b"}, {"id": "mistral"}]}
        monkeypatch.setattr(httpx, "get", _fake_httpx_get(status_code=200, payload=payload))
        cfg = resolve_provider_config("ollama")
        assert cfg["base_url"] == "http://localhost:11434/v1"
        assert cfg["model"] == "qwen2.5:7b"  # prefers qwen/llama3 over first entry

    def test_ollama_keeps_model_when_available(self, clean_env, monkeypatch):
        clean_env.setenv("OLLAMA_MODEL", "mistral")
        payload = {"data": [{"id": "mistral"}, {"id": "qwen2.5:7b"}]}
        monkeypatch.setattr(httpx, "get", _fake_httpx_get(status_code=200, payload=payload))
        cfg = resolve_provider_config("ollama")
        assert cfg["model"] == "mistral"

    def test_ollama_models_request_fails(self, clean_env, monkeypatch):
        monkeypatch.setattr(httpx, "get", _fake_httpx_get(exc=ConnectionError("refused")))
        cfg = resolve_provider_config("ollama")
        assert cfg["base_url"] == "http://127.0.0.1:11434/v1"
        assert cfg["model"] == "llama3.2:3b"

    def test_ollama_models_non_200(self, clean_env, monkeypatch):
        monkeypatch.setattr(httpx, "get", _fake_httpx_get(status_code=500))
        cfg = resolve_provider_config("ollama")
        assert cfg["model"] == "llama3.2:3b"

    def test_deepseek_auto_uses_resolver(self, clean_env, monkeypatch):
        fake = _FakeModelResolver()
        monkeypatch.setitem(sys.modules, "model_resolver", fake)
        clean_env.setenv("DEEPSEEK_API_KEY", "sk-d")
        cfg = resolve_provider_config("deepseek")
        assert cfg["model"] == "deepseek-v4-flash"
        assert fake.calls[0]["preferred"] == "auto"
        assert fake.calls[0]["thinking"] is False
        assert fake.calls[0]["api_key"] == "sk-d"

    def test_deepseek_auto_resolver_import_error(self, clean_env, monkeypatch):
        monkeypatch.setitem(sys.modules, "model_resolver", None)
        cfg = resolve_provider_config("deepseek")
        assert cfg["model"] == "deepseek-chat"

    def test_deepseek_explicit_model_skips_resolver(self, clean_env, monkeypatch):
        fake = _FakeModelResolver()
        monkeypatch.setitem(sys.modules, "model_resolver", fake)
        clean_env.setenv("CODING_AGENT_MODEL", "deepseek-reasoner")
        cfg = resolve_provider_config("deepseek")
        assert cfg["model"] == "deepseek-reasoner"
        assert fake.calls == []

    def test_thinking_flag(self, clean_env, monkeypatch):
        monkeypatch.setitem(sys.modules, "model_resolver", None)
        clean_env.setenv("CODING_AGENT_THINKING", "yes")
        cfg = resolve_provider_config("deepseek")
        assert cfg["thinking"] is True


# ── build_llm_config ─────────────────────────────────────────────────────


class TestBuildLlmConfig:
    def test_build_llm_config(self, clean_env):
        clean_env.setenv("OPENAI_API_KEY", "sk-x")
        clean_env.setenv("OPENAI_MODEL", "gpt-4o-mini")
        cfg = build_llm_config("openai")
        assert cfg.api_key == "sk-x"
        assert cfg.base_url == "https://api.openai.com/v1"
        assert cfg.model == "gpt-4o-mini"
        assert cfg.provider == "openai"
        assert cfg.thinking is False
