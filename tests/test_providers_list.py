"""Tests for the AI provider list (core/providers.list_providers)."""

from __future__ import annotations

import os

import pytest

from core.providers import (
    FREE_TIER_PROVIDERS,
    PROVIDER_DEFAULTS,
    PROVIDER_ORDER,
    VALID_PROVIDERS,
    list_providers,
)

REQUIRED_KEYS = {
    "id",
    "name",
    "base_url",
    "model",
    "env_key",
    "key_present",
    "local",
    "free_tier",
    "configured",
    "current",
}


@pytest.fixture
def clean_env(monkeypatch):
    """Strip provider-related env vars so tests are hermetic."""
    for var in (
        "CODING_AGENT_PROVIDER",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "ZAI_API_KEY",
        "OPENCODE_API_KEY",
        "OPENROUTER_API_KEY",
        "GROQ_API_KEY",
        "DEEPSEEK_API_KEY",
        "CODING_AGENT_API_KEY",
        "CLINEPASS_API_KEY",
        "CLINE_USAGE_MODEL",
        "CLINEPASS_MODEL",
        "OLLAMA_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


class TestListProviders:
    def test_covers_all_valid_providers(self, clean_env):
        ids = {p["id"] for p in list_providers()}
        assert ids == set(VALID_PROVIDERS)
        assert set(PROVIDER_ORDER) == set(VALID_PROVIDERS)

    def test_entry_shape(self, clean_env):
        for entry in list_providers():
            assert set(entry) >= REQUIRED_KEYS, f"missing keys in {entry['id']}"
            assert entry["name"]
            assert entry["base_url"].startswith(("http://", "https://"))
            assert entry["model"]

    def test_ollama_always_configured_keyless(self, clean_env):
        ollama = next(p for p in list_providers() if p["id"] == "ollama")
        assert ollama["local"] is True
        assert ollama["env_key"] is None
        assert ollama["configured"] is True

    def test_key_gates_cloud_providers(self, clean_env, monkeypatch):
        by_id = {p["id"]: p for p in list_providers()}
        assert by_id["openai"]["configured"] is False
        assert by_id["openai"]["key_present"] is False

        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        by_id = {p["id"]: p for p in list_providers()}
        assert by_id["openai"]["configured"] is True
        assert by_id["openai"]["key_present"] is True

    def test_current_follows_explicit_provider(self, clean_env, monkeypatch):
        monkeypatch.setenv("CODING_AGENT_PROVIDER", "groq")
        current = [p["id"] for p in list_providers() if p["current"]]
        assert current == ["groq"]

    def test_free_tier_flags(self, clean_env):
        by_id = {p["id"]: p for p in list_providers()}
        for pid in FREE_TIER_PROVIDERS:
            assert by_id[pid]["free_tier"] is True, pid
        assert by_id["anthropic"]["free_tier"] is False
        assert by_id["deepseek"]["free_tier"] is False

    def test_env_overrides_surface(self, clean_env, monkeypatch):
        monkeypatch.setenv("GROQ_MODEL", "groq/compound-test")
        groq = next(p for p in list_providers() if p["id"] == "groq")
        assert groq["model"] == "groq/compound-test"

    def test_env_key_matches_defaults(self, clean_env):
        for entry in list_providers():
            assert entry["env_key"] == PROVIDER_DEFAULTS[entry["id"]]["env_key"]

    def test_no_network_needed(self, clean_env, monkeypatch):
        # list_providers must never touch the network: block sockets entirely.
        import socket

        def _blocked(*a, **k):
            raise AssertionError("network call during list_providers")

        monkeypatch.setattr(socket, "create_connection", _blocked)
        monkeypatch.setattr("httpx.get", _blocked, raising=False)
        monkeypatch.setattr("httpx.Client", _blocked, raising=False)
        providers = list_providers()
        assert len(providers) == len(VALID_PROVIDERS)
        # os import sanity (keeps linters quiet about unused imports in some envs)
        assert os.environ is not None
