"""Google Jules CLI in the Agent Mesh (10.1).

mesh-jules rides alongside OpenCode, MiniMax Code, and Cline — it coexists
with them and must never displace them. These tests pin the wiring:
shell allowlist entries, exe resolution, dock metadata, and availability
reporting.
"""

from pathlib import Path

import pytest


def test_jules_shells_allowlisted() -> None:
    """mesh-jules (and the bare `jules` alias) must be allowlisted shells.

    2026-09-24: mesh-aider / aider ride the same mesh rails.
    """
    from browser.browser_core.terminal_server import MESH_SHELLS, SHELLS

    assert "mesh-jules" in SHELLS
    assert "jules" in SHELLS
    assert MESH_SHELLS["mesh-jules"] == "jules"
    assert MESH_SHELLS["jules"] == "jules"
    assert "mesh-aider" in SHELLS
    assert "aider" in SHELLS
    assert MESH_SHELLS["mesh-aider"] == "aider"
    assert MESH_SHELLS["aider"] == "aider"


def test_jules_mesh_dock_entry() -> None:
    """The dock chip metadata must exist with the 4-tuple shape.

    2026-09-24: Aider's dock chip gets the same guarantee.
    """
    from browser.browser_core.terminal_page import _MESH_AGENTS, _SHELL_LABELS

    entry = _MESH_AGENTS["mesh-jules"]
    assert isinstance(entry, tuple) and len(entry) == 4
    label, emoji, accent, blurb = entry
    assert label == "Jules"
    assert emoji and accent.startswith("#") and blurb
    assert _SHELL_LABELS.get("mesh-jules") == "Jules"
    assert _SHELL_LABELS.get("jules") == "Jules"

    aider = _MESH_AGENTS["mesh-aider"]
    assert isinstance(aider, tuple) and len(aider) == 4
    alabel, aemoji, aaccent, ablurb = aider
    assert alabel == "Aider"
    assert aemoji and aaccent.startswith("#") and ablurb
    assert _SHELL_LABELS.get("mesh-aider") == "Aider"
    assert _SHELL_LABELS.get("aider") == "Aider"


def test_jules_mesh_emoji_unused() -> None:
    """Jules' dock emoji must not collide with another agent's.

    2026-09-24: same guarantee for Aider's emoji.
    """
    from browser.browser_core.terminal_page import _MESH_AGENTS

    emojis = [meta[1] for shell, meta in _MESH_AGENTS.items() if shell != "mesh-jules"]
    assert _MESH_AGENTS["mesh-jules"][1] not in emojis
    aider_emojis = [meta[1] for shell, meta in _MESH_AGENTS.items() if shell != "mesh-aider"]
    assert _MESH_AGENTS["mesh-aider"][1] not in aider_emojis


def test_jules_shell_command_resolves(monkeypatch, tmp_path: Path) -> None:
    """mesh-jules spawns the real `jules` CLI, no extra args."""
    from browser.browser_core.terminal_server import _mesh_shell_command

    mock_jules = tmp_path / "jules.exe"
    mock_jules.touch()
    monkeypatch.setattr(
        "browser.browser_core.terminal_server._find_mesh_exe",
        lambda exe: str(mock_jules) if exe == "jules" else None,
    )
    cmd = _mesh_shell_command("mesh-jules")
    assert len(cmd) == 1 and cmd[0].lower().endswith("jules.exe")
    # Bare alias resolves to the same executable.
    cmd2 = _mesh_shell_command("jules")
    assert cmd2[0].lower().endswith("jules.exe")


def test_jules_availability_reported(monkeypatch) -> None:
    """mesh_shells_available() reflects whether `jules` is on PATH."""
    from browser.browser_core.terminal_server import (
        _reset_mesh_availability_cache,
        mesh_shells_available,
    )

    monkeypatch.setattr(
        "browser.browser_core.terminal_server._find_mesh_exe",
        lambda exe: "/fake/jules" if exe == "jules" else None,
    )
    _reset_mesh_availability_cache()
    avail = mesh_shells_available()
    assert avail["mesh-jules"] is True
    assert avail["jules"] is True

    monkeypatch.setattr("browser.browser_core.terminal_server._find_mesh_exe", lambda exe: None)
    _reset_mesh_availability_cache()
    avail = mesh_shells_available()
    assert avail["mesh-jules"] is False
    assert avail["jules"] is False


