"""LuckyD 10.5 — provider/model switching polish.

Covers the health snapshot (``list_providers()`` rotation/TTL/last-working
fields, ``/api/providers`` on web_server + the agents bridge), the
last-known-working memory (``core/last_working.py``), the rotator's live
active pair, the LLM success recording, the bridge best-free + live re-read,
and the ``lucky-code model`` in-process hot reload.

Hermetic: provider env is stripped per-test, the Cline session token and the
Ollama probe are stubbed, and the credit/last-working state files point at
per-test paths (see tests/conftest.py). Only fake keys are ever used.
"""

from __future__ import annotations

import http.client
import importlib.util
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Intentionally fake API keys for tests (not real secrets).
TEST_API_KEY = "test-key-not-a-secret"

_PROVIDER_ENV_VARS = (
    "CODING_AGENT_PROVIDER",
    "CODING_AGENT_API_KEY",
    "CODING_AGENT_BASE_URL",
    "CODING_AGENT_MODEL",
    "CLINEPASS_API_KEY",
    "CLINEPASS_BASE_URL",
    "CLINEPASS_MODEL",
    "CLINE_USAGE_MODEL",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "ZAI_API_KEY",
    "OPENROUTER_API_KEY",
    "GROQ_API_KEY",
    "OPENCODE_API_KEY",
    "MINIMAX_API_KEY",
    "DEEPSEEK_API_KEY",
    "OLLAMA_HOST",
    "OLLAMA_MODEL",
)


@pytest.fixture
def clean_env(monkeypatch):
    """Strip provider/auth env; no Cline session; Ollama probe refuses."""
    from core import providers

    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    providers.reset_cline_session_cache()
    monkeypatch.setattr(providers, "cline_session_token", lambda: "")

    def _refuse(*a, **k):
        raise ConnectionError("no network in provider-health tests")

    monkeypatch.setattr(httpx, "get", _refuse)
    return monkeypatch


