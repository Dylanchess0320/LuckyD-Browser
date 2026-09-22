"""LuckyD 9.8 — goals, plugins, custom providers, ACP, MiniMax CLI tools."""

from __future__ import annotations

import io
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from acp_server import AcpServer
from core import custom_providers as _custom_providers
from core.custom_providers import (
    add_provider,
    get_provider,
    list_providers,
    remove_provider,
    validate_api_format,
    validate_provider_id,
)
from core.goals import Goal, GoalStore, parse_budget, parse_goal_command
from core.plugins import (
    add_plugin,
    disable_plugin,
    enable_plugin,
    list_plugins,
    remove_plugin,
    set_enabled,
    split_name_ref,
)
from tools.minimax_cli import run_mcode, run_mmx

# ── goals ──────────────────────────────────────────────────────────────


def test_goal_budget_parse() -> None:
    assert parse_budget("50K") == 50_000
    assert parse_budget("2M") == 2_000_000
    assert parse_budget("1000") == 1000
    assert parse_budget("clear") is None


def test_goal_command_parse() -> None:
    assert parse_goal_command("") == ("status", "")
    assert parse_goal_command("pause") == ("pause", "")
    assert parse_goal_command("budget 50K") == ("budget", "50K")
    assert parse_goal_command("budget=2M") == ("budget", "2M")
    assert parse_goal_command("hello world") == ("set", "hello world")


def test_goal_store_roundtrip() -> None:
    store = GoalStore()
    assert "No active goal" in store.describe()
    store.handle_command("ship 9.8")
    assert "ship 9.8" in store.describe()
    assert "50K" in store.handle_command("budget 50K")
    assert "paused" in store.handle_command("pause").lower() or "paused" in store.describe()
    assert "resumed" in store.handle_command("resume").lower()
    data = store.to_dict()
    assert data and data["text"] == "ship 9.8"
    restored = GoalStore()
    restored.restore(data)
    assert restored.describe() == store.describe()
    assert "cleared" in store.handle_command("clear").lower()


def test_goal_from_dict_rejects_empty() -> None:
    assert Goal.from_dict(None) is None
    assert Goal.from_dict({}) is None
    assert Goal.from_dict({"text": ""}) is None


# ── plugins ────────────────────────────────────────────────────────────


def test_split_name_ref() -> None:
    assert split_name_ref("foo") == ("foo", "")
    assert split_name_ref("foo@official") == ("foo", "official")
    assert split_name_ref("  foo @ LOCAL ") == ("foo", "local")


def test_plugins_local_list_enable_disable(tmp_path: Path) -> None:
    plugdir = tmp_path / ".luckyd-code" / "plugins" / "demo"
    plugdir.mkdir(parents=True)
    (plugdir / "SKILL.md").write_text("description: demo plugin\n", encoding="utf-8")
    rows = list_plugins(home=tmp_path)
    assert [p.name for p in rows] == ["demo"]
    set_enabled("demo", False, tmp_path)
    assert list_plugins(home=tmp_path)[0].enabled is False
    assert enable_plugin("demo", tmp_path) is True
    assert disable_plugin("demo", tmp_path) is True
    assert remove_plugin("demo", tmp_path) is True
    assert list_plugins(home=tmp_path) == []


def test_plugins_add_official(tmp_path: Path, monkeypatch) -> None:
    import core.plugins as plugins_mod

    catalog = tmp_path / "kit" / "skills" / "demo"
    catalog.mkdir(parents=True)
    (catalog / "SKILL.md").write_text("description: demo\n", encoding="utf-8")
    monkeypatch.setattr(
        plugins_mod, "official_catalog_dir", lambda repo_root=None: tmp_path / "kit" / "skills"
    )
    info = add_plugin("demo", home=tmp_path, repo_root=tmp_path)
    assert info.name == "demo"
    assert (tmp_path / ".luckyd-code" / "plugins" / "demo" / "SKILL.md").is_file()


# ── custom providers ───────────────────────────────────────────────────


def test_custom_provider_id_and_format_validation() -> None:
    assert validate_provider_id("My-Provider_1") == "my-provider_1"
    try:
        validate_provider_id("bad id!")
        raise AssertionError("should raise")
    except ValueError:
        pass
    assert validate_api_format("OpenAI-Completions") == "openai-completions"
    try:
        validate_api_format("nope")
        raise AssertionError("should raise")
    except ValueError:
        pass


def test_custom_provider_add_list_remove(tmp_path: Path) -> None:
    store = tmp_path / "providers.json"
    add_provider("demo", "https://example.com/v1", "openai-completions", ["m1", "m2"], store=store)
    rows = list_providers(store)
    assert len(rows) == 1 and rows[0].id == "demo"
    assert get_provider("demo", store) is not None
    try:
        add_provider("demo", "https://example.com/v1", "openai-completions", ["m1"], store=store)
        raise AssertionError("should raise")
    except ValueError:
        pass
    assert remove_provider("demo", store) is True
    assert list_providers(store) == []


