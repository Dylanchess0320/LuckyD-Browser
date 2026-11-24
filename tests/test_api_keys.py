"""10.9.2 — /key: paste a provider API key into the shared per-user .env.

Covers the credential store both LuckyD Code and the browser AI assistant
use, plus the slash-command surface. No network: verification is either
disabled or faked at urllib's boundary.
"""

from __future__ import annotations

import urllib.error

import pytest

import core.credentials as credentials
from core import slash_commands


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path) -> object:
    """Point the store at a throwaway file and clear every provider key."""
    env_file = tmp_path / "user.env"
    monkeypatch.setenv(credentials.ENV_PATH_OVERRIDE, str(env_file))
    vars_to_clear = {
        var for pid in credentials.key_providers() if (var := credentials.provider_key_var(pid))
    }
    for var in vars_to_clear:
        monkeypatch.delenv(var, raising=False)
    return env_file


def test_provider_key_var_maps_aliases() -> None:
    assert credentials.provider_key_var("openrouter") == "OPENROUTER_API_KEY"
    assert credentials.provider_key_var("OPENAI") == "OPENAI_API_KEY"
    assert credentials.provider_key_var("cline") == "CLINEPASS_API_KEY"
    assert credentials.provider_key_var("gemini") == "GOOGLE_API_KEY"
    assert credentials.provider_key_var("ollama") is None
    assert credentials.provider_key_var("not-a-provider") is None


def test_key_providers_excludes_keyless() -> None:
    providers = credentials.key_providers()
    assert "openrouter" in providers
    assert "ollama" not in providers


def test_mask_key_never_reveals_the_middle() -> None:
    assert credentials.mask_key("") == "(not set)"
    assert credentials.mask_key("short") == "*****"
    masked = credentials.mask_key("aaaaaaaaaaaa1111")
    assert masked.startswith("aaaaaa")
    assert masked.endswith("1111")
    assert "aaaaaaaaaaaa" not in masked


def test_set_api_key_writes_and_applies(_isolate) -> None:
    result = credentials.set_api_key("openrouter", "aaaaaaaaaaaa1111", verify=False)
    assert result["ok"] is True
    assert result["env_var"] == "OPENROUTER_API_KEY"
    assert result["masked"] != "aaaaaaaaaaaa1111"
    assert "OPENROUTER_API_KEY=aaaaaaaaaaaa1111" in _isolate.read_text(encoding="utf-8")
    assert credentials.get_key("openrouter") == "aaaaaaaaaaaa1111"


def test_set_api_key_replaces_existing_line_in_place(_isolate) -> None:
    _isolate.write_text("# my keys\nOPENROUTER_API_KEY=old-value\nOTHER=1\n", encoding="utf-8")
    credentials.set_api_key("openrouter", "new-value", verify=False)
    text = _isolate.read_text(encoding="utf-8")
    assert "OPENROUTER_API_KEY=new-value" in text
    assert "old-value" not in text
    assert "OTHER=1" in text
    assert text.count("OPENROUTER_API_KEY=") == 1


def test_set_api_key_strips_quotes(_isolate) -> None:
    credentials.set_api_key("openai", '  "sk-quoted"  ', verify=False)
    assert "OPENAI_API_KEY=sk-quoted" in _isolate.read_text(encoding="utf-8")


def test_set_api_key_rejects_bad_input(_isolate) -> None:
    assert credentials.set_api_key("bogus", "x", verify=False)["ok"] is False
    assert credentials.set_api_key("openrouter", "   ", verify=False)["ok"] is False
    assert credentials.set_api_key("openrouter", "has a space", verify=False)["ok"] is False
    assert credentials.set_api_key("ollama", "x", verify=False)["ok"] is False
    assert not _isolate.exists()


def test_configured_lists_masked_values(_isolate) -> None:
    credentials.set_api_key("openrouter", "dddddddddddd4444", verify=False)
    rows = credentials.configured()
    assert [row["provider"] for row in rows] == ["openrouter"]
    assert rows[0]["env_var"] == "OPENROUTER_API_KEY"
    assert rows[0]["masked"] != "dddddddddddd4444"


def test_verify_key_accepts(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Resp:
        status = 200

        def read(self, _n: int = 0) -> bytes:
            return b'{"data": {"label": "test"}}'

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *_exc) -> bool:
            return False

    monkeypatch.setattr(credentials.urllib.request, "urlopen", lambda *a, **k: _Resp())
    ok, message = credentials.verify_key("openrouter", "eeeeeeeeeeee5555")
    assert ok is True
    assert "accepted" in message


def test_verify_key_rejects_401(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_a, **_k):
        raise urllib.error.HTTPError("u", 401, "unauthorized", {}, None)

    monkeypatch.setattr(credentials.urllib.request, "urlopen", _raise)
    ok, message = credentials.verify_key("openrouter", "ffffffffffff6666")
    assert ok is False
    assert "401" in message


def test_verify_key_offline_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_a, **_k):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(credentials.urllib.request, "urlopen", _raise)
    ok, message = credentials.verify_key("openrouter", "gggggggggggg7777")
    assert ok is None
    assert "offline" in message


def test_slash_key_saves_inline_key(_isolate, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials, "verify_key", lambda *a, **k: (None, "skipped"))
    handled, response = slash_commands.handle_slash("/key openrouter hhhhhhhhhhhh8888")
    assert handled is True
    assert "Saved" in response
    assert "OPENROUTER_API_KEY=hhhhhhhhhhhh8888" in _isolate.read_text(encoding="utf-8")


def test_slash_keys_lists_without_leaking(_isolate) -> None:
    credentials.set_api_key("openrouter", "hhhhhhhhhhhh8888", verify=False)
    handled, response = slash_commands.handle_slash("/keys")
    assert handled is True
    assert "openrouter" in response
    assert "hhhhhhhhhhhh8888" not in response


def test_slash_key_without_value_returns_usage() -> None:
    handled, response = slash_commands.handle_slash("/key openrouter")
    assert handled is True
    assert "Usage" in response


def test_slash_key_rejects_unknown_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials, "verify_key", lambda *a, **k: (None, "skipped"))
    handled, response = slash_commands.handle_slash("/key nope sk-123")
    assert handled is True
    assert "Unknown provider" in response
