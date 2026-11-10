"""Live free-model detection (10.8.8): probe parsing, ranking, guardrails.

The detector re-probes the free gateways at startup (at most daily) and
rebuilds the free rotation from what is ACTUALLY being served. A listing
is never proof — every probe is mocked here at the HTTP seam; the live
endpoints are OpenRouter :free (price $0), the Cline gateway (existence
only — never auto-admission), Zen (paid freshness only), the `opencode
models` CLI roster (in-app-only), and the RankLLMs live benchmark feed
(no key; AA-derived scores).

Guardrails pinned here: the default never changes (ollama/luckyd:8b
leads every rotation), only $0/no-card routes are ever auto-selected,
`opencode/*-free` ids never enter the HTTP rotation (they 403 on direct
API calls), MuseCode has no free tier, and the Antigravity Claude roster
expires 2026-11-18.
"""

from __future__ import annotations

import json
import time

import pytest

from core.free_detection import (
    ANTIGRAVITY_CLAUDE_REMOVED,
    DEFAULT_PAIR,
    FREE_BENCHMARK_SNAPSHOT,
    SNAPSHOT_DATE,
    DetectionResult,
    antigravity_free_today,
    detect_free_models,
)


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_urlopen_factory(routes):
    """routes: {url_substring: payload-dict-or-None} -> urlopen stub."""
    import urllib.error

    def _urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        for needle, payload in routes.items():
            if needle in url:
                if payload is None:
                    raise urllib.error.URLError("down")
                return _FakeResp(payload)
        raise urllib.error.URLError(f"unexpected url {url}")

    return _urlopen


_OPENROUTER_PAYLOAD = {
    "data": [
        {
            "id": "nvidia/nemotron-3-ultra-550b-a55b:free",
            "pricing": {"prompt": "0", "completion": "0"},
        },
        {"id": "cohere/north-mini-code:free", "pricing": {"prompt": "0", "completion": "0"}},
        {
            "id": "deepseek/deepseek-v4.1-flash-1017:free",
            "pricing": {"prompt": "0", "completion": "0"},
        },
        {"id": "openai/gpt-6.1-sol", "pricing": {"prompt": "0.000002", "completion": "0.00001"}},
        {"id": "meta/muse-ember", "pricing": {"prompt": "0.0000001", "completion": "0.0000005"}},
        {"id": "openrouter/free", "pricing": {"prompt": "0", "completion": "0"}},
    ]
}

_RANKLLMS_PAYLOAD = {
    "rankings": [
        {
            "openrouter_id": "nvidia/nemotron-3-ultra-550b-a55b",
            "intelligence_index": 44.0,
            "coding_index": 46.0,
            "agentic_index": 45.0,
        },
        {
            "openrouter_id": "deepseek/deepseek-v4.1-flash-1017",
            "intelligence_index": 40.0,
            "coding_index": 48.0,
            "agentic_index": 42.0,
        },
        {
            "openrouter_id": "meta/muse-ember",
            "intelligence_index": 36.0,
            "coding_index": 38.0,
            "agentic_index": 35.0,
        },
    ]
}

_CLINE_PAYLOAD = {
    "data": [
        {"id": "meta/muse-ember"},
        {"id": "deepseek/deepseek-v4.1-flash"},
        {"id": "poolside/laguna-s-2.1:free"},
    ]
}


@pytest.fixture
def no_opencode_cli(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)


class TestOpenRouterProbe:
    def test_zero_price_free_ids_only(self, monkeypatch):
        from core import free_detection

        monkeypatch.setattr(
            "urllib.request.urlopen",
            _fake_urlopen_factory({"openrouter": _OPENROUTER_PAYLOAD}),
        )
        free_ids = free_detection.probe_openrouter_free()
        # Only :free ids with exactly $0 pricing; the router id and the
        # paid ids are excluded even when priced near zero.
        assert free_ids == [
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "cohere/north-mini-code:free",
            "deepseek/deepseek-v4.1-flash-1017:free",
        ]

    def test_probe_failure_is_empty_not_fatal(self, monkeypatch):
        from core import free_detection

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen_factory({"openrouter": None}))
        assert free_detection.probe_openrouter_free() == []


