"""LuckyD 10.4 — Cline credit-honesty UX.

When the Cline gateway returns HTTP 402, a marker is recorded in
``~/.luckyd/cline_credit_state.json`` (24 h TTL) and auto-selection steers
away from Cline until it expires or the user runs
``lucky-code providers --clear-credit-state``. No balance API: the 402 is
the only trigger.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
import pytest

import core.providers as providers
from core.cline_credit import (
    CREDIT_STATE_TTL_SEC,
    clear_cline_credit_state,
    credit_state_path,
    is_cline_credit_exhausted,
    read_cline_credit_state,
    record_cline_credit_exhausted,
)

_REASON = "HTTP 402 from https://api.cline.bot/api/v1 (m): payment required"

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


@pytest.fixture
def credit_env(monkeypatch, tmp_path):
    """Hermetic provider env + a scratch marker file (overrides conftest)."""
    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    providers.reset_cline_session_cache()
    monkeypatch.setattr(providers, "cline_session_token", lambda: "")
    state = tmp_path / "cline_credit_state.json"
    monkeypatch.setenv("LUCKYD_CLINE_CREDIT_STATE", str(state))

    def _refuse(*a, **k):
        raise ConnectionError("no network in credit tests")

    monkeypatch.setattr(httpx, "get", _refuse)
    return state


def _by_id(entries):
    return {p["id"]: p for p in entries}


# ── marker file ──────────────────────────────────────────────────────────


class TestMarkerFile:
    def test_record_writes_timestamp_and_reason(self, credit_env):
        assert record_cline_credit_exhausted(_REASON, now=1000.0) is True
        data = json.loads(credit_env.read_text(encoding="utf-8"))
        assert data["exhausted_at"] == 1000.0
        assert data["reason"] == _REASON

    def test_record_creates_parent_dirs(self, credit_env):
        nested = credit_env.parent / "sub" / "dir" / "state.json"
        os.environ["LUCKYD_CLINE_CREDIT_STATE"] = str(nested)
        try:
            assert record_cline_credit_exhausted(_REASON) is True
            assert nested.is_file()
        finally:
            os.environ["LUCKYD_CLINE_CREDIT_STATE"] = str(credit_env)

    def test_fresh_marker_is_exhausted(self, credit_env):
        record_cline_credit_exhausted(_REASON, now=1000.0)
        assert is_cline_credit_exhausted(now=1000.0) is True
        assert is_cline_credit_exhausted(now=1000.0 + CREDIT_STATE_TTL_SEC - 1) is True

    def test_expired_marker_is_not_exhausted(self, credit_env):
        record_cline_credit_exhausted(_REASON, now=1000.0)
        assert is_cline_credit_exhausted(now=1000.0 + CREDIT_STATE_TTL_SEC + 1) is False

    def test_ttl_boundary_is_expired(self, credit_env):
        # Exactly 24 h old: outside the TTL (strict less-than).
        record_cline_credit_exhausted(_REASON, now=1000.0)
        assert is_cline_credit_exhausted(now=1000.0 + CREDIT_STATE_TTL_SEC) is False

    def test_missing_file_is_not_exhausted(self, credit_env):
        assert not credit_env.exists()
        assert read_cline_credit_state() is None
        assert is_cline_credit_exhausted() is False

    def test_corrupt_file_is_not_exhausted(self, credit_env):
        credit_env.write_text("{not json", encoding="utf-8")
        assert read_cline_credit_state() is None
        assert is_cline_credit_exhausted() is False

    def test_wrong_shape_is_not_exhausted(self, credit_env):
        credit_env.write_text("[1, 2]", encoding="utf-8")
        assert is_cline_credit_exhausted() is False
        credit_env.write_text(json.dumps({"reason": "no timestamp"}), encoding="utf-8")
        assert is_cline_credit_exhausted() is False
        credit_env.write_text(json.dumps({"exhausted_at": "soon"}), encoding="utf-8")
        assert is_cline_credit_exhausted() is False

    def test_default_path_is_luckyd_state_file(self, credit_env, monkeypatch):
        monkeypatch.delenv("LUCKYD_CLINE_CREDIT_STATE")
        assert credit_state_path() == Path.home() / ".luckyd" / "cline_credit_state.json"

    def test_clear_removes_marker(self, credit_env):
        record_cline_credit_exhausted(_REASON)
        assert clear_cline_credit_state() is True
        assert not credit_env.exists()
        assert is_cline_credit_exhausted() is False

    def test_clear_missing_is_quiet_false(self, credit_env):
        assert clear_cline_credit_state() is False


# ── detect_provider / list_providers honoring ────────────────────────────


class TestDetectHonorsMarker:
    def test_session_first_skipped_while_exhausted(self, credit_env, monkeypatch):
        monkeypatch.setattr(providers, "cline_session_token", lambda: "tok-live")
        assert providers.detect_provider() == "cline-usage"  # control: no marker
        record_cline_credit_exhausted(_REASON)
        assert providers.detect_provider() is None  # Cline skipped, nothing else

    def test_falls_through_to_next_provider(self, credit_env, monkeypatch):
        monkeypatch.setattr(providers, "cline_session_token", lambda: "tok-live")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        record_cline_credit_exhausted(_REASON)
        assert providers.detect_provider() == "openai"

    def test_cline_env_keys_skipped(self, credit_env, monkeypatch):
        monkeypatch.setenv("CLINEPASS_API_KEY", "cp-key")
        assert providers.detect_provider() == "clinepass"  # control
        record_cline_credit_exhausted(_REASON)
        assert providers.detect_provider() is None
        monkeypatch.delenv("CLINEPASS_API_KEY")
        monkeypatch.setenv("CLINE_USAGE_MODEL", "deepseek/deepseek-chat")
        assert providers.detect_provider() is None  # still skipped

    def test_explicit_provider_wins_while_exhausted(self, credit_env, monkeypatch):
        monkeypatch.setattr(providers, "cline_session_token", lambda: "tok-live")
        monkeypatch.setenv("CODING_AGENT_PROVIDER", "cline-usage")
        record_cline_credit_exhausted(_REASON)
        assert providers.detect_provider() == "cline-usage"


class TestListHonorsMarker:
    def test_cline_rows_flagged_and_unconfigured(self, credit_env, monkeypatch):
        monkeypatch.setattr(providers, "cline_session_token", lambda: "tok-live")
        record_cline_credit_exhausted(_REASON)
        by_id = _by_id(providers.list_providers())
        for pid in ("clinepass", "cline-usage"):
            assert by_id[pid]["credit_exhausted"] is True
            assert by_id[pid]["configured"] is False
            assert by_id[pid]["current"] is False
        assert by_id["openai"]["credit_exhausted"] is False

    def test_current_never_auto_cline_while_exhausted(self, credit_env, monkeypatch):
        monkeypatch.setattr(providers, "cline_session_token", lambda: "tok-live")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        record_cline_credit_exhausted(_REASON)
        current = [p["id"] for p in providers.list_providers() if p["current"]]
        assert current == ["openai"]

    def test_explicit_cline_stays_current_but_flagged(self, credit_env, monkeypatch):
        monkeypatch.setattr(providers, "cline_session_token", lambda: "tok-live")
        monkeypatch.setenv("CODING_AGENT_PROVIDER", "cline-usage")
        record_cline_credit_exhausted(_REASON)
        by_id = _by_id(providers.list_providers())
        assert by_id["cline-usage"]["current"] is True
        assert by_id["cline-usage"]["credit_exhausted"] is True
        assert by_id["cline-usage"]["configured"] is False

    def test_no_marker_keeps_normal_status(self, credit_env, monkeypatch):
        monkeypatch.setattr(providers, "cline_session_token", lambda: "tok-live")
        by_id = _by_id(providers.list_providers())
        assert by_id["cline-usage"]["credit_exhausted"] is False
        assert by_id["cline-usage"]["configured"] is True
        assert by_id["cline-usage"]["current"] is True


# ── 402 trigger (cline-only) ─────────────────────────────────────────────


def _status_error(code, base_url, detail="broke"):
    from core.llm_client import LLMClient

    client = LLMClient(api_key="k", base_url=base_url, model="m")
    req = httpx.Request("POST", f"{base_url}/chat/completions")
    resp = httpx.Response(code, json={"error": {"code": "x", "message": detail}}, request=req)
    return client, httpx.HTTPStatusError(f"{code}", request=req, response=resp)


class TestTrigger402:
    def test_402_on_cline_records_marker(self, credit_env):
        client, err = _status_error(402, "https://api.cline.bot/api/v1")
        out = client._handle_http_error(err, attempt=0)
        assert out["content"].startswith("[API Error: 402")
        assert is_cline_credit_exhausted() is True
        assert "402" in (read_cline_credit_state().reason or "")

    def test_402_off_cline_does_not_record(self, credit_env):
        client, err = _status_error(402, "https://api.openai.com/v1")
        out = client._handle_http_error(err, attempt=0)
        assert out["content"].startswith("[API Error: 402")
        assert is_cline_credit_exhausted() is False

    def test_non_402_on_cline_does_not_record(self, credit_env):
        client, err = _status_error(401, "https://api.cline.bot/api/v1")
        client._handle_http_error(err, attempt=0)
        assert is_cline_credit_exhausted() is False
        client, err = _status_error(429, "https://api.cline.bot/api/v1")
        assert client._handle_http_error(err, attempt=0) is None  # retryable
        assert is_cline_credit_exhausted() is False


# ── clear command ────────────────────────────────────────────────────────


class TestClearCommand:
    def test_clear_command_removes_marker(self, credit_env, capsys):
        record_cline_credit_exhausted(_REASON)
        import main

        main._cli_providers(["--clear-credit-state"])
        assert "Cleared" in capsys.readouterr().out
        assert not credit_env.exists()
        main._cli_providers(["--clear-credit-state"])
        assert "No Cline credit-exhausted marker" in capsys.readouterr().out

    def test_providers_help_mentions_flag(self, credit_env, capsys):
        import main

        main._cli_providers(["--help"])
        out = capsys.readouterr().out
        assert "--clear-credit-state" in out
        assert "exhausted" in out


# ── browser default_provider honoring ────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))

from browser_core import ai_bridge, cline_session
from browser_core.ai_bridge import AIBridge


def _hermetic_bridge(monkeypatch, env):
    monkeypatch.setattr(ai_bridge, "_load_env", lambda: env)
    monkeypatch.setattr(AIBridge, "_detect_local", staticmethod(lambda env: {}))
    monkeypatch.setattr(cline_session, "has_session", lambda: False)
    return AIBridge()


class TestBrowserDefaultHonorsMarker:
    def test_cline_default_without_marker(self, credit_env, monkeypatch):
        bridge = _hermetic_bridge(monkeypatch, {"CLINEPASS_API_KEY": "k"})
        assert bridge.default_provider() == "cline-usage"
        assert bridge.cline_credit_exhausted() is False

    def test_cline_skipped_with_marker(self, credit_env, monkeypatch):
        record_cline_credit_exhausted(_REASON)
        bridge = _hermetic_bridge(monkeypatch, {"CLINEPASS_API_KEY": "k"})
        assert bridge.cline_credit_exhausted() is True
        assert bridge.default_provider() is None

    def test_falls_through_to_keyed_cloud(self, credit_env, monkeypatch):
        record_cline_credit_exhausted(_REASON)
        bridge = _hermetic_bridge(monkeypatch, {"CLINEPASS_API_KEY": "k", "OPENAI_API_KEY": "sk-x"})
        assert bridge.default_provider() == "openai"

    def test_local_still_wins_with_marker(self, credit_env, monkeypatch):
        record_cline_credit_exhausted(_REASON)
        monkeypatch.setattr(ai_bridge, "_load_env", lambda: {"CLINEPASS_API_KEY": "k"})
        monkeypatch.setattr(
            AIBridge,
            "_detect_local",
            staticmethod(lambda env: {"ollama": ("m", "http://127.0.0.1:11434/v1", "", "openai")}),
        )
        monkeypatch.setattr(cline_session, "has_session", lambda: False)
        assert AIBridge().default_provider() == "ollama"

    def test_frozen_fallback_matches_core(self, credit_env, monkeypatch):
        # Frozen browser: `core` may be unimportable — the inline fallback
        # must agree with the canonical reader on the same file.
        monkeypatch.setattr(ai_bridge, "_core_cline_exhausted", None)
        assert ai_bridge._cline_credit_exhausted() is False
        record_cline_credit_exhausted(_REASON)
        assert ai_bridge._cline_credit_exhausted() is True
        credit_env.write_text("{corrupt", encoding="utf-8")
        assert ai_bridge._cline_credit_exhausted() is False


# ── terminal UI indicator ────────────────────────────────────────────────


def _entries():
    base = {
        "base_url": "https://api.cline.bot/api/v1",
        "model": "deepseek/deepseek-chat",
        "env_key": "CLINEPASS_API_KEY",
        "key_present": True,
        "local": False,
        "free_tier": True,
        "configured": False,
        "current": False,
        "credit_exhausted": True,
    }
    openai = dict(
        base,
        id="openai",
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        model="gpt-4o",
        env_key="OPENAI_API_KEY",
        key_present=False,
        free_tier=False,
        credit_exhausted=False,
    )
    return [dict(base, id="cline-usage", name="Cline (usage)"), openai]


class TestProviderUIIndicator:
    def test_rich_or_ansi_shows_exhausted(self, credit_env, capsys):
        from ui import TerminalUI

        TerminalUI().show_providers(_entries())
        out = capsys.readouterr().out
        assert "exhausted" in out
        assert "clear-credit-state" in out

    def test_ansi_path_shows_exhausted(self, credit_env, capsys):
        from ui import TerminalUI

        TerminalUI()._show_providers_ansi(_entries())
        out = capsys.readouterr().out
        assert "exhausted" in out
        assert "clear-credit-state" in out

    def test_healthy_rows_have_no_exhausted_hint(self, credit_env, capsys):
        from ui import TerminalUI

        rows = _entries()
        rows[0]["credit_exhausted"] = False
        rows[0]["configured"] = True
        TerminalUI()._show_providers_ansi(rows)
        out = capsys.readouterr().out
        assert "exhausted" not in out
