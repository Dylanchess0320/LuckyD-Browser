"""LuckyD provider/model-switching polish — last-known-working memory.

Covers ``core.last_working`` (record/read/expiry/summary), the rotation
preference for the last-good pair in ``core.free_rotation``, and the
success recording hook in ``core.llm_client``.
"""

from __future__ import annotations

import json
import time

import pytest

from core import last_working
from core.free_rotation import available_free_models
from core.last_working import (
    LAST_WORKING_TTL_SEC,
    clear_last_working_model,
    is_last_working_fresh,
    last_working_pair,
    last_working_summary,
    read_last_working_model,
    record_last_working_model,
)
from core.llm_client import LLMClient


@pytest.fixture
def lw_state(monkeypatch, tmp_path):
    """Scratch state file; hermetic provider env for rotation tests."""
    state = tmp_path / "last_working_model.json"
    monkeypatch.setenv("LUCKYD_LAST_WORKING_STATE", str(state))
    monkeypatch.setenv("LUCKYD_CLINE_CREDIT_STATE", str(tmp_path / "credit.json"))
    for var in (
        "CODING_AGENT_PROVIDER",
        "CLINEPASS_API_KEY",
        "GOOGLE_API_KEY",
        "ZAI_API_KEY",
        "OPENROUTER_API_KEY",
        "GROQ_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    return state


class TestRecordAndRead:
    def test_record_writes_provider_model_timestamp(self, lw_state):
        assert record_last_working_model("google", "gemini-2.5-flash", now=1000.0) is True
        data = json.loads(lw_state.read_text(encoding="utf-8"))
        assert data["provider"] == "google"
        assert data["model"] == "gemini-2.5-flash"
        assert data["worked_at"] == 1000.0

    def test_read_returns_state(self, lw_state):
        record_last_working_model("ollama", "llama3.2:3b", now=1000.0)
        st = read_last_working_model()
        assert st is not None
        assert (st.provider, st.model) == ("ollama", "llama3.2:3b")

    def test_missing_file_reads_none(self, lw_state):
        assert read_last_working_model() is None
        assert last_working_pair() is None
        assert last_working_summary() is None

    def test_record_overwrites_previous(self, lw_state):
        record_last_working_model("google", "gemini-2.5-flash", now=1000.0)
        record_last_working_model("zai", "glm-4.5-air", now=2000.0)
        assert last_working_pair(now=2500.0) == ("zai", "glm-4.5-air")

    def test_provider_is_normalized(self, lw_state):
        record_last_working_model(" Google ", "gemini-2.5-flash", now=1000.0)
        assert last_working_pair(now=1500.0) == ("google", "gemini-2.5-flash")

    def test_empty_values_rejected(self, lw_state):
        assert record_last_working_model("", "m") is False
        assert record_last_working_model("google", "") is False
        assert read_last_working_model() is None

    def test_clear_removes_state(self, lw_state):
        record_last_working_model("google", "gemini-2.5-flash", now=1000.0)
        assert clear_last_working_model() is True
        assert read_last_working_model() is None


class TestFreshness:
    def test_fresh_within_ttl(self, lw_state):
        record_last_working_model("google", "gemini-2.5-flash", now=1000.0)
        assert is_last_working_fresh(now=1000.0 + LAST_WORKING_TTL_SEC - 1) is True

    def test_stale_after_ttl(self, lw_state):
        record_last_working_model("google", "gemini-2.5-flash", now=1000.0)
        assert is_last_working_fresh(now=1000.0 + LAST_WORKING_TTL_SEC + 1) is False

    def test_stale_pair_hidden_from_rotation_use(self, lw_state):
        record_last_working_model("google", "gemini-2.5-flash", now=1000.0)
        assert last_working_pair(now=1000.0 + LAST_WORKING_TTL_SEC + 1) is None

    def test_malformed_file_reads_none(self, lw_state):
        lw_state.write_text("{not json", encoding="utf-8")
        assert read_last_working_model() is None
        assert last_working_pair() is None

    def test_wrong_shape_reads_none(self, lw_state):
        lw_state.write_text(json.dumps({"provider": "google"}), encoding="utf-8")
        assert read_last_working_model() is None

    def test_unwritable_state_never_raises(self, monkeypatch, tmp_path):
        # A regular file where the parent dir should be: mkdir fails,
        # record returns False instead of raising (works even as root).
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setenv("LUCKYD_LAST_WORKING_STATE", str(blocker / "state.json"))
        assert record_last_working_model("google", "m") is False


class TestSummary:
    def test_summary_format(self, lw_state):
        now = time.time()
        record_last_working_model("google", "gemini-2.5-flash", now=now - 2 * 3600)
        summary = last_working_summary(now=now)
        assert summary is not None
        assert summary.startswith("google/gemini-2.5-flash")
        assert "worked 2h ago" in summary

    def test_summary_empty_when_nothing(self, lw_state):
        assert last_working_summary() is None


class TestRotationPreference:
    def test_last_good_moves_to_front(self, lw_state, monkeypatch):
        import core.providers as providers

        monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
        monkeypatch.setenv("GROQ_API_KEY", "gr-test")
        monkeypatch.setattr(providers, "cline_session_token", lambda: "")
        # Priority order would put gemini first…
        before = available_free_models()
        assert before[0][0] == "gemini"
        # …but a proven groq pair jumps the queue.
        record_last_working_model("groq", "groq/compound-mini")
        after = available_free_models()
        assert after[0] == ("groq", "groq/compound-mini")
        # Nothing removed: the documented fallback chain is intact.
        assert set(before) == set(after)

    def test_unusable_last_good_is_ignored(self, lw_state, monkeypatch):
        import core.providers as providers

        monkeypatch.setattr(providers, "cline_session_token", lambda: "")
        record_last_working_model("gemini", "gemini-2.5-flash")
        # gemini has no key here, so it can't be preferred.
        assert all(p[0] != "gemini" for p in available_free_models())

    def test_failed_last_good_stays_excluded(self, lw_state, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
        record_last_working_model("gemini", "gemini-2.5-flash")
        usable = available_free_models(failed=[("gemini", "gemini-2.5-flash")])
        assert ("gemini", "gemini-2.5-flash") not in usable

    def test_corrupt_state_leaves_order_alone(self, lw_state, monkeypatch):
        import core.providers as providers

        monkeypatch.setenv("GOOGLE_API_KEY", "g-test")
        monkeypatch.setattr(providers, "cline_session_token", lambda: "")
        lw_state.write_text("garbage", encoding="utf-8")
        usable = available_free_models()
        assert usable[0][0] == "gemini"


class TestLlmClientRecording:
    def test_note_success_records_pair(self, lw_state):
        client = LLMClient(
            api_key="k",
            base_url="https://api.example.com/v1",
            model="some-model",
            provider="google",
        )
        client._note_success()
        assert last_working_pair() == ("google", "some-model")

    def test_provider_derived_from_base_url(self, lw_state):
        client = LLMClient(
            api_key="",
            base_url="http://127.0.0.1:11434/v1",
            model="llama3.2:3b",
        )
        assert client._provider_id == "ollama"
        client._note_success()
        assert last_working_pair() == ("ollama", "llama3.2:3b")

    def test_note_success_never_raises(self, lw_state, monkeypatch):
        client = LLMClient(api_key="k", base_url="u", model="m", provider="google")

        def _boom(*a, **k):
            raise RuntimeError("state exploded")

        monkeypatch.setattr(last_working, "record_last_working_model", _boom)
        client._note_success()  # must not raise

    def test_unknown_base_url_records_nothing(self, lw_state):
        client = LLMClient(api_key="k", base_url="u", model="m")
        assert client._provider_id == ""
        client._note_success()
        assert last_working_pair() is None