def test_jules_coexists_with_other_agents() -> None:
    """Adding Jules must not disturb OpenCode, MiniMax Code, or Cline.

    2026-09-24: Aider (mesh-aider) joins the same way — it coexists too.
    """
    from browser.browser_core.terminal_page import _MESH_AGENTS
    from browser.browser_core.terminal_server import MESH_SHELLS, SHELLS

    for shell in ("mesh-opencode", "mesh-mcode", "mesh-mmx", "mesh-cline", "mesh-aider"):
        assert shell in SHELLS
        assert shell in MESH_SHELLS
        assert shell in _MESH_AGENTS


def test_mesh_doctor_and_tier_labels(monkeypatch) -> None:
    """mesh_doctor() and dock chips label every backend as free, keyed, or credit-billed."""
    from browser.browser_core.terminal_page import _MESH_AGENTS, _MESH_TIERS, _mesh_dock_html
    from browser.browser_core.terminal_server import (
        MESH_BACKEND_SPECS,
        _reset_mesh_availability_cache,
        mesh_doctor,
    )

    assert set(MESH_BACKEND_SPECS.keys()) == set(_MESH_AGENTS.keys())
    assert set(_MESH_TIERS.keys()) == set(_MESH_AGENTS.keys())

    monkeypatch.setattr(
        "browser.browser_core.terminal_server._find_mesh_exe",
        lambda exe: f"/fake/{exe}" if exe in ("jules", "aider") else None,
    )
    for var in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENROUTER_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    _reset_mesh_availability_cache()

    rows = {r["shell"]: r for r in mesh_doctor()}
    assert len(rows) == len(_MESH_AGENTS)
    for shell, r in rows.items():
        assert r["tier"] in ("free", "keyed", "credit-billed")
        assert r["tier"] == _MESH_TIERS[shell]
        assert isinstance(r["install_hint"], str) and r["install_hint"]

    # Jules is free and installed -> ready
    assert rows["mesh-jules"]["tier"] == "free"
    assert rows["mesh-jules"]["installed"] is True
    assert rows["mesh-jules"]["ready"] is True

    # Aider is keyed -> installed but missing API key when no key env is set
    assert rows["mesh-aider"]["tier"] == "keyed"
    assert rows["mesh-aider"]["installed"] is True
    assert rows["mesh-aider"]["auth_ok"] is False
    assert rows["mesh-aider"]["ready"] is False

    # Cline is credit-billed
    assert rows["mesh-cline"]["tier"] == "credit-billed"

    html = _mesh_dock_html()
    assert 'data-tier="free"' in html
    assert 'data-tier="keyed"' in html
    assert 'data-tier="credit-billed"' in html


def test_session_store_resolve_and_cost_estimate(tmp_path: Path) -> None:
    """SessionStore.resolve supports latest, 1-based index, prefix, and keyword with ambiguity hints."""
    from core.session_store import SessionStore
    from llm import CostTracker

    store = SessionStore(sessions_dir=tmp_path)
    store.save(
        "conv_alpha_111",
        [{"role": "user", "content": "Fix authentication bug in login flow"}],
        model="deepseek-v4",
        provider="deepseek",
    )
    store.save(
        "conv_beta_222",
        [{"role": "user", "content": "Add dark mode toggle to settings"}],
        model="claude-sonnet-4-5",
        provider="anthropic",
    )

    # 1-based index resolution
    rec1, cands1 = store.resolve("1")
    assert rec1 is not None and not cands1
    assert rec1["conversation_id"] in ("conv_alpha_111", "conv_beta_222")

    # Unique prefix resolution
    rec_a, cands_a = store.resolve("conv_alpha")
    assert rec_a is not None and rec_a["conversation_id"] == "conv_alpha_111"
    assert not cands_a

    # Ambiguous prefix returns candidates
    rec_amb, cands_amb = store.resolve("conv_")
    assert rec_amb is None
    assert len(cands_amb) == 2

    # Keyword substring in preview
    rec_kw, cands_kw = store.resolve("dark mode")
    assert rec_kw is not None and rec_kw["conversation_id"] == "conv_beta_222"
    assert not cands_kw

    # CostTracker upfront run estimation
    tracker = CostTracker()
    est_paid = tracker.estimate_run("deepseek-v4", turns=10)
    assert est_paid["pricing_known"] is True
    assert est_paid["is_free"] is False
    assert float(est_paid["estimated_cost_usd"]) > 0.0

    est_free = tracker.estimate_run("nvidia/nemotron-3-ultra-550b-a55b:free", turns=10)
    assert est_free["is_free"] is True
    assert est_free["estimated_cost_usd"] == 0.0
    assert "$0 / free tier" in tracker.estimate_summary(
        "nvidia/nemotron-3-ultra-550b-a55b:free", turns=10
    )


