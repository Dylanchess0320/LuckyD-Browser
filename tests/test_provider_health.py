"""LuckyD provider/model-switching polish — provider health snapshot.

Covers the extended ``core.providers.list_providers()`` fields
(``status``, ``rotation_order``, ``next_in_rotation``,
``credit_ttl_remaining_sec``) and ``core.providers.health_snapshot()``.
"""

from __future__ import annotations

import json
import time

import pytest

import core.providers as providers
from core.cline_credit import CREDIT_STATE_TTL_SEC, record_cline_credit_exhausted
from core.free_rotation import FREE_MODEL_PRIORITY
from core.last_working import record_last_working_model
from core.providers import health_snapshot, list_providers

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
    "GOOGLE_API_KEY",
    "ZAI_API_KEY",
    "OPENCODE_API_KEY",
    "OPENROUTER_API_KEY",
    "GROQ_API_KEY",
    "DEEPSEEK_API_KEY",
    "CODING_AGENT_API_KEY",
    "CODING_AGENT_BASE_URL",
    "CODING_AGENT_MODEL",
    "OLLAMA_MODEL",
    "OLLAMA_HOST",
    "MINIMAX_API_KEY",
]

_NEW_KEYS = {"status", "rotation_order", "next_in_rotation", "credit_ttl_remaining_sec"}


@pytest.fixture
def health_env(monkeypatch, tmp_path):
    """Hermetic provider env + scratch state files."""
    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    # Deterministic "current" without touching browser/data/settings.json.
    monkeypatch.setenv("CODING_AGENT_PROVIDER", "groq")
    providers.reset_cline_session_cache()
    monkeypatch.setattr(providers, "cline_session_token", lambda: "")
    monkeypatch.setenv("LUCKYD_CLINE_CREDIT_STATE", str(tmp_path / "credit.json"))
    monkeypatch.setenv("LUCKYD_LAST_WORKING_STATE", str(tmp_path / "last.json"))

    # No network: list_providers/health_snapshot must be pure state reads.
    import socket

    def _blocked(*a, **k):
        raise AssertionError("network call during provider health read")

    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr("httpx.get", _blocked, raising=False)
    monkeypatch.setattr("httpx.Client", _blocked, raising=False)


def _by_id(entries):
    return {p["id"]: p for p in entries}