def _bridge_module():
    """Import apps/luckyd-ui/scripts/luckyd_agents_bridge.py by path."""
    path = _REPO_ROOT / "apps" / "luckyd-ui" / "scripts" / "luckyd_agents_bridge.py"
    spec = importlib.util.spec_from_file_location("luckyd_agents_bridge_105", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["luckyd_agents_bridge_105"] = module
    spec.loader.exec_module(module)
    return module


# ── health snapshot: list_providers() ──────────────────────────────────


class TestHealthSnapshot:
    def test_new_keys_present(self, clean_env):
        from core.providers import list_providers

        for entry in list_providers():
            assert entry["rotation_order"] is None or isinstance(entry["rotation_order"], int)
            assert isinstance(entry["next_in_rotation"], bool)
            assert isinstance(entry["credit_ttl_remaining_sec"], int)
            assert isinstance(entry["last_working"], bool)
            assert entry["last_working_ago"] is None or isinstance(entry["last_working_ago"], str)

    def test_rotation_order_matches_priority(self, clean_env):
        from core.free_rotation import FREE_MODEL_PRIORITY
        from core.providers import list_providers

        by_id = {p["id"]: p for p in list_providers()}
        for index, (pid, _model) in enumerate(FREE_MODEL_PRIORITY):
            assert by_id[pid]["rotation_order"] == index
        # Paid-only providers are not in the rotation.
        assert by_id["openai"]["rotation_order"] is None
        assert by_id["anthropic"]["rotation_order"] is None

    def test_next_in_rotation_matches_best_free(self, clean_env):
        from core.free_rotation import best_free_provider
        from core.providers import list_providers

        clean_env.setenv("GROQ_API_KEY", TEST_API_KEY)
        best = best_free_provider()
        assert best == "groq"
        flagged = [p["id"] for p in list_providers() if p["next_in_rotation"]]
        assert flagged == [best]

    def test_next_in_rotation_empty_when_nothing_usable(self, clean_env):
        from core.providers import list_providers

        assert [p for p in list_providers() if p["next_in_rotation"]] == []

    def test_credit_ttl_remaining_on_cline_rows(self, clean_env):
        import time

        from core.cline_credit import (
            CREDIT_STATE_TTL_SEC,
            record_cline_credit_exhausted,
        )
        from core.providers import list_providers

        assert record_cline_credit_exhausted("test 402") is True
        by_id = {p["id"]: p for p in list_providers()}
        for pid in ("clinepass", "cline-usage"):
            ttl = by_id[pid]["credit_ttl_remaining_sec"]
            assert CREDIT_STATE_TTL_SEC - 60 < ttl <= CREDIT_STATE_TTL_SEC
            assert by_id[pid]["credit_exhausted"] is True
        assert by_id["groq"]["credit_ttl_remaining_sec"] == 0

        # Expired markers report 0.
        assert record_cline_credit_exhausted("old", now=time.time() - CREDIT_STATE_TTL_SEC - 1)
        by_id = {p["id"]: p for p in list_providers()}
        assert by_id["cline-usage"]["credit_ttl_remaining_sec"] == 0

    def test_last_working_flags(self, clean_env):
        from core.last_working import record_last_working
        from core.providers import list_providers

        assert record_last_working("groq", "groq/compound-mini") is True
        by_id = {p["id"]: p for p in list_providers()}
        assert by_id["groq"]["last_working"] is True
        assert by_id["groq"]["last_working_ago"] == "worked just now"
        assert by_id["groq"]["last_working_model"] == "groq/compound-mini"
        assert by_id["gemini"]["last_working"] is False
        assert by_id["gemini"]["last_working_ago"] is None


# ── last-known-working memory ──────────────────────────────────────────


class TestLastWorkingMemory:
    def test_record_read_roundtrip(self, clean_env, tmp_path):
        from core.last_working import read_last_working, record_last_working

        assert read_last_working() is None
        assert record_last_working("GROQ", "groq/compound-mini", now=1000.0) is True
        state = read_last_working()
        assert state is not None
        assert (state.provider, state.model, state.worked_at) == (
            "groq",
            "groq/compound-mini",
            1000.0,
        )

    def test_empty_inputs_rejected(self, clean_env):
        from core.last_working import read_last_working, record_last_working

        assert record_last_working("", "m") is False
        assert record_last_working("groq", "") is False
        assert read_last_working() is None

    def test_malformed_file_reads_none(self, clean_env, tmp_path, monkeypatch):
        from core.last_working import read_last_working

        path = tmp_path / "broken.json"
        path.write_text("not json{{", encoding="utf-8")
        monkeypatch.setenv("LUCKYD_LAST_WORKING_STATE", str(path))
        assert read_last_working() is None
        path.write_text(json.dumps({"provider": "groq"}), encoding="utf-8")
        assert read_last_working() is None

    def test_age_labels(self, clean_env):
        from core.last_working import last_working_age_label, record_last_working

        assert last_working_age_label() is None
        assert record_last_working("groq", "m", now=10_000.0) is True
        assert last_working_age_label(now=10_030.0) == "worked just now"
        assert last_working_age_label(now=10_000.0 + 5 * 60) == "worked 5m ago"
        assert last_working_age_label(now=10_000.0 + 2 * 3600) == "worked 2h ago"
        assert last_working_age_label(now=10_000.0 + 3 * 86400) == "worked 3d ago"

    def test_clear(self, clean_env):
        from core.last_working import (
            clear_last_working,
            read_last_working,
            record_last_working,
        )

        assert clear_last_working() is False
        assert record_last_working("groq", "m") is True
        assert clear_last_working() is True
        assert read_last_working() is None

    def test_record_never_raises(self, clean_env, tmp_path, monkeypatch):
        from core.last_working import record_last_working

        # Point at a directory — the write must fail closed, not raise.
        monkeypatch.setenv("LUCKYD_LAST_WORKING_STATE", str(tmp_path))
        assert record_last_working("groq", "m") is False


# ── rotation preference + live active pair ─────────────────────────────


class TestRotationPreference:
    def test_last_working_preferred_when_usable(self, clean_env):
        from core.free_rotation import available_free_models, best_free_provider
        from core.last_working import record_last_working

        clean_env.setenv("GOOGLE_API_KEY", TEST_API_KEY)
        clean_env.setenv("GROQ_API_KEY", TEST_API_KEY)
        # No record: plain priority order (gemini beats groq).
        assert best_free_provider() == "gemini"
        # A proven groq success moves groq first (membership unchanged).
        assert record_last_working("groq", "groq/compound-mini") is True
        assert best_free_provider() == "groq"
        pairs = available_free_models()
        assert pairs[0] == ("groq", "groq/compound-mini")
        assert ("gemini", "gemini-2.5-flash") in pairs

    def test_unusable_last_working_ignored(self, clean_env):
        from core.free_rotation import best_free_provider
        from core.last_working import record_last_working

        clean_env.setenv("GOOGLE_API_KEY", TEST_API_KEY)
        # groq has no key: the stale record must not win.
        assert record_last_working("groq", "groq/compound-mini") is True
        assert best_free_provider() == "gemini"

    def test_provider_match_survives_model_drift(self, clean_env):
        from core.free_rotation import best_free_provider
        from core.last_working import record_last_working

        clean_env.setenv("GOOGLE_API_KEY", TEST_API_KEY)
        clean_env.setenv("GROQ_API_KEY", TEST_API_KEY)
        assert record_last_working("groq", "some/renamed-model") is True
        assert best_free_provider() == "groq"


class TestActivePair:
    def test_set_get_reset_and_label(self, clean_env):
        from core.free_rotation import (
            active_pair_label,
            get_active_pair,
            reset_active_pair,
            set_active_pair,
        )

        assert get_active_pair() is None
        assert active_pair_label() == ""
        set_active_pair("groq", "groq/compound-mini")
        assert get_active_pair() == ("groq", "groq/compound-mini")
        assert active_pair_label() == "groq/groq/compound-mini"
        reset_active_pair()
        assert get_active_pair() is None

    def test_rotator_rotate_sets_active(self, clean_env):
        from core.free_rotation import FreeModelRotator, get_active_pair

        clean_env.setenv("GROQ_API_KEY", TEST_API_KEY)
        rot = FreeModelRotator()
        assert rot.rotate() == ("groq", "groq/compound-mini")
        assert get_active_pair() == ("groq", "groq/compound-mini")

    def test_agent_init_and_switch_seed_active(self, clean_env):
        from unittest.mock import patch

        from core.free_rotation import get_active_pair

        clean_env.setenv("GROQ_API_KEY", TEST_API_KEY)
        with patch("llm.ProviderRouter"):
            from core.agent_loop import CodingAgent

            ag = CodingAgent(api_key=TEST_API_KEY, model="test-model")
            live = get_active_pair()
            assert live is not None and live[1] == "test-model"

            from core.providers import LLMConfig

            ag.switch_provider(
                LLMConfig(
                    api_key=TEST_API_KEY,
                    base_url="https://x.test/v1",
                    model="switched-model",
                    provider="groq",
                )
            )
            assert get_active_pair() == ("groq", "switched-model")
            assert ag.llm_client.provider == "groq"


# ── LLM success recording ──────────────────────────────────────────────


class TestSuccessRecording:
    def test_record_success_writes_memory_and_active(self, clean_env):
        from core.free_rotation import get_active_pair
        from core.last_working import read_last_working
        from core.llm_client import LLMClient

        client = LLMClient("k", "https://x.test/v1", "m", provider="groq")
        client._record_success()
        assert get_active_pair() == ("groq", "m")
        state = read_last_working()
        assert state is not None and (state.provider, state.model) == ("groq", "m")

    def test_record_success_noop_without_provider(self, clean_env):
        from core.free_rotation import get_active_pair
        from core.last_working import read_last_working
        from core.llm_client import LLMClient

        LLMClient("k", "https://x.test/v1", "m")._record_success()
        assert get_active_pair() is None
        assert read_last_working() is None

    async def test_chat_nonstreaming_success_records(self, clean_env):
        from core.free_rotation import get_active_pair
        from core.last_working import read_last_working
        from core.llm_client import LLMClient

        client = LLMClient("k", "https://x.test/v1", "m", provider="groq")
        fake_resp = SimpleNamespace(
            json=lambda: {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "hi"},
                        "finish_reason": "stop",
                    }
                ]
            }
        )
        client._http_post = AsyncMock(return_value=fake_resp)
        msg = await client.chat_nonstreaming([{"role": "user", "content": "hi"}])
        assert msg is not None and msg.get("content") == "hi"
        assert get_active_pair() == ("groq", "m")
        state = read_last_working()
        assert state is not None and state.provider == "groq"