def test_custom_provider_test_probe(tmp_path: Path) -> None:
    store = tmp_path / "providers.json"
    provider = add_provider(
        "demo", "https://example.com/v1", "openai-completions", ["m1"], store=store
    )

    class _Resp:
        status_code = 200

        def json(self):
            return {"data": [{"id": "m1"}, {"id": "m2"}]}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None):
            assert url == "https://example.com/v1/models"
            return _Resp()

    result = _custom_providers.test_provider(provider, client_factory=_Client)
    assert result["ok"] is True and "m1" in result["models"]


# ── ACP server ─────────────────────────────────────────────────────────


def test_acp_initialize_ping_unknown() -> None:
    server = AcpServer(stdin=io.StringIO(""), stdout=io.StringIO())
    assert server.handle({"method": "ping", "id": 1})["result"] == {"ok": True}

    # Test missing id edge case
    res_no_id = server.handle({"method": "ping"})
    assert res_no_id["id"] is None
    assert res_no_id["result"] == {"ok": True}

    # Not initialized → prompt is rejected.
    err = server.handle({"method": "prompt", "params": {"text": "hi"}, "id": 2})
    assert "error" in err
    ok = server.handle({"method": "initialize", "id": 3})
    assert ok["result"]["agent"]["name"] == "LuckyD Code"
    assert server.handle({"method": "bogus", "id": 4})["error"]["code"] == -32601


def test_acp_goal_and_steer() -> None:
    server = AcpServer(stdin=io.StringIO(""), stdout=io.StringIO())
    server.handle({"method": "initialize", "id": 1})

    class _Goals:
        def __init__(self):
            self.text = ""

        def describe(self):
            return self.text or "No active goal."

        def handle_command(self, args: str) -> str:
            self.text = args
            return f"Goal: {args}"

    class _Agent:
        def __init__(self):
            self.goals = _Goals()

        def run(self, text: str) -> str:
            return f"echo:{text}"

        def steer(self, text: str) -> str:
            return f"steered:{text}"

    server._agent = _Agent()
    prompt = server.handle({"method": "prompt", "params": {"text": "hello"}, "id": 2})
    assert prompt["result"]["text"] == "echo:hello"
    goal = server.handle({"method": "goal", "params": {"action": "set", "value": "x"}, "id": 3})
    assert "x" in goal["result"]["text"]
    steer = server.handle({"method": "steer", "params": {"text": "faster"}, "id": 4})
    assert steer["result"]["text"] == "steered:faster"


# ── minimax CLI ────────────────────────────────────────────────────────


def test_minimax_cli_missing_binary(monkeypatch) -> None:
    monkeypatch.setattr("tools.minimax_cli.find_mcode_exe", lambda: None)
    monkeypatch.setattr("tools.minimax_cli.find_mmx_exe", lambda: None)
    assert "not found" in run_mcode("hello")["error"].lower()
    assert "not found" in run_mmx("hello")["error"].lower()


def test_minimax_cli_run_capture(monkeypatch) -> None:
    import tools.minimax_cli as mm

    monkeypatch.setattr(mm, "find_mcode_exe", lambda: "/bin/mcode")
    monkeypatch.setattr(mm, "find_mmx_exe", lambda: "/bin/mmx")

    class _Done:
        stdout = "out"
        stderr = "err"
        returncode = 0

    monkeypatch.setattr(mm.subprocess, "run", lambda *a, **k: _Done())
    assert run_mcode("hi")["ok"] is True
    assert run_mmx("hi")["ok"] is True

    class _Fail:
        stdout = ""
        stderr = "boom"
        returncode = 1

    monkeypatch.setattr(mm.subprocess, "run", lambda *a, **k: _Fail())
    assert run_mcode("hi")["ok"] is False
    assert "exit code 1" in run_mcode("hi")["error"]


def test_plugin_and_provider_cli_wiring() -> None:
    text = (_REPO_ROOT / "main.py").read_text(encoding="utf-8")
    assert '"plugin"' in text or "'plugin'" in text or 'args[0] == "plugin"' in text
    assert "custom-provider" in text
    assert "--acp" in text


def test_minimax_cli_tools_are_real_registry_tools() -> None:
    """Importing tools.minimax_cli must not poison the agent's tool registry.

    Regression test: McodeTool/MmxTool were once plain classes registered at
    import time, so registry.openai_tools() (called on every agent turn)
    crashed with AttributeError: 'McodeTool' object has no attribute
    'to_openai_schema'.
    """
    from tools.base import ToolBase
    from tools.minimax_cli import McodeTool, MmxTool
    from tools.registry import registry

    assert issubclass(McodeTool, ToolBase)
    assert issubclass(MmxTool, ToolBase)
    assert isinstance(registry.get("mcode"), ToolBase)
    assert isinstance(registry.get("mmx"), ToolBase)
    names = {s["function"]["name"] for s in registry.openai_tools()}
    assert {"mcode", "mmx"} <= names


def test_acp_serve_forever_error_handling() -> None:
    stdin = io.StringIO(
        '{invalid json\n{"method": "ping", "id": 1}\n{"method": "shutdown", "id": 2}\n'
    )
    stdout = io.StringIO()
    server = AcpServer(stdin=stdin, stdout=stdout)
    exit_code = server.serve_forever()
    assert exit_code == 0
    out = stdout.getvalue()

    # check that it responded with parse error for the first line
    assert "parse error" in out
    assert "-32700" in out

    # check that it gracefully continued to ping
    assert '"ok": true' in out