class TestRankLLMs:
    def test_score_parsing(self, monkeypatch):
        from core import free_detection

        monkeypatch.setattr(
            "urllib.request.urlopen",
            _fake_urlopen_factory({"rankllms": _RANKLLMS_PAYLOAD}),
        )
        scores = free_detection.fetch_rankllms_scores()
        # Keys are normalized slugs (provider prefix stripped).
        assert scores["nemotron-3-ultra-550b-a55b"]["coding"] == 46.0
        assert scores["deepseek-v4.1-flash-1017"]["intelligence"] == 40.0
        assert "muse-ember" in scores  # paid models carry scores too

    def test_fetch_failure_returns_empty_and_snapshot_covers(self, monkeypatch):
        """A dead RankLLMs fetch returns {} — the Nov 10 snapshot is the
        fallback, consulted by _score_for, not by the fetch."""
        from core import free_detection

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen_factory({"rankllms": None}))
        assert free_detection.fetch_rankllms_scores() == {}
        assert SNAPSHOT_DATE == "2026-11-10"
        # Snapshot still scores the known free models (AA index only).
        coding, intel = free_detection._score_for("deepseek/deepseek-v4.1-flash-1017:free", {})
        assert (coding, intel) == (0.0, 32.0)


class TestScoring:
    def test_live_scores_beat_snapshot(self):
        from core import free_detection

        live = {"deepseek-v4.1-flash-1017": {"coding": 48.0, "intelligence": 40.0}}
        assert free_detection._score_for("deepseek/deepseek-v4.1-flash-1017:free", live) == (
            48.0,
            40.0,
        )

    def test_unscored_model_scores_zero(self):
        from core import free_detection

        assert free_detection._score_for("some/unknown-model:free", {}) == (0.0, 0.0)

    def test_snapshot_shape(self):
        # Every snapshot entry carries an AA intelligence index and an
        # LMArena coding Elo from the Nov 10, 2026 audit.
        assert SNAPSHOT_DATE == "2026-11-10"
        for slug, entry in FREE_BENCHMARK_SNAPSHOT.items():
            assert entry["aa"] > 0 and entry["lmarena"] > 0, slug


class TestAntigravityGate:
    def test_claude_present_before_removal(self):
        import datetime

        assert datetime.date(2026, 11, 18) == ANTIGRAVITY_CLAUDE_REMOVED
        assert "claude-opus-4.6" in antigravity_free_today(datetime.date(2026, 11, 17))

    def test_claude_gone_on_removal_day(self):
        import datetime

        models = antigravity_free_today(datetime.date(2026, 11, 18))
        assert not any("claude" in m for m in models)
        assert "gemini-3.8-flash" in models