# ── agents bridge: providers, best-free, live re-read ───────────────────


class TestAgentsBridge:
    def _bridge(self, tmp_path):
        module = _bridge_module()
        root = tmp_path / "backend"
        (root / "browser" / "data").mkdir(parents=True, exist_ok=True)
        return module.Bridge(root, 9885, 9886)

    def test_read_providers_snapshot(self, clean_env, tmp_path):
        clean_env.setenv("GROQ_API_KEY", TEST_API_KEY)
        bridge = self._bridge(tmp_path)
        snap = bridge.read_providers()
        assert snap["available"] is True
        by_id = {p["id"]: p for p in snap["providers"]}
        assert by_id["groq"]["next_in_rotation"] is True
        assert by_id["groq"]["rotation_order"] == 4
        assert "credit_ttl_remaining_sec" in by_id["cline-usage"]

    def test_read_best_free(self, clean_env, tmp_path):
        clean_env.setenv("GROQ_API_KEY", TEST_API_KEY)
        bridge = self._bridge(tmp_path)
        best = bridge.read_best_free()
        assert best["available"] is True
        assert best["provider"] == "groq"
        assert best["model"] == "groq/compound-mini"

    def test_read_best_free_none_when_nothing_usable(self, clean_env, tmp_path):
        bridge = self._bridge(tmp_path)
        best = bridge.read_best_free()
        assert best["available"] is False
        assert best["provider"] == "" and best["model"] == ""

    def test_set_model_live_reread(self, clean_env, tmp_path):
        bridge = self._bridge(tmp_path)
        out = bridge.set_model("groq", "groq/compound-mini")
        assert out["provider"] == "groq" and out["model"] == "groq/compound-mini"
        # An external edit (the browser sidebar) is visible immediately.
        disk = json.loads(bridge.settings_path.read_text(encoding="utf-8-sig"))
        disk["ai_provider"] = "gemini"
        disk["ai_model_overrides"] = {"gemini": "gemini-2.5-flash"}
        bridge.settings_path.write_text(json.dumps(disk), encoding="utf-8-sig")
        assert bridge.reload_settings()["ai_provider"] == "gemini"
        assert bridge.read_current_model()["provider"] == "gemini"

    def test_http_routes(self, clean_env, tmp_path):
        module = _bridge_module()
        clean_env.setenv("GROQ_API_KEY", TEST_API_KEY)
        bridge = self._bridge(tmp_path)
        server = ThreadingHTTPServer(("127.0.0.1", 0), module.make_handler(bridge))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_port

            def _get(path):
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
                conn.request("GET", path)
                resp = conn.getresponse()
                body = json.loads(resp.read().decode("utf-8") or "{}")
                conn.close()
                return resp.status, body

            status, snap = _get("/api/providers")
            assert status == 200 and snap["available"] is True
            assert any(p["id"] == "groq" for p in snap["providers"])
            status, best = _get("/api/best-free")
            assert status == 200 and best["provider"] == "groq"

            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
            conn.request(
                "POST",
                "/api/model",
                body=json.dumps({"provider": "groq", "model": "groq/compound-mini"}),
                headers={"Content-Type": "application/json"},
            )
            resp = conn.getresponse()
            posted = json.loads(resp.read().decode("utf-8") or "{}")
            conn.close()
            assert resp.status == 200 and posted["model"] == "groq/compound-mini"
            # The POST response is a live disk re-read, not an echo.
            disk = json.loads(bridge.settings_path.read_text(encoding="utf-8-sig"))
            assert disk["ai_model_overrides"]["groq"] == "groq/compound-mini"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


