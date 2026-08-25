class TestProviders:
    def test_resolve_provider_config(self, monkeypatch):
        from core.providers import resolve_provider_config

        cfg = resolve_provider_config()
        assert isinstance(cfg, dict)
        assert "provider" in cfg
        assert "model" in cfg

    def test_detect_api_format(self):
        from core.providers import detect_api_format

        assert detect_api_format("openai") == "openai"
        assert detect_api_format("deepseek") == "openai"
        assert detect_api_format("anthropic") == "anthropic"


class TestMessageBuilder:
    def test_build_system(self):
        from core.message_builder import MessageBuilder

        builder = MessageBuilder()
        result = builder.build_system("test-provider", "test-model", "test-tools")
        assert isinstance(result, str)

    def test_build_truncation_notice(self):
        from core.message_builder import MessageBuilder

        builder = MessageBuilder()
        msg = builder.build_truncation_notice()
        assert msg is not None
        assert isinstance(msg, dict)
        assert msg.get("role") == "system"


class TestContextManager:
    def test_estimate_tokens(self):
        from core.context_manager import estimate_tokens

        assert estimate_tokens("") == 0
        assert estimate_tokens("hello world") > 0

    def test_truncate_messages_under_limit(self):
        from core.context_manager import truncate_messages

        msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
        r = truncate_messages(msgs, max_messages=40)
        assert len(r) == 2

    def test_truncate_preserves_tool_pairs(self):
        from core.context_manager import truncate_messages

        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "do"},
            {"role": "assistant", "content": "ok", "tool_calls": [{"id": "c1"}]},
            {"role": "tool", "content": "result", "tool_call_id": "c1"},
        ]
        r = truncate_messages(msgs, max_messages=3, keep_recent=2)
        assert len(r) >= 3


class TestHooks:
    def test_register_before_tool(self):
        from core.hooks import get_hooks, reset_hooks

        reset_hooks()
        hooks = get_hooks()
        results = []
        hooks.register_before_tool(lambda name, args, ctx: results.append(name))
        hooks.before_tool[0]("bash", {}, None)
        assert "bash" in results

    def test_reset_hooks(self):
        from core.hooks import get_hooks, reset_hooks

        reset_hooks()
        hooks = get_hooks()
        hooks.register_before_tool(lambda n, a, c: None)
        reset_hooks()
        fresh = get_hooks()
        assert fresh is not None
        assert len(fresh.before_tool) == 0


class TestCheckpoint:
    def test_record_and_list(self, tmp_path, monkeypatch):
        from core.checkpoint import CheckpointManager

        monkeypatch.chdir(tmp_path)
        cm = CheckpointManager()
        cp = cm.record_change("/tmp/t.py", "old content", "new content")
        assert cp.checkpoint_id.startswith("cp_")
        assert cp.content_before == "old content"
        lst = cm.list_checkpoints()
        assert len(lst) == 1

    def test_clear(self, tmp_path, monkeypatch):
        from core.checkpoint import CheckpointManager

        monkeypatch.chdir(tmp_path)
        cm = CheckpointManager()
        cm.record_change("/tmp/a.py", "a", "b")
        cm.clear()
        assert len(cm.list_checkpoints()) == 0


class TestLLMClient:
    def test_create_openai(self):
        from llm import LLMClient, LLMConfig

        cfg = LLMConfig(
            api_key="sk-test", base_url="https://test.com/v1", model="gpt-4o", provider="openai"
        )
        client = LLMClient.create(cfg)
        assert client is not None
