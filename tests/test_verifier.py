"""A2: verifier-gated completion via the dormant ReflectionEngine.

``core/reflection.py`` shipped a complete score-and-improve loop with zero
product callers. Final answers can now pass through it (single
score/improve/rescore pass) when ``CODING_AGENT_VERIFY=1`` (or
``--verify``); the improved answer is adopted only when the engine
reports a real improvement, and any verification failure keeps the
original answer.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

# Intentionally fake API key for tests (not a real secret).
TEST_API_KEY = "test-key-not-a-secret"


def _scores_json(score: float) -> str:
    return json.dumps(
        {
            "scores": [
                {
                    "dimension": dim,
                    "score": score,
                    "feedback": "f",
                    "suggestion": "s",
                }
                for dim in ("correctness", "completeness", "clarity")
            ],
            "overall_assessment": "ok",
            "should_improve": score < 0.85,
        }
    )


class FakeLLMClient:
    def __init__(self, script):
        self._script = list(script)
        self.model = "fake-model"
        self.chat_nonstreaming = AsyncMock(return_value={"content": "{}"})

    async def chat_stream(self, *args, **kwargs):
        if not self._script:
            return {"content": "default final answer"}
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _make_agent(**kwargs):
    with patch("llm.ProviderRouter"):
        from core.agent_loop import CodingAgent

        ag = CodingAgent(api_key=TEST_API_KEY, model="test-model", **kwargs)

    async def _noop_extract(user_message):
        return None

    async def _noop_refresh(query):
        return False

    ag._extract_session_memories = _noop_extract
    ag._refresh_memory_context = _noop_refresh
    return ag


class TestVerifyGate:
    async def test_off_by_default(self, monkeypatch):
        monkeypatch.delenv("CODING_AGENT_VERIFY", raising=False)
        ag = _make_agent()
        assert ag.verify_completions is False
        ag.llm_client = FakeLLMClient([{"content": "done and complete."}])
        assert await ag.run("go", max_turns=2) == "done and complete."
        ag.llm_client.chat_nonstreaming.assert_not_called()

    async def test_adopts_improved_answer(self, monkeypatch):
        monkeypatch.setenv("CODING_AGENT_VERIFY", "1")
        ag = _make_agent()
        assert ag.verify_completions is True
        client = FakeLLMClient([{"content": "rough draft answer here."}])
        client.chat_nonstreaming = AsyncMock(
            side_effect=[
                {"content": _scores_json(0.5)},
                {"content": "BETTER ANSWER HERE."},
                {"content": _scores_json(0.95)},
            ]
        )
        ag.llm_client = client
        assert await ag.run("go", max_turns=3) == "BETTER ANSWER HERE."

    async def test_keeps_original_when_already_good(self, monkeypatch):
        monkeypatch.setenv("CODING_AGENT_VERIFY", "yes")
        ag = _make_agent()
        client = FakeLLMClient([{"content": "solid and finished."}])
        client.chat_nonstreaming = AsyncMock(return_value={"content": _scores_json(0.95)})
        ag.llm_client = client
        assert await ag.run("go", max_turns=3) == "solid and finished."
        assert client.chat_nonstreaming.await_count == 1

    async def test_skips_errors_and_empty(self, monkeypatch):
        monkeypatch.setenv("CODING_AGENT_VERIFY", "1")
        ag = _make_agent()
        client = FakeLLMClient([{"content": "done."}])
        client.chat_nonstreaming = AsyncMock(side_effect=AssertionError("called"))
        ag.llm_client = client
        assert (
            await ag._verify_final_answer("req", "[API Error: 500] down") == "[API Error: 500] down"
        )
        assert await ag._verify_final_answer("req", "   ") == "   "

    async def test_falls_back_on_verifier_failure(self, monkeypatch):
        monkeypatch.setenv("CODING_AGENT_VERIFY", "true")
        ag = _make_agent()
        client = FakeLLMClient([{"content": "original answer kept."}])
        client.chat_nonstreaming = AsyncMock(side_effect=RuntimeError("boom"))
        ag.llm_client = client
        assert await ag.run("go", max_turns=3) == "original answer kept."


class TestVerifyFlag:
    def test_verify_flag_sets_env(self, monkeypatch):
        import main

        monkeypatch.delenv("CODING_AGENT_VERIFY", raising=False)
        main._parse_agent_args(["--verify"], {"model": "m", "temperature": 0.0})
        import os

        assert os.environ["CODING_AGENT_VERIFY"] == "true"