# ── web_server: providers snapshot + live settings ─────────────────────


class TestWebServerHealth:
    @pytest.fixture()
    def hq(self, monkeypatch):
        import web_server

        monkeypatch.setattr(web_server, "_TOKEN", "test-token-105")
        server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.HQHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield server.server_port
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    def _get(self, port, path):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        conn.request("GET", path, headers={"Cookie": "luckyd_hq=test-token-105"})
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8") or "{}")
        conn.close()
        return resp.status, body

    def test_api_providers_snapshot(self, clean_env, hq):
        from core.free_rotation import set_active_pair

        clean_env.setenv("GROQ_API_KEY", TEST_API_KEY)
        set_active_pair("groq", "groq/compound-mini")
        status, data = self._get(hq, "/api/providers")
        assert status == 200
        by_id = {p["id"]: p for p in data["providers"]}
        assert by_id["groq"]["next_in_rotation"] is True
        assert data["best_free"] == "groq"
        assert data["active"] == {"provider": "groq", "model": "groq/compound-mini"}

    def test_api_settings_prefers_live_pair(self, clean_env, hq):
        from core.free_rotation import set_active_pair

        # Static config path first (no live pair in a fresh test).
        status, data = self._get(hq, "/api/settings")
        assert status == 200 and "provider" in data and "model" in data
        # A rotation-pinned live pair wins over the static config.
        set_active_pair("groq", "groq/compound-mini")
        status, data = self._get(hq, "/api/settings")
        assert status == 200
        assert (data["provider"], data["model"]) == ("groq", "groq/compound-mini")