def test_cli_doctor_json_and_text(capsys) -> None:
    """lucky-code doctor outputs both JSON and human-readable diagnostics."""
    import json

    import main

    main._cli_doctor(["--json"])
    payload = json.loads(capsys.readouterr().out)
    assert "active_provider" in payload
    assert "mesh_backends" in payload
    assert len(payload["mesh_backends"]) >= 16

    main._cli_doctor([])
    text_out = capsys.readouterr().out
    assert "LuckyD Code & Agent Mesh Doctor" in text_out
    assert "credit-billed" in text_out


@pytest.mark.asyncio
async def test_action_undo_stack_and_js_driver_undo() -> None:
    """ActionUndoStack records reversible actions and JsDriver reverts them via undo."""
    from browser_core.agent import ActionUndoStack, AgentSession, JsDriver

    stack = ActionUndoStack(max_entries=5)
    assert len(stack) == 0
    assert stack.pop() is None

    # Verify undo_js_for across all supported action kinds
    assert "typed" in ActionUndoStack.undo_js_for({"kind": "type", "index": 3, "prev_value": "old"})
    assert "selected" in ActionUndoStack.undo_js_for(
        {"kind": "select", "index": 2, "prev_value": "Opt A"}
    )
    assert "scrollBy(0, -700)" in ActionUndoStack.undo_js_for({"kind": "scroll", "dy": 700})
    assert "window.location.href" in ActionUndoStack.undo_js_for(
        {"kind": "navigate", "prev_url": "https://example.com/a"}
    )
    assert "history.back()" in ActionUndoStack.undo_js_for(
        {"kind": "click_nav", "prev_url": "about:blank"}
    )

    executed_js: list[str] = []

    class FakeJsBridge:
        async def run(self, js: str, timeout: float = 8.0):
            executed_js.append(js)
            if "more_below" in js:
                import json

                return json.dumps(
                    {
                        "url": "https://example.com/form",
                        "title": "Form",
                        "elements": "[4] <input> Name",
                        "text": "Form",
                        "dialog": "",
                        "more_below": False,
                    }
                )
            if "document.readyState" in js and "location.href" in js:
                return "https://example.com/form|complete"
            if "e.value !== undefined" in js:
                return "initial-val"
            if "typed " in js:
                return "typed 11 chars into <input>"
            return "ok"

    driver = JsDriver(FakeJsBridge(), undo_stack=stack)
    res_type = await driver.act({"action": "type", "index": 4, "text": "updated-val"})
    assert "typed " in res_type
    assert len(stack) == 1
    peeked = stack.peek()
    assert peeked is not None
    assert peeked["kind"] == "type"
    assert peeked["prev_value"] == "initial-val"
    assert peeked["new_value"] == "updated-val"

    res_scroll = await driver.act({"action": "scroll", "text": "down"})
    assert res_scroll == "scrolled"
    assert len(stack) == 2

    # Undo via act({"action": "undo"}) pops scroll first, then type
    undo1 = await driver.act({"action": "undo"})
    assert "undid" in undo1
    assert len(stack) == 1

    # Test AgentSession step progress percentage and undo_last()
    class FakeAIBridge:
        def default_provider(self) -> str:
            return "ollama"

        def is_local(self, provider=None) -> bool:
            return False

        def supports_vision(self, provider=None) -> bool:
            return False

        async def chat(self, messages, provider=None):
            return (
                '{"actions": [{"action": "scroll", "text": "down", "reason": "check page"}, {"action": "done", "reason": "all finished", "text": "Done!"}]}',
                "ollama",
            )

    steps_seen: list[str] = []
    session = AgentSession(
        FakeAIBridge(),
        FakeJsBridge(),
        max_steps=10,
        use_cdp=False,
        undo_stack=stack,
    )
    undo_res = await session.undo_last()
    assert "undid" in undo_res
    assert len(stack) == 0

    final = await session.run("Complete the form", on_step=steps_seen.append)
    assert final == "Done!"
    assert any("Step 1/10 (10%)" in s for s in steps_seen)


