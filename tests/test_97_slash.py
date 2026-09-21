"""Tests for core/slash_commands.py — file-based slash commands."""

from __future__ import annotations

import core.slash_commands as sc


def test_discover_commands_finds_md_files():
    commands = sc.discover_commands()
    for name in ("compact", "review", "init", "skills"):
        assert name in commands, f"/{name} not discovered"
    assert commands["compact"]["description"], "header description should be parsed"
    assert "{args}" in commands["review"]["template"]


def test_commands_dir_points_at_repo_root():
    assert sc.COMMANDS_DIR.name == "slash_commands"
    assert sc.COMMANDS_DIR.parent.name == "LuckyD-Browser"
    assert sc.COMMANDS_DIR.is_dir()


def test_list_commands_includes_builtins_and_discovered():
    listed = dict(sc.list_commands())
    for name in ("help", "compact", "resume", "init", "review", "skills"):
        assert name in listed, f"/{name} missing from list_commands"


def test_help_lists_commands():
    handled, response = sc.handle_slash("/help", None)
    assert handled
    for name in ("/help", "/compact", "/resume", "/init", "/review", "/skills"):
        assert name in response


def test_non_slash_not_handled():
    handled, response = sc.handle_slash("hello world", None)
    assert not handled
    assert response is None


def test_unknown_command_message():
    handled, response = sc.handle_slash("/frobnicate", None)
    assert handled
    assert response == "Unknown command. Try /help."


def test_args_substitution():
    handled, response = sc.handle_slash("/review focus on the new tools", None)
    assert handled
    assert "focus on the new tools" in response
    assert "{args}" not in response


def test_compact_missing_module_does_not_crash(monkeypatch):
    # Simulate core.compaction being unimportable; the handler must degrade.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "core.compaction" or name.startswith("core.compaction."):
            raise ImportError("No module named 'core.compaction'")
        if name == "core" or name.startswith("core."):
            # 'from core import compaction' triggers import of 'core.compaction'
            raise ImportError("No module named 'core.compaction'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    handled, response = sc.handle_slash("/compact", None)
    assert handled
    assert "unavailable" in response.lower()


def test_compact_present_module_runs_without_loop():
    # core/compaction.py exists now; with no running event loop the async
    # maybe_compact is driven to completion and must not crash.
    handled, response = sc.handle_slash("/compact", None)
    assert handled
    assert "compaction" in response.lower()


def test_resume_guard_no_restore_session():
    class NoRestore:
        pass

    handled, response = sc.handle_slash("/resume abc123", NoRestore())
    assert handled
    assert "not supported" in response.lower()


def test_resume_calls_restore_session_and_reports():
    calls = []

    class Agent:
        def restore_session(self, session_id):
            calls.append(session_id)

    handled, response = sc.handle_slash("/resume abc123", Agent())
    assert handled
    assert calls == ["abc123"]
    assert "abc123" in response


def test_resume_requires_id():
    handled, response = sc.handle_slash("/resume", None)
    assert handled
    assert "Usage" in response


def test_init_returns_init_template():
    handled, response = sc.handle_slash("/init extra notes here", None)
    assert handled
    assert "project guidance" in response.lower()
    assert "extra notes here" in response
    assert "{args}" not in response


def test_review_returns_review_template():
    handled, response = sc.handle_slash("/review", None)
    assert handled
    assert "verdict" in response.lower()


def test_discovered_extra_command_substitutes_args(tmp_path, monkeypatch):
    extra = tmp_path / "demo.md"
    extra.write_text("# /demo - demo command\nDo the thing: {args}\n", encoding="utf-8")
    monkeypatch.setattr(sc, "COMMANDS_DIR", tmp_path)

    assert "demo" in sc.discover_commands()
    handled, response = sc.handle_slash("/demo hello", None)
    assert handled
    assert "Do the thing: hello" in response

    listed = dict(sc.list_commands())
    assert listed["demo"] == "demo command"


def test_empty_commands_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "COMMANDS_DIR", tmp_path)
    assert sc.discover_commands() == {}
    handled, response = sc.handle_slash("/help", None)
    assert handled
    assert "/help" in response
