"""Centralized configuration for LuckyD Deep Research.

Modernized from the standalone deep-research-swarm:
- Real Gemini model defaults (gemini-2.5-flash), not fictional 3.8-flash.
- GOOGLE_API_KEY fallback in addition to GEMINI_API_KEY.
- DRS_PROVIDER: auto | gemini | luckyd | opencode | openrouter | ollama | mock.
  auto prefers the verified-free OpenCode Zen pool (nemotron-3-ultra-free),
  then OpenRouter :free, then local Ollama, then Gemini native grounding.
- DRS_SEARCH_BACKEND: auto | gemini | ddg | none — auto resolves from provider.
- Runs/cache default under LuckyD DATA_DIR (data/deep_research/...) when
  available, with env overrides (DRS_RUNS_DIR / DRS_CACHE_DIR).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass


def _luckyd_data_dir() -> Path | None:
    """Locate LuckyD's data dir without importing config (avoids cycles)."""
    try:
        import sys

        if getattr(sys, "frozen", False):
            root = Path(sys.executable).resolve().parent
        else:
            # features/deep_research/config.py -> repo root is parents[2]
            root = Path(__file__).resolve().parent.parent.parent
        data = root / "data"
        if data.exists():
            return data
    except Exception:
        pass
    return None


_DATA_DIR = _luckyd_data_dir()
_DEFAULT_RUNS = str((_DATA_DIR / "deep_research" / "runs") if _DATA_DIR else "runs")
_DEFAULT_CACHE = str((_DATA_DIR / "deep_research" / "cache") if _DATA_DIR else ".swarm_cache")


def _default_model() -> str:
    # Prefer LuckyD's GOOGLE_MODEL when set, else a real Flash default.
    return os.getenv("DRS_MODEL_WORKER") or os.getenv("GOOGLE_MODEL") or "gemini-2.5-flash"


@dataclass
class Settings:
    api_key: str | None = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or None

    # Provider selection: auto | gemini | luckyd | opencode | openrouter | ollama | mock
    # auto = verified-free OpenCode Zen pool first, then OpenRouter :free,
    # then local Ollama, then Gemini native grounding (when the LuckyD stack
    # resolves to provider=google with a key), else LuckyD multi-provider.
    provider: str = os.getenv("DRS_PROVIDER", "auto").lower()

    # Free-model defaults (verified live 2026-09-06). Per-role overrides via
    # DRS_MODEL_PLANNER / DRS_MODEL_WORKER / DRS_MODEL_SYNTHESIZER /
    # DRS_MODEL_CRITIC, backend-scoped via DRS_OPENCODE_MODEL /
    # DRS_OPENROUTER_MODEL / DRS_OLLAMA_MODEL.
    opencode_model: str = os.getenv("DRS_OPENCODE_MODEL", "nemotron-3-ultra-free")
    openrouter_model: str = os.getenv(
        "DRS_OPENROUTER_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free"
    )
    ollama_model: str = os.getenv("DRS_OLLAMA_MODEL", os.getenv("OLLAMA_MODEL", "llama3.2:3b"))
    opencode_base_url: str = os.getenv(
        "DRS_OPENCODE_BASE_URL", "https://opencode.ai/zen/v1"
    )
    openrouter_base_url: str = os.getenv(
        "DRS_OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
    )

    model_worker: str = os.getenv("DRS_MODEL_WORKER", _default_model())
    model_planner: str = os.getenv(
        "DRS_MODEL_PLANNER", os.getenv("GOOGLE_MODEL", "gemini-2.5-flash")
    )
    model_synthesizer: str = os.getenv(
        "DRS_MODEL_SYNTHESIZER", os.getenv("GOOGLE_MODEL", "gemini-2.5-flash")
    )
    model_critic: str = os.getenv("DRS_MODEL_CRITIC", os.getenv("GOOGLE_MODEL", "gemini-2.5-flash"))

    max_iterations: int = int(os.getenv("DRS_MAX_ITERATIONS", "2"))
    max_parallel: int = int(os.getenv("DRS_MAX_PARALLEL", "4"))
    temp_reasoning: float = float(os.getenv("DRS_TEMP_REASONING", "0.4"))
    temp_extract: float = float(os.getenv("DRS_TEMP_EXTRACT", "0.1"))

    # Iterative deep-read worker loop controls.
    research_rounds: int = int(os.getenv("DRS_RESEARCH_ROUNDS", "3"))
    searches_per_round: int = int(os.getenv("DRS_SEARCHES_PER_ROUND", "3"))
    max_urls_per_round: int = int(os.getenv("DRS_MAX_URLS_PER_ROUND", "5"))
    max_passages_per_worker: int = int(os.getenv("DRS_MAX_PASSAGES_PER_WORKER", "12"))
    min_coverage: float = float(os.getenv("DRS_MIN_COVERAGE", "0.82"))
    fetch_timeout: float = float(os.getenv("DRS_FETCH_TIMEOUT", "10"))

    # auto = gemini grounding when provider is google+key, else ddg.
    search_backend: str = os.getenv("DRS_SEARCH_BACKEND", "auto").lower()

    runs_dir: str = os.getenv("DRS_RUNS_DIR", _DEFAULT_RUNS)

    # Cache + budget controls.
    cache_enabled: bool = os.getenv("DRS_CACHE_ENABLED", "true").lower() == "true"
    cache_ttl_days: int = int(os.getenv("DRS_CACHE_TTL_DAYS", "14"))
    cache_dir: str = os.getenv("DRS_CACHE_DIR", _DEFAULT_CACHE)

    max_search_queries: int = int(os.getenv("DRS_MAX_SEARCH_QUERIES", "0"))
    max_llm_calls: int = int(os.getenv("DRS_MAX_LLM_CALLS", "0"))
    max_fetches: int = int(os.getenv("DRS_MAX_FETCHES", "0"))
    max_seconds: float = float(os.getenv("DRS_MAX_SECONDS", "0"))

    def model_for(self, role: str) -> str:
        return {
            "worker": self.model_worker,
            "planner": self.model_planner,
            "synthesizer": self.model_synthesizer,
            "critic": self.model_critic,
        }.get(role, self.model_worker)

    def temp_for(self, role: str) -> float:
        if role in ("planner", "synthesizer"):
            return self.temp_reasoning
        return self.temp_extract

    def effective_search_backend(self, provider_name: str = "") -> str:
        """Resolve 'auto' to a concrete backend."""
        if self.search_backend != "auto":
            return self.search_backend
        # Mock runs must stay offline: the mock's grounded() path returns
        # deterministic cards with no network.
        if provider_name == "mock":
            return "gemini"
        if provider_name == "gemini" and self.api_key:
            return "gemini"
        # Free-model + LuckyD multi-provider runs use keyless DDG + fetch.
        return "ddg"

    def refresh(self) -> None:
        """Re-read env overrides (used by the LuckyD tool per-run)."""
        self.provider = os.getenv("DRS_PROVIDER", self.provider).lower()
        self.search_backend = os.getenv("DRS_SEARCH_BACKEND", self.search_backend).lower()
        if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
            self.api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


settings = Settings()
