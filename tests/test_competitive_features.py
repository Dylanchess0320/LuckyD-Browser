"""Tests for LuckyD Code 2.1.0 competitive features — providers, sessions, rules."""

from __future__ import annotations

import os


class TestProviders:
    def test_zai_provider_resolution(self):
        from core.providers import resolve_provider_config

        os.environ["ZAI_API_KEY"] = "sk-test-zai"
        cfg = resolve_provider_config("zai")
        assert cfg["provider"] == "zai"
        assert cfg["api_key"] == "sk-test-zai"
        assert "z.ai" in cfg["base_url"]
        assert cfg["model"] == "glm-4.5"

    def test_openrouter_provider_resolution(self):
        from core.providers import resolve_provider_config

        os.environ["OPENROUTER_API_KEY"] = "sk-test-or"
        cfg = resolve_provider_config("openrouter")
        assert cfg["provider"] == "openrouter"
        assert cfg["api_key"] == "sk-test-or"
        assert "openrouter.ai" in cfg["base_url"]

    def test_zai_auto_detect(self):
        from core.providers import detect_provider

        os.environ["ZAI_API_KEY"] = "sk-test-zai"
        # Clear other provider keys that conftest sets, plus any explicit
        # override coming from the local .env, so detection is deterministic.
        for key in (
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GOOGLE_API_KEY",
            "OPENROUTER_API_KEY",
            "CODING_AGENT_PROVIDER",
        ):
            os.environ.pop(key, None)
        assert detect_provider() == "zai"

    def test_valid_providers_includes_new(self):
        from core.providers import VALID_PROVIDERS

        assert "zai" in VALID_PROVIDERS
        assert "openrouter" in VALID_PROVIDERS

    def test_zai_uses_openai_client(self):
        from llm import LLMClient, LLMConfig

        config = LLMConfig(
            api_key="sk-test",
            base_url="https://api.z.ai/api/paas/v4",
            model="glm-4.5",
            provider="zai",
        )
        client = LLMClient.create(config)
        assert client.__class__.__name__ == "OpenAIClient"

    def test_openrouter_uses_openai_client(self):
        from llm import LLMClient, LLMConfig

        config = LLMConfig(
            api_key="sk-test",
            base_url="https://openrouter.ai/api/v1",
            model="deepseek/deepseek-chat-v3.1",
            provider="openrouter",
        )
        client = LLMClient.create(config)
        assert client.__class__.__name__ == "OpenAIClient"

    def test_glm_cost_pricing(self):
        from llm import MODEL_COSTS

        assert "glm-4.6" in MODEL_COSTS
        assert "glm-4.5" in MODEL_COSTS
        assert MODEL_COSTS["glm-4.5"]["input"] > 0


class TestSessionStore:
    def test_save_and_load(self, tmp_path):
        from core.session_store import SessionStore

        store = SessionStore(sessions_dir=tmp_path)
        messages = [
            {"role": "system", "content": "You are a helper."},
            {"role": "user", "content": "Build a web server"},
            {"role": "assistant", "content": "Here's the code..."},
        ]
        path = store.save("conv_test_001", messages, model="glm-4.5", provider="zai")
        assert path.exists()
        loaded = store.load("conv_test_001")
        assert loaded is not None
        assert loaded["conversation_id"] == "conv_test_001"
        assert loaded["model"] == "glm-4.5"
        assert len(loaded["messages"]) == 3

    def test_prefix_match(self, tmp_path):
        from core.session_store import SessionStore

        store = SessionStore(sessions_dir=tmp_path)
        store.save("conv_20260722_120000", [{"role": "user", "content": "hello"}])
        loaded = store.load("conv_20260722")
        assert loaded is not None
        assert loaded["conversation_id"] == "conv_20260722_120000"

    def test_list_sessions(self, tmp_path):
        from core.session_store import SessionStore

        store = SessionStore(sessions_dir=tmp_path)
        store.save("conv_a", [{"role": "user", "content": "first"}], model="gpt-4o")
        store.save("conv_b", [{"role": "user", "content": "second"}], model="glm-4.5")
        sessions = store.list(limit=10)
        assert len(sessions) == 2

    def test_latest_session(self, tmp_path):
        import time

        from core.session_store import SessionStore

        store = SessionStore(sessions_dir=tmp_path)
        store.save("conv_a", [{"role": "user", "content": "first"}])
        time.sleep(0.01)
        store.save("conv_b", [{"role": "user", "content": "second"}])
        latest = store.latest()
        assert latest is not None
        assert latest["conversation_id"] == "conv_b"

    def test_delete_session(self, tmp_path):
        from core.session_store import SessionStore

        store = SessionStore(sessions_dir=tmp_path)
        store.save("conv_x", [{"role": "user", "content": "hello"}])
        assert store.load("conv_x") is not None
        assert store.delete("conv_x") is True
        assert store.load("conv_x") is None


class TestRulesLoader:
    def test_loads_agents_md(self, tmp_path):
        from core.rules_loader import load_project_rules

        (tmp_path / "AGENTS.md").write_text("# AGENTS\nAlways write tests.", encoding="utf-8")
        rules = load_project_rules(extra_dirs=[tmp_path])
        assert "AGENTS" in rules
        assert "Always write tests" in rules

    def test_loads_clinerules(self, tmp_path):
        from core.rules_loader import load_project_rules

        (tmp_path / ".clinerules").write_text("Use 4-space indent.", encoding="utf-8")
        rules = load_project_rules(extra_dirs=[tmp_path])
        assert "4-space indent" in rules

    def test_loads_goosehints(self, tmp_path):
        from core.rules_loader import load_project_rules

        (tmp_path / ".goosehints").write_text("Run ruff before commit.", encoding="utf-8")
        rules = load_project_rules(extra_dirs=[tmp_path])
        assert "ruff" in rules

    def test_no_rules_returns_empty(self, tmp_path):
        from core.rules_loader import load_project_rules

        rules = load_project_rules(extra_dirs=[tmp_path])
        assert rules == ""

    def test_truncation(self, tmp_path):
        from core.rules_loader import load_project_rules

        (tmp_path / "AGENTS.md").write_text("x" * 100000, encoding="utf-8")
        rules = load_project_rules(extra_dirs=[tmp_path])
        assert len(rules) < 100000
