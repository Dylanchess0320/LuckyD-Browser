"""``/apikey`` - paste, save and enable provider keys without editing .env."""

from __future__ import annotations

import os
from pathlib import Path

import config
import main


def _env_file(monkeypatch, tmp_path: Path) -> Path:
    env = tmp_path / ".env"
    env.write_text("# keep this comment\nOPENAI_API_KEY=old-key\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", env)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("CODING_AGENT_PROVIDER", raising=False)
    return env


def test_save_env_values_replaces_and_appends(monkeypatch, tmp_path):
    env = _env_file(monkeypatch, tmp_path)

    main._save_env_values(
        {
            "OPENAI_API_KEY": "sk-new-openai-key",
            "ANTHROPIC_API_KEY": "sk-ant-new-key",
        }
    )

    text = env.read_text(encoding="utf-8")
    assert text.count("OPENAI_API_KEY=") == 1
    assert "OPENAI_API_KEY=sk-new-openai-key" in text
    assert "ANTHROPIC_API_KEY=sk-ant-new-key" in text
    assert "# keep this comment" in text
    assert not text.startswith("\ufeff")
    assert os.environ["OPENAI_API_KEY"] == "sk-new-openai-key"


def test_remove_env_key_removes_assignment_only(monkeypatch, tmp_path):
    env = _env_file(monkeypatch, tmp_path)
    env.write_text(
        "# OPENAI_API_KEY=commented-out\nOPENAI_API_KEY=real-key\nOTHER=1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "real-key")

    assert main._remove_env_key("OPENAI_API_KEY") is True

    text = env.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=real-key" not in text
    assert "# OPENAI_API_KEY=commented-out" in text
    assert "OTHER=1" in text
    assert "OPENAI_API_KEY" not in os.environ


def test_api_key_target_handles_aliases_and_unknowns():
    pid, env_key, name = main._api_key_target("claude")
    assert pid == "anthropic"
    assert env_key == "ANTHROPIC_API_KEY"
    assert name == "Anthropic"

    pid, env_key, name = main._api_key_target("cline")
    assert pid == "cline-usage"
    assert env_key == "CLINEPASS_API_KEY"

    assert main._api_key_target("not-a-provider") is None


def test_apikey_command_saves_and_switches(monkeypatch, tmp_path):
    env = _env_file(monkeypatch, tmp_path)
    switched: list[tuple[str | None, str]] = []
    monkeypatch.setattr(
        main,
        "_switch_model",
        lambda agent, provider=None, model_name="": switched.append((provider, model_name)),
    )

    main._handle_apikey_command(object(), "/apikey anthropic sk-ant-12345678")

    assert "ANTHROPIC_API_KEY=sk-ant-12345678" in env.read_text(encoding="utf-8")
    assert switched == [("anthropic", "")]


def test_apikey_keep_saves_without_switching(monkeypatch, tmp_path):
    env = _env_file(monkeypatch, tmp_path)
    switched: list[tuple[str | None, str]] = []
    monkeypatch.setattr(
        main,
        "_switch_model",
        lambda agent, provider=None, model_name="": switched.append((provider, model_name)),
    )

    main._handle_apikey_command(object(), "/apikey openrouter sk-or-12345678 --keep")

    assert "OPENROUTER_API_KEY=sk-or-12345678" in env.read_text(encoding="utf-8")
    assert switched == []


def test_apikey_remove(monkeypatch, tmp_path):
    env = _env_file(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "old-key")

    main._handle_apikey_command(object(), "/apikey remove openai")

    assert "OPENAI_API_KEY=old-key" not in env.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" not in os.environ


def test_apikey_rejects_unknown_provider(monkeypatch, tmp_path):
    env = _env_file(monkeypatch, tmp_path)
    before = env.read_text(encoding="utf-8")

    main._handle_apikey_command(object(), "/apikey nope sk-12345678")

    assert env.read_text(encoding="utf-8") == before


def test_show_api_keys_never_prints_the_secret(monkeypatch, tmp_path, capsys):
    _env_file(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret-value-123")
    main._save_env_values({"OPENROUTER_API_KEY": "sk-or-secret-value-123"})

    main._show_api_keys()

    out = capsys.readouterr().out
    assert "sk-or-secret-value-123" not in out
    assert "openrouter" in out


def test_activate_provider_env_writes_provider_and_default_model(monkeypatch, tmp_path):
    env = _env_file(monkeypatch, tmp_path)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)

    assert main._activate_provider_env("openrouter") is True

    text = env.read_text(encoding="utf-8")
    assert "CODING_AGENT_PROVIDER=openrouter" in text
    assert "OPENROUTER_MODEL=openrouter/free" in text
