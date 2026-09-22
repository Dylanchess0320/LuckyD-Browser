import sys
import os
import pytest
import asyncio
from unittest.mock import MagicMock, patch

import main as main_module

@pytest.fixture
def mock_subcommands(monkeypatch):
    mocks = {
        "model": MagicMock(),
        "providers": MagicMock(),
        "plugin": MagicMock(),
        "custom_provider": MagicMock(),
        "schedule": MagicMock(),
        "acp_main": MagicMock(return_value=0),
    }
    monkeypatch.setattr(main_module, "_cli_model", mocks["model"])
    monkeypatch.setattr(main_module, "_cli_providers", mocks["providers"])
    monkeypatch.setattr(main_module, "_cli_plugin", mocks["plugin"])
    monkeypatch.setattr(main_module, "_cli_custom_provider", mocks["custom_provider"])
    monkeypatch.setattr(main_module, "_cli_schedule", mocks["schedule"])

    # Mock ACP main directly since it is imported inside the function
    import acp_server
    monkeypatch.setattr(acp_server, "main", mocks["acp_main"])

    return mocks

def test_main_dispatch_model(mock_subcommands, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["lucky-code", "model", "flash"])
    main_module.main()
    mock_subcommands["model"].assert_called_once_with(["flash"])

def test_main_dispatch_providers(mock_subcommands, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["lucky-code", "providers"])
    main_module.main()
    mock_subcommands["providers"].assert_called_once_with([])

def test_main_dispatch_plugin(mock_subcommands, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["lucky-code", "plugin", "list"])
    main_module.main()
    mock_subcommands["plugin"].assert_called_once_with(["list"])

def test_main_dispatch_custom_provider(mock_subcommands, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["lucky-code", "custom-provider", "add", "x"])
    main_module.main()
    mock_subcommands["custom_provider"].assert_called_once_with(["add", "x"])

def test_main_dispatch_schedule(mock_subcommands, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["lucky-code", "schedule"])
    main_module.main()
    mock_subcommands["schedule"].assert_called_once_with([])

def test_main_dispatch_acp(mock_subcommands, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["lucky-code", "--acp"])
    with pytest.raises(SystemExit) as exc:
        main_module.main()
    assert exc.value.code == 0
    mock_subcommands["acp_main"].assert_called_once()

def test_main_version(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["lucky-code", "-v"])
    monkeypatch.setenv("LUCKYD_AGENT_VERSION", "v99.99")
    monkeypatch.setenv("LUCKYD_AGENT_NAME", "TestAgent")

    with pytest.raises(SystemExit) as exc:
        main_module.main()

    assert exc.value.code == 0
    out, _ = capsys.readouterr()
    assert "v99.99" in out
    assert "TestAgent" in out

def test_main_help(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["lucky-code", "--help"])
    with pytest.raises(SystemExit) as exc:
        main_module.main()

    assert exc.value.code == 0
    out, _ = capsys.readouterr()
    assert "Usage:" in out
    assert "Options:" in out

@pytest.fixture
def mock_core_deps(monkeypatch):
    # Create coroutine mocks for the asyncio endpoints
    async def _mock_run_repl(*args, **kwargs):
        pass
    async def _mock_run_one_shot(*args, **kwargs):
        pass
    async def _mock_run_one_shot_json(*args, **kwargs):
        pass

    mocks = {
        "get_config": MagicMock(return_value={
            "model": "deepseek",
            "temperature": 0.0,
            "max_tokens": 1024,
            "api_key": "test_key",
            "base_url": "https://api.deepseek.com",
            "provider": "deepseek",
        }),
        "resolve_model": MagicMock(return_value="resolved-model"),
        "CodingAgent": MagicMock(),
        "ApprovalHook": MagicMock(),
        "AuditHook": MagicMock(),
        "MCPManager": MagicMock(),
        "get_session_store": MagicMock(),
        "run_repl": MagicMock(side_effect=_mock_run_repl),
        "run_one_shot": MagicMock(side_effect=_mock_run_one_shot),
        "run_one_shot_json": MagicMock(side_effect=_mock_run_one_shot_json),
        "ui": MagicMock(),
        "register_plugin": MagicMock(),
        "_prompt_and_save_api_key": MagicMock(),
    }

    monkeypatch.setattr(main_module, "get_config", mocks["get_config"])
    monkeypatch.setattr(main_module, "resolve_model", mocks["resolve_model"])
    monkeypatch.setattr(main_module, "CodingAgent", mocks["CodingAgent"])
    monkeypatch.setattr(main_module, "ApprovalHook", mocks["ApprovalHook"])
    monkeypatch.setattr(main_module, "AuditHook", mocks["AuditHook"])
    monkeypatch.setattr(main_module, "MCPManager", mocks["MCPManager"])
    monkeypatch.setattr(main_module, "get_session_store", mocks["get_session_store"])
    monkeypatch.setattr(main_module, "run_repl", mocks["run_repl"])
    monkeypatch.setattr(main_module, "run_one_shot", mocks["run_one_shot"])
    monkeypatch.setattr(main_module, "run_one_shot_json", mocks["run_one_shot_json"])
    monkeypatch.setattr(main_module, "ui", mocks["ui"])
    monkeypatch.setattr(main_module, "register_plugin", mocks["register_plugin"])
    monkeypatch.setattr(main_module, "_prompt_and_save_api_key", mocks["_prompt_and_save_api_key"])

    # Also patch sys.exit since main might exit
    monkeypatch.setattr(sys, "exit", MagicMock())

    return mocks

def test_main_cli_flags_parsing(mock_core_deps, monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "lucky-code",
        "--model", "flash",
        "--provider", "openai",
        "--thinking",
        "--temp", "0.5",
        "--max-turns", "50",
        "--permission-mode", "acceptEdits",
        "--agent", "2",
        "--json",
        "do", "something", "cool"
    ])

    main_module.main()

    assert os.environ.get("CODING_AGENT_PROVIDER") == "openai"
    assert os.environ.get("CODING_AGENT_THINKING") == "true"
    assert os.environ.get("CODING_AGENT_MAX_TURNS") == "50"
    assert os.environ.get("LUCKYD_AGENT_SLOT") == "2"

    # get_config is called a few times due to re-loading cfg on environ updates
    assert mock_core_deps["get_config"].called

    # verify one_shot parsing
    assert mock_core_deps["run_one_shot_json"].called

def test_main_no_api_key_one_shot_exit(mock_core_deps, monkeypatch):
    mock_core_deps["get_config"].return_value["api_key"] = ""
    monkeypatch.setattr(sys, "argv", ["lucky-code", "one", "shot"])

    main_module.main()

    # System exit should be called because no API key
    sys.exit.assert_called_with(1)
    mock_core_deps["ui"].error.assert_called()

def test_main_resume_session(mock_core_deps, monkeypatch):
    mock_session_store = MagicMock()
    mock_session = {"preview": "test preview"}
    mock_session_store.latest.return_value = mock_session
    mock_session_store.load.return_value = mock_session
    mock_core_deps["get_session_store"].return_value = mock_session_store

    # Test latest resume
    monkeypatch.setattr(sys, "argv", ["lucky-code", "-c"])
    main_module.main()
    mock_session_store.latest.assert_called()
    mock_core_deps["run_repl"].assert_called()
    mock_core_deps["CodingAgent"].return_value.restore_session.assert_called_with(mock_session)

    # Test specific resume
    monkeypatch.setattr(sys, "argv", ["lucky-code", "--resume", "1234"])
    main_module.main()
    mock_session_store.load.assert_called_with("1234")
