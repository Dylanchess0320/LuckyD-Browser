"""Night-4 browser-core audit: browser_core/agent.py round 1.

The repo's test_agent*.py files cover a different agent.py shim
(core/agent_loop.py); browser_core/agent.py (the in-browser tab-driving
agent) had no coverage. Covers parse_action's reply shapes, JsDriver.act
branches, snapshot parsing, the adaptive _wait_loaded, and AgentSession's
run loop (done flow, invalid replies, LLM errors, stop, step cap,
ACTIVE_SESSIONS accounting) plus _make_plan and _prompt construction.
Qt is mocked; JsBridge is never constructed — a MagicMock stands in.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

for _mod in (
    "PySide6",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BROWSER_DIR = _REPO_ROOT / "browser"
if str(_BROWSER_DIR) not in sys.path:
    sys.path.append(str(_BROWSER_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

import browser_core.agent as agent_mod
from browser_core.agent import AgentSession, JsDriver, parse_action


@pytest.fixture()
def fast_sleep(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())


# ── parse_action ─────────────────────────────────────────────────────


def test_parse_action_batched() -> None:
    raw = 'noise {"actions": [{"action": "click", "index": 2}, {"action": "done", "text": "ok"}]} tail'
    actions = parse_action(raw)
    assert [a["action"] for a in actions] == ["click", "done"]


def test_parse_action_legacy_single() -> None:
    actions = parse_action('{"action": "navigate", "url": "https://x.com"}')
    assert actions == [{"action": "navigate", "url": "https://x.com"}]


def test_parse_action_filters_entries_without_action() -> None:
    raw = '{"actions": [{"note": "no action key"}, {"action": "click", "index": 1}]}'
    assert parse_action(raw) == [{"action": "click", "index": 1}]


def test_parse_action_empty_actions_is_none() -> None:
    assert parse_action('{"actions": []}') is None
    assert parse_action('{"actions": [{"note": 1}]}') is None


def test_parse_action_no_json_is_none() -> None:
    assert parse_action("just words, no braces") is None


def test_parse_action_bad_json_is_none() -> None:
    assert parse_action('{"actions": [oops}') is None


def test_parse_action_non_dict_is_none() -> None:
    assert parse_action('["click"]') is None


def test_parse_action_dict_without_action_is_none() -> None:
    assert parse_action('{"result": "nothing to do"}') is None


# ── JsDriver.act ─────────────────────────────────────────────────────


def _driver(script_map=None, seq=None):
    """JsDriver with a scripted _js coroutine."""
    drv = JsDriver(MagicMock())
    calls = []

    async def _fake_js(script):
        calls.append(script)
        if seq is not None:
            return seq[len(calls) - 1] if len(calls) <= len(seq) else "complete"
        for needle, value in (script_map or {}).items():
            if needle in script:
                return value
        return "complete"

    drv._js = _fake_js
    return drv, calls


@pytest.mark.asyncio()
async def test_act_click(fast_sleep) -> None:
    drv, calls = _driver(seq=["highlighted", "clicked <a> link", "complete"])
    assert await drv.act({"action": "click", "index": 2}) == "clicked <a> link"
    assert "data-ld-agent" in calls[1]  # _CLICK_JS targets the indexed element


@pytest.mark.asyncio()
async def test_act_type_quotes_text_safely(fast_sleep) -> None:
    drv, calls = _driver(seq=["highlighted", "typed 11 chars into <input>", "complete"])
    result = await drv.act({"action": "type", "index": 1, "text": 'say "hi"'})
    assert result == "typed 11 chars into <input>"
    assert '"say \\"hi\\""' in calls[1]  # json.dumps-escaped into the JS


@pytest.mark.asyncio()
async def test_act_press_unknown_key_becomes_enter(fast_sleep) -> None:
    drv, calls = _driver(seq=["pressed Enter on <input>", "complete"])
    assert await drv.act({"action": "press", "text": "F9"}) == "pressed Enter on <input>"
    assert '"Enter"' in calls[0]


@pytest.mark.asyncio()
async def test_act_press_tab(fast_sleep) -> None:
    drv, calls = _driver(seq=["pressed Tab on <input>", "complete"])
    assert await drv.act({"action": "press", "text": "Tab"}) == "pressed Tab on <input>"
    assert '"Tab"' in calls[0]


@pytest.mark.asyncio()
async def test_act_select(fast_sleep) -> None:
    drv, _calls = _driver(seq=["highlighted", "selected Option B"])
    assert await drv.act({"action": "select", "index": 4, "text": "B"}) == "selected Option B"


@pytest.mark.asyncio()
async def test_act_scroll_up_and_down(fast_sleep) -> None:
    drv, calls = _driver()
    assert await drv.act({"action": "scroll", "text": "up"}) == "scrolled"
    assert "scrollBy(0, -700)" in calls[0]
    assert await drv.act({"action": "scroll"}) == "scrolled"
    assert "scrollBy(0, 700)" in calls[1]


@pytest.mark.asyncio()
async def test_act_back(fast_sleep) -> None:
    drv, calls = _driver(seq=["ok", "complete"])
    assert await drv.act({"action": "back"}) == "went back"
    assert "history.back()" in calls[0]


@pytest.mark.asyncio()
async def test_act_navigate_valid(fast_sleep) -> None:
    drv, calls = _driver(seq=["ok", "complete"])
    url = "https://example.com/page"
    assert await drv.act({"action": "navigate", "url": url}) == f"navigated to {url}"
    assert json.dumps(url) in calls[0]


@pytest.mark.asyncio()
async def test_act_navigate_rejects_non_http(fast_sleep) -> None:
    drv, calls = _driver()
    assert await drv.act({"action": "navigate", "url": "file:///etc/passwd"}) == (
        "refused: invalid url"
    )
    assert calls == []  # no JS ever evaluated


@pytest.mark.asyncio()
async def test_act_wait_clamps_and_reports(fast_sleep) -> None:
    drv, _ = _driver()
    sleeps = []
    real = agent_mod.asyncio.sleep

    async def _rec(secs):
        sleeps.append(secs)

    mp = pytest.MonkeyPatch()
    mp.setattr(agent_mod.asyncio, "sleep", _rec)
    try:
        assert await drv.act({"action": "wait", "text": "30"}) == "waited 5.0s"
        assert await drv.act({"action": "wait", "text": "0.01"}) == "waited 0.2s"
        assert await drv.act({"action": "wait", "text": "bogus"}) == "waited 1.0s"
    finally:
        mp.setattr(agent_mod.asyncio, "sleep", real)
    assert sleeps == [5.0, 0.2, 1.0]


@pytest.mark.asyncio()
async def test_act_unknown_is_noop(fast_sleep) -> None:
    drv, calls = _driver()
    assert await drv.act({"action": "teleport"}) == "no-op"
    assert calls == []


# ── JsDriver.snapshot ────────────────────────────────────────────────


@pytest.mark.asyncio()
async def test_snapshot_parses_payload_as_is(fast_sleep) -> None:
    """Valid JSON is returned verbatim — no key defaults merged in."""
    payload = json.dumps({"url": "https://x.com", "title": "X"})
    drv, calls = _driver(seq=[payload])
    snap = await drv.snapshot()
    assert snap == {"url": "https://x.com", "title": "X"}
    assert "80" in calls[0] and "__MAXELS__" not in calls[0]


@pytest.mark.asyncio()
async def test_snapshot_compact_dims(fast_sleep) -> None:
    drv, calls = _driver(seq=["{}"])
    await drv.snapshot(compact=True)
    assert "45" in calls[0] and "800" in calls[0]


@pytest.mark.asyncio()
async def test_snapshot_malformed_json(fast_sleep) -> None:
    drv, _ = _driver(seq=["{bad"])
    snap = await drv.snapshot()
    assert snap["url"] == "" and snap["title"] == ""


@pytest.mark.asyncio()
async def test_snapshot_non_string_result(fast_sleep) -> None:
    drv, _ = _driver(seq=[None])
    assert (await drv.snapshot())["elements"] == ""


# ── _wait_loaded ─────────────────────────────────────────────────────


@pytest.mark.asyncio()
async def test_wait_loaded_fast_path(fast_sleep) -> None:
    drv, _ = _driver(seq=["complete"])
    await drv._wait_loaded(timeout=5.0, initial=0.0)


@pytest.mark.asyncio()
async def test_wait_loaded_tolerates_torn_down_context(fast_sleep) -> None:
    drv, _ = _driver()

    async def _boom(script):
        raise RuntimeError("context gone")

    drv._js = _boom
    await drv._wait_loaded(timeout=0.1, initial=0.0)  # deadline passes, no raise


# ── AgentSession run loop ────────────────────────────────────────────


_SNAP = {
    "url": "https://x.com",
    "title": "X",
    "elements": "[0] <a> link",
    "text": "hello",
    "dialog": "",
    "more_below": False,
}


class _FakeDriver:
    def __init__(self, snaps=None):
        self.snaps = snaps or [dict(_SNAP)]
        self.acts = []
        self.n = 0

    async def snapshot(self, compact=False):
        self.n += 1
        return dict(self.snaps[min(self.n - 1, len(self.snaps) - 1)])

    async def act(self, action):
        self.acts.append(action)
        return f"did {action['action']}"


class _FakeAI:
    def __init__(self, replies, fail=False):
        self.replies = list(replies)
        self.fail = fail
        self.calls = 0

    def default_provider(self):
        return "kimi"

    def is_local(self, provider):
        return False

    async def chat(self, messages, provider=None):
        self.calls += 1
        if self.fail:
            raise RuntimeError("LLM down")
        return self.replies.pop(0), "kimi"


def _session(ai, driver, **kw):
    sess = AgentSession(ai, MagicMock(), max_steps=kw.pop("max_steps", 5), use_cdp=False)
    return sess


@pytest.fixture()
def patched_jsdriver(monkeypatch):
    made = {}

    def _factory(bridge):
        drv = made["driver"]
        return drv

    monkeypatch.setattr(agent_mod, "JsDriver", _factory)
    return made


@pytest.mark.asyncio()
async def test_run_done_first_step(patched_jsdriver, fast_sleep) -> None:
    driver = _FakeDriver()
    patched_jsdriver["driver"] = driver
    ai = _FakeAI(
        [
            "1. open the page\n2. read the answer",  # plan call (first)
            '{"actions": [{"action": "done", "text": "found it"}]}',
        ]
    )
    sess = _session(ai, driver)
    said = []
    result = await sess.run("find it", on_step=said.append)
    assert result == "found it"
    assert any("found it" in m for m in said)
    assert ai.calls == 2  # plan call + step call


@pytest.mark.asyncio()
async def test_run_empty_done_text_defaults(patched_jsdriver, fast_sleep) -> None:
    driver = _FakeDriver()
    patched_jsdriver["driver"] = driver
    ai = _FakeAI(["1. look\n2. finish", '{"actions": [{"action": "done"}]}'])
    assert await _session(ai, driver).run("t") == "Done."


@pytest.mark.asyncio()
async def test_run_invalid_reply_then_done(patched_jsdriver, fast_sleep) -> None:
    driver = _FakeDriver()
    patched_jsdriver["driver"] = driver
    ai = _FakeAI(
        [
            "1. try\n2. finish",
            "not json at all",
            '{"actions": [{"action": "done", "text": "recovered"}]}',
        ]
    )
    assert await _session(ai, driver, max_steps=3).run("t") == "recovered"


@pytest.mark.asyncio()
async def test_run_executes_actions_and_caps_batch(patched_jsdriver, fast_sleep) -> None:
    driver = _FakeDriver()
    patched_jsdriver["driver"] = driver
    many = [{"action": "click", "index": i} for i in range(6)]
    ai = _FakeAI(
        [
            "1. click things\n2. done",
            json.dumps({"actions": many}),
            '{"actions": [{"action": "done", "text": "ok"}]}',
        ]
    )
    assert await _session(ai, driver).run("t") == "ok"
    assert len(driver.acts) == 4  # batch capped at 4


@pytest.mark.asyncio()
async def test_run_stuck_signature_counts(patched_jsdriver, fast_sleep) -> None:
    driver = _FakeDriver(snaps=[dict(_SNAP), dict(_SNAP), dict(_SNAP)])
    patched_jsdriver["driver"] = driver
    ai = _FakeAI(
        [
            "1. scroll\n2. done",
            '{"actions": [{"action": "scroll"}]}',
            '{"actions": [{"action": "scroll"}]}',
            '{"actions": [{"action": "done", "text": "gave up"}]}',
        ]
    )
    assert await _session(ai, driver).run("t") == "gave up"


@pytest.mark.asyncio()
async def test_run_stop_requested(patched_jsdriver, fast_sleep) -> None:
    driver = _FakeDriver()
    patched_jsdriver["driver"] = driver
    ai = _FakeAI(['{"actions": [{"action": "done", "text": "x"}]}'])
    sess = _session(ai, driver)
    sess.stop()
    assert sess.stop_requested is True
    assert await sess.run("t") == "Stopped."


@pytest.mark.asyncio()
async def test_run_llm_error(patched_jsdriver, fast_sleep) -> None:
    driver = _FakeDriver()
    patched_jsdriver["driver"] = driver
    ai = _FakeAI([], fail=True)
    result = await _session(ai, driver).run("t")
    assert result.startswith("LLM error: LLM down")


@pytest.mark.asyncio()
async def test_run_step_cap_exhausted(patched_jsdriver, fast_sleep) -> None:
    driver = _FakeDriver()
    patched_jsdriver["driver"] = driver
    ai = _FakeAI(["1. scroll\n2. more"] + ['{"actions": [{"action": "scroll"}]}'] * 4)
    result = await _session(ai, driver, max_steps=2).run("t")
    assert result == "Reached 2 steps without finishing."


@pytest.mark.asyncio()
async def test_run_snapshot_failure_retries(patched_jsdriver, fast_sleep) -> None:
    class _Flaky(_FakeDriver):
        async def snapshot(self, compact=False):
            if self.n == 0:
                self.n += 1
                raise RuntimeError("transient")
            return await super().snapshot(compact)

    driver = _Flaky()
    patched_jsdriver["driver"] = driver
    ai = _FakeAI(["1. try\n2. done", '{"actions": [{"action": "done", "text": "ok"}]}'])
    said = []
    assert await _session(ai, driver).run("t", on_step=said.append) == "ok"
    assert any("Snapshot failed" in m for m in said)


@pytest.mark.asyncio()
async def test_run_active_sessions_accounting(patched_jsdriver, fast_sleep) -> None:
    driver = _FakeDriver()
    patched_jsdriver["driver"] = driver
    ai = _FakeAI([], fail=True)
    before = agent_mod.ACTIVE_SESSIONS
    await _session(ai, driver).run("t")
    assert before == agent_mod.ACTIVE_SESSIONS  # decremented in finally


@pytest.mark.asyncio()
async def test_run_vision_attaches_shot(patched_jsdriver, fast_sleep) -> None:
    snap = dict(_SNAP, shot_b64="Zm9v")
    driver = _FakeDriver(snaps=[snap])
    patched_jsdriver["driver"] = driver
    seen = []

    class _SpyAI(_FakeAI):
        async def chat(self, messages, provider=None):
            seen.append(messages)
            return await super().chat(messages, provider)

    ai = _SpyAI(["1. look\n2. finish", '{"actions": [{"action": "done", "text": "v"}]}'])
    sess = AgentSession(ai, MagicMock(), max_steps=2, use_cdp=False, vision=True)
    assert await sess.run("t") == "v"
    content = seen[1][1]["content"]  # step call's user message
    assert isinstance(content, list)
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


# ── _make_plan / _prompt ─────────────────────────────────────────────


@pytest.mark.asyncio()
async def test_make_plan_truncates_to_six_lines() -> None:
    lines = "\n".join(f"{i}. step number {i} with extra words here" for i in range(1, 11))
    ai = _FakeAI([lines])
    sess = _session(ai, None)
    plan = await sess._make_plan("task")
    assert plan.count("\n") == 5
    assert len(plan) <= 600


@pytest.mark.asyncio()
async def test_make_plan_failure_returns_empty() -> None:
    ai = _FakeAI([], fail=True)
    assert await _session(ai, None)._make_plan("task") == ""


def test_prompt_includes_all_sections() -> None:
    sess = _session(_FakeAI([]), None)
    text = sess._prompt(
        "do the thing",
        dict(_SNAP, more_below=True, dialog="alert!"),
        history=["click(0) -> ok"],
        plan="1. open\n2. click",
        stuck=2,
    )
    assert "TASK: do the thing" in text
    assert "URL: https://x.com" in text
    assert "PLAN:" in text
    assert "WARNING: the page has not changed" in text
    assert "PAGE POPUP DIALOG" in text
    assert "More content below the fold" in text
    assert "PREVIOUS STEPS:" in text
    assert "click(0) -> ok" in text


def test_prompt_minimal_sections() -> None:
    sess = _session(_FakeAI([]), None)
    text = sess._prompt("t", dict(_SNAP), [])
    assert "PLAN:" not in text and "WARNING" not in text
    assert "PAGE POPUP DIALOG" not in text