# ── lucky-code model hot reload + REPL prompt ───────────────────────────


class TestCliHotReload:
    def test_hot_reload_success(self, clean_env, tmp_path, monkeypatch):
        import config
        import main

        env_file = tmp_path / ".env"
        env_file.write_text("GROQ_API_KEY=test-key-not-a-secret\n", encoding="utf-8")
        monkeypatch.setattr(config, "BASE_ENV_FILE", env_file)
        monkeypatch.setattr(config, "ENV_FILE", env_file)
        clean_env.delenv("CODING_AGENT_PROVIDER", raising=False)
        assert main._hot_reload_model_switch("groq", "groq/compound-mini") is True
        from core.free_rotation import get_active_pair

        assert get_active_pair() == ("groq", "groq/compound-mini")

    def test_hot_reload_failure(self, clean_env, monkeypatch):
        import main

        monkeypatch.setattr(
            "core.providers.resolve_provider_config",
            lambda provider=None: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        assert main._hot_reload_model_switch("groq", "m") is False

    def test_reexec_fallback_prints_hint_once_guarded(self, clean_env, monkeypatch, capsys):
        import main

        monkeypatch.setenv("LUCKYD_MODEL_REEXEC", "1")
        main._reexec_or_restart_hint()
        assert "Restart the terminal" in capsys.readouterr().out

    def test_reexec_attempts_self_exec(self, clean_env, monkeypatch, capsys):
        import main

        monkeypatch.delenv("LUCKYD_MODEL_REEXEC", raising=False)

        def _boom(*a, **k):
            raise OSError("no exec in tests")

        monkeypatch.setattr("os.execv", _boom)
        main._reexec_or_restart_hint()
        out = capsys.readouterr().out
        assert "Restart the terminal" in out
        # Guard set so a real exec would only ever happen once.
        import os

        assert os.environ["LUCKYD_MODEL_REEXEC"] == "1"

    def test_cli_model_no_restart_message(self, clean_env, tmp_path, monkeypatch, capsys):
        import config
        import main

        env_file = tmp_path / ".env"
        env_file.write_text("GROQ_API_KEY=test-key-not-a-secret\n", encoding="utf-8")
        monkeypatch.setattr(config, "BASE_ENV_FILE", env_file)
        monkeypatch.setattr(config, "ENV_FILE", env_file)
        main._cli_model(["groq", "groq/compound-mini"])
        out = capsys.readouterr().out
        assert "Live now" in out
        assert "Restart the terminal" not in out
        written = env_file.read_text(encoding="utf-8")
        assert "CODING_AGENT_PROVIDER=groq" in written


class TestReplPromptLabel:
    def test_live_pair_preferred(self, clean_env):
        from core.free_rotation import set_active_pair
        from ui import TerminalUI

        ui = TerminalUI()
        ui.set_session_info(provider="DeepSeek", model="deepseek-chat")
        set_active_pair("groq", "groq/compound-mini")
        assert ui._active_model_label() == "groq/groq/compound-mini"

    def test_session_fallback_without_live_state(self, clean_env):
        from ui import TerminalUI

        ui = TerminalUI()
        ui.set_session_info(provider="DeepSeek", model="deepseek-chat")
        assert ui._active_model_label() == "DeepSeek/deepseek-chat"