class TestHealthFields:
    def test_new_keys_present_on_every_entry(self, health_env):
        for entry in list_providers():
            assert set(entry) >= _NEW_KEYS, f"missing keys in {entry['id']}"

    def test_status_values_are_known(self, health_env):
        for entry in list_providers():
            assert entry["status"] in ("ready", "needs_key", "exhausted"), entry["id"]

    def test_ollama_ready_keyless(self, health_env):
        ollama = _by_id(list_providers())["ollama"]
        assert ollama["status"] == "ready"
        assert ollama["configured"] is True

    def test_unkeyed_cloud_provider_needs_key(self, health_env):
        openai = _by_id(list_providers())["openai"]
        assert openai["status"] == "needs_key"
        assert openai["configured"] is False

    def test_keyed_provider_ready(self, health_env, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        openai = _by_id(list_providers())["openai"]
        assert openai["status"] == "ready"

    def test_rotation_order_matches_free_priority(self, health_env):
        by_id = _by_id(list_providers())
        for order, (pid, _model) in enumerate(FREE_MODEL_PRIORITY):
            assert by_id[pid]["rotation_order"] == order, pid
        assert by_id["openai"]["rotation_order"] is None
        assert by_id["anthropic"]["rotation_order"] is None

    def test_rotation_order_is_dense_and_unique(self, health_env):
        orders = [p["rotation_order"] for p in list_providers() if p["rotation_order"] is not None]
        assert sorted(orders) == list(range(len(orders)))

    def test_existing_fields_untouched(self, health_env):
        # The old contract still holds: no keys were removed or renamed.
        required = {
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
            "credit_exhausted",
        }
        for entry in list_providers():
            assert set(entry) >= required, f"missing keys in {entry['id']}"


class TestNextInRotation:
    def test_points_at_best_free_provider(self, health_env, monkeypatch):
        # Cline has no session here and Ollama isn't reachable in the test
        # sandbox, so a Gemini key wins the free rotation.
        monkeypatch.delenv("CODING_AGENT_PROVIDER")
        monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
        entries = list_providers()
        flagged = [p["id"] for p in entries if p["next_in_rotation"]]
        assert flagged == ["gemini"]
        snap = health_snapshot()
        assert snap["best_free"] == "gemini"

    def test_skips_exhausted_cline(self, health_env, monkeypatch):
        monkeypatch.delenv("CODING_AGENT_PROVIDER")
        monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
        record_cline_credit_exhausted("HTTP 402")
        entries = list_providers()
        flagged = [p["id"] for p in entries if p["next_in_rotation"]]
        assert flagged == ["gemini"]
        assert _by_id(entries)["cline-usage"]["next_in_rotation"] is False

    def test_exactly_one_next(self, health_env, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
        flagged = [p["id"] for p in list_providers() if p["next_in_rotation"]]
        assert len(flagged) == 1


class TestCreditTtl:
    def test_fresh_marker_gives_full_ttl(self, health_env):
        record_cline_credit_exhausted("HTTP 402")
        by_id = _by_id(list_providers())
        for pid in ("clinepass", "cline-usage"):
            ttl = by_id[pid]["credit_ttl_remaining_sec"]
            assert CREDIT_STATE_TTL_SEC - 5 <= ttl <= CREDIT_STATE_TTL_SEC, pid

    def test_ttl_counts_down(self, health_env):
        record_cline_credit_exhausted("HTTP 402", now=time.time() - 100)
        ttl = _by_id(list_providers())["cline-usage"]["credit_ttl_remaining_sec"]
        assert abs(ttl - (CREDIT_STATE_TTL_SEC - 100)) <= 5

    def test_no_marker_gives_zero_ttl(self, health_env):
        for entry in list_providers():
            assert entry["credit_ttl_remaining_sec"] == 0, entry["id"]

    def test_exhausted_status_with_marker(self, health_env, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
        record_cline_credit_exhausted("HTTP 402")
        by_id = _by_id(list_providers())
        assert by_id["cline-usage"]["status"] == "exhausted"
        assert by_id["google"]["status"] == "ready"


class TestHealthSnapshot:
    def test_shape(self, health_env):
        snap = health_snapshot()
        assert set(snap) >= {"providers", "best_free", "best_free_model", "last_working"}
        assert isinstance(snap["providers"], list)
        assert len(snap["providers"]) == len(providers.VALID_PROVIDERS)

    def test_best_free_model_matches(self, health_env, monkeypatch):
        monkeypatch.delenv("CODING_AGENT_PROVIDER")
        monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
        snap = health_snapshot()
        assert snap["best_free"] == "gemini"
        assert snap["best_free_model"] == "gemini-3.8-flash"

    def test_last_working_absent_initially(self, health_env):
        assert health_snapshot()["last_working"] is None

    def test_last_working_round_trips(self, health_env):
        record_last_working_model("gemini", "gemini-2.5-flash")
        lw = health_snapshot()["last_working"]
        assert lw is not None
        assert lw["provider"] == "gemini"
        assert lw["model"] == "gemini-2.5-flash"
        assert lw["timestamp"] > 0

    def test_corrupt_last_working_state_never_breaks_snapshot(
        self, health_env, monkeypatch, tmp_path
    ):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        monkeypatch.setenv("LUCKYD_LAST_WORKING_STATE", str(bad))
        snap = health_snapshot()
        assert snap["last_working"] is None
        assert len(snap["providers"]) == len(providers.VALID_PROVIDERS)

    def test_snapshot_is_json_serializable(self, health_env):
        json.dumps(health_snapshot())