def test_onboarding_status_and_control_server_new_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    """AIBridge.onboarding_status/start_ollama_pull and Control API /mesh/doctor, /onboarding/*, /undo/*."""
    import json
    import shutil
    import subprocess
    import urllib.request

    from browser_core.ai_bridge import AIBridge
    from browser_core.control_server import BrowserControlServer

    bridge = AIBridge()
    status = bridge.onboarding_status("llama3.2:3b")
    assert "ready" in status
    assert "ollama_installed" in status
    assert "faq_topics" in status
    topic_ids = {t["id"] for t in status["faq_topics"]}
    assert topic_ids >= {"ollama", "smartscreen", "halcyon", "cline_zero"}

    # Invalid model name is rejected before spawning anything
    bad_pull = AIBridge.start_ollama_pull("bad model; rm -rf /")
    assert bad_pull["started"] is False
    assert "invalid" in str(bad_pull["error"])

    # Mock shutil.which and subprocess.Popen for valid model pull
    class DummyProc:
        pid = 43210

    monkeypatch.setattr(shutil, "which", lambda _: __file__)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: DummyProc())
    good_pull = AIBridge.start_ollama_pull("llama3.2:3b")
    assert good_pull["started"] is True
    assert good_pull["pid"] == 43210
    assert good_pull["command"] == "ollama pull llama3.2:3b"

    class DummyBackend:
        def status(self):
            return {"tabs": 1, "current_url": "https://example.com"}

        def ai_info(self):
            return {"ai_providers": ["ollama"], "ai_default": "ollama", "ai_local_status": {}}

        def undo_stack(self):
            return [{"kind": "scroll", "dy": 700, "summary": "scroll down (700px)"}]

        def undo_last(self):
            return {"undone": True, "detail": "undid scroll"}

    srv = BrowserControlServer(DummyBackend(), host="127.0.0.1", port=0, token="secret-tok")
    srv.start()
    try:
        headers = {"Authorization": "Bearer secret-tok", "Content-Type": "application/json"}

        req_doc = urllib.request.Request(f"{srv.base_url}/mesh/doctor", headers=headers)
        with urllib.request.urlopen(req_doc, timeout=5) as resp:
            doc_data = json.loads(resp.read().decode())
        assert doc_data["ok"] is True
        assert doc_data["total_count"] >= 16

        req_onb = urllib.request.Request(f"{srv.base_url}/onboarding/status", headers=headers)
        with urllib.request.urlopen(req_onb, timeout=5) as resp:
            onb_data = json.loads(resp.read().decode())
        assert onb_data["ok"] is True
        assert "ollama_installed" in onb_data

        req_ustack = urllib.request.Request(f"{srv.base_url}/undo/stack", headers=headers)
        with urllib.request.urlopen(req_ustack, timeout=5) as resp:
            ustack_data = json.loads(resp.read().decode())
        assert ustack_data["ok"] is True
        assert len(ustack_data["stack"]) == 1

        req_undo = urllib.request.Request(
            f"{srv.base_url}/undo", data=b"{}", headers=headers, method="POST"
        )
        with urllib.request.urlopen(req_undo, timeout=5) as resp:
            undo_data = json.loads(resp.read().decode())
        assert undo_data["ok"] is True
        assert undo_data["undone"] is True

        req_pull = urllib.request.Request(
            f"{srv.base_url}/onboarding/ollama-pull",
            data=json.dumps({"model": "llama3.2:3b"}).encode(),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req_pull, timeout=5) as resp:
            pull_data = json.loads(resp.read().decode())
        assert pull_data["ok"] is True
        assert pull_data["started"] is True
    finally:
        srv.stop()