class TestDetectFreeModels:
    def test_full_detection_with_mocked_network(self, monkeypatch, tmp_path, no_opencode_cli):

        monkeypatch.setenv("LUCKYD_FREE_MODELS_CACHE", str(tmp_path / "cache.json"))
        monkeypatch.setattr(
            "urllib.request.urlopen",
            _fake_urlopen_factory(
                {
                    "openrouter": _OPENROUTER_PAYLOAD,
                    "rankllms": _RANKLLMS_PAYLOAD,
                    "cline": _CLINE_PAYLOAD,
                }
            ),
        )
        result = detect_free_models()
        assert isinstance(result, DetectionResult)
        assert result.pairs[0] == DEFAULT_PAIR == ("ollama", "luckyd:8b")
        # Ranked by live coding score: deepseek (48) ahead of nemotron (46),
        # unscored north-mini-code last among the :free legs.
        assert result.pairs[1] == ("openrouter", "deepseek/deepseek-v4.1-flash-1017:free")
        assert result.pairs[2] == ("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free")
        # The Cline listing is NOT auto-admitted (existence is not a free
        # signal — free credits ending); cline-usage stays the last leg.
        assert result.pairs[-1] == ("cline-usage", "minimax/minimax-m2.5")
        assert result.live_benchmarks is True
        assert result.sources
        assert "MuseCode: no free tier" in result.report

    def test_offline_detection_still_returns_default(self, monkeypatch, tmp_path, no_opencode_cli):

        monkeypatch.setenv("LUCKYD_FREE_MODELS_CACHE", str(tmp_path / "cache.json"))
        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen_factory({}))
        result = detect_free_models()
        assert result.pairs[0] == ("ollama", "luckyd:8b")
        assert result.live_benchmarks is False
        assert "snapshot" in result.report

    def test_opencode_app_ids_never_enter_http_rotation(
        self, monkeypatch, tmp_path, no_opencode_cli
    ):
        """opencode/*-free ids 403 on direct API calls — they belong to the
        app-only list, never to the HTTP rotation pairs."""
        from core import free_detection

        monkeypatch.setenv("LUCKYD_FREE_MODELS_CACHE", str(tmp_path / "cache.json"))
        monkeypatch.setattr(
            "urllib.request.urlopen",
            _fake_urlopen_factory(
                {"openrouter": _OPENROUTER_PAYLOAD, "rankllms": _RANKLLMS_PAYLOAD}
            ),
        )
        monkeypatch.setattr(
            free_detection,
            "probe_opencode_app_free",
            lambda: ["opencode/deepseek-v4.1-flash-free", "opencode/glm-5.3-flash-free"],
        )
        result = detect_free_models()
        assert result.opencode_app_free == [
            "opencode/deepseek-v4.1-flash-free",
            "opencode/glm-5.3-flash-free",
        ]
        assert not any(m.startswith("opencode/") for _p, m in result.pairs)


class TestCacheRoundTrip:
    def test_refresh_writes_and_cached_detection_reads(
        self, monkeypatch, tmp_path, no_opencode_cli
    ):
        from core import free_detection

        cache = tmp_path / "free_models_cache.json"
        monkeypatch.setenv("LUCKYD_FREE_MODELS_CACHE", str(cache))
        monkeypatch.setattr(
            "urllib.request.urlopen",
            _fake_urlopen_factory(
                {"openrouter": _OPENROUTER_PAYLOAD, "rankllms": _RANKLLMS_PAYLOAD}
            ),
        )
        result = free_detection.refresh_free_detection()
        assert cache.exists()
        cached = free_detection.cached_detection()
        assert cached is not None
        assert cached.pairs == result.pairs
        assert cached.live_benchmarks is True

    def test_stale_cache_returns_none(self, monkeypatch, tmp_path):
        from core import free_detection

        cache = tmp_path / "free_models_cache.json"
        cache.write_text(
            json.dumps(
                {
                    "fetched_at_ts": time.time() - 25 * 3600,
                    "pairs": [["gemini", "gemini-2.5-flash"]],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("LUCKYD_FREE_MODELS_CACHE", str(cache))
        assert free_detection.cached_detection() is None


class TestFreeRotationReport:
    def test_report_names_from_to_and_why(self):
        from core.free_detection import free_rotation_report

        previous = [("ollama", "luckyd:8b"), ("gemini", "gemini-2.5-flash")]
        current = [
            ("ollama", "luckyd:8b"),
            ("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free"),
        ]
        result = DetectionResult(
            pairs=current,
            previous_pairs=previous,
            fetched_at="2026-11-10",
            live_benchmarks=True,
            sources=["openrouter", "rankllms"],
        )
        report = free_rotation_report(result)
        assert "gemini/gemini-2.5-flash" in report
        assert "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free" in report
        assert "CHANGED:" in report
        assert "benchmarks LIVE" in report

    def test_report_has_no_changed_line_when_unchanged(self):
        from core.free_detection import free_rotation_report

        pairs = [("ollama", "luckyd:8b")]
        result = DetectionResult(pairs=pairs, previous_pairs=list(pairs), fetched_at="2026-11-10")
        assert "CHANGED:" not in free_rotation_report(result)
