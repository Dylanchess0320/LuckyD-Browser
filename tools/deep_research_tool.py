"""
DeepResearch tool — LuckyD-native wrapper around the vendored deep-research swarm.

Runs a multi-agent research pipeline (planner -> parallel grounded workers ->
citation-grounded synthesizer -> critic -> claim-level verifier -> finalizer)
and returns a citation-backed markdown report plus artifact paths.

Backends (automatic):
- Gemini native grounding when LuckyD resolves to provider=google + key.
- Tavily / Brave premium search when TAVILY_API_KEY / BRAVE_API_KEY is set
  (or ``search_backend`` selects them explicitly).
- Otherwise LuckyD multi-provider (Ollama local free, DeepSeek, OpenAI,
  OpenRouter, ...) + keyless DDG search + full-page fetch/read.
- dry_run=True uses the offline mock provider (no key, no network).

Artifacts per run land under data/deep_research/runs/<timestamp>/:
plan.json, evidence.json, report.json, report.md, critique_iter*.json,
citation_audit.json, events.log
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from .base import ToolBase, ToolOutput
from .registry import register_tool

# Depth presets: (research_rounds, searches/round, urls/round,
# passages/worker, critic iterations, parallel workers)
DEPTH_PRESETS: dict[str, dict[str, int]] = {
    "quick": {
        "research_rounds": 1,
        "searches_per_round": 2,
        "max_urls_per_round": 3,
        "max_passages_per_worker": 6,
        "max_iterations": 1,
        "max_parallel": 2,
    },
    "standard": {
        "research_rounds": 3,
        "searches_per_round": 3,
        "max_urls_per_round": 5,
        "max_passages_per_worker": 12,
        "max_iterations": 2,
        "max_parallel": 4,
    },
    "deep": {
        "research_rounds": 4,
        "searches_per_round": 4,
        "max_urls_per_round": 6,
        "max_passages_per_worker": 16,
        "max_iterations": 3,
        "max_parallel": 4,
    },
    "max": {
        "research_rounds": 5,
        "searches_per_round": 5,
        "max_urls_per_round": 8,
        "max_passages_per_worker": 20,
        "max_iterations": 3,
        "max_parallel": 6,
    },
}

_VALID_BACKENDS = ("auto", "gemini", "ddg", "tavily", "brave", "none")
_VALID_PROVIDERS = ("auto", "gemini", "luckyd", "opencode", "openrouter", "ollama", "mock")


class DeepResearchTool(ToolBase):
    name = "DeepResearch"
    description = (
        "Run a multi-agent deep-research swarm on a question and get back a "
        "citation-backed markdown report with real source quotes. Use for "
        "open-ended research, comparisons, literature scans, or any question "
        "needing grounded web sources. Set depth=quick|standard|deep|max to "
        "control thoroughness. Returns markdown + source list + artifact paths."
    )
    aliases = ["Research", "DeepResearchSwarm", "SwarmResearch"]
    permission_level = "NORMAL"
    timeout_sec = 900.0
    parameters = {
        "query": {
            "type": "string",
            "description": "The research question to investigate.",
            "required": True,
        },
        "depth": {
            "type": "string",
            "description": "quick (1 round), standard (default), deep, or max.",
        },
        "context": {
            "type": "string",
            "description": "Extra background (e.g. current page text) to focus the research.",
        },
        "max_sources": {
            "type": "integer",
            "description": "Cap on evidence passages kept per worker (default 12).",
        },
        "max_iterations": {
            "type": "integer",
            "description": "Max critic revise rounds before finalizing (default 2).",
        },
        "max_parallel": {
            "type": "integer",
            "description": "Max parallel research workers (default 4).",
        },
        "search_backend": {
            "type": "string",
            "description": "auto (default), gemini, ddg, tavily, brave, or none.",
        },
        "max_seconds": {
            "type": "number",
            "description": "Wall-clock budget for the run, 0 = unlimited.",
        },
        "provider": {
            "type": "string",
            "description": "auto (default: free OpenCode Zen pool), gemini, luckyd, opencode, openrouter, ollama, or mock.",
        },
        "dry_run": {
            "type": "boolean",
            "description": "True = offline mock run, no key/network needed.",
        },
    }

    async def execute(
        self,
        query: str = "",
        depth: str | None = None,
        context: str = "",
        max_sources: int | None = None,
        max_iterations: int | None = None,
        max_parallel: int | None = None,
        search_backend: str | None = None,
        max_seconds: float | None = None,
        provider: str | None = None,
        dry_run: bool = False,
    ) -> ToolOutput:
        query = (query or "").strip()
        if not query:
            return ToolOutput(text="Provide a research question in 'query'.", error=True)
        if len(query) > 2000:
            return ToolOutput(text="Query too long (max 2000 chars).", error=True)

        try:
            from features.deep_research.config import settings as drs_settings
        except Exception as e:
            return ToolOutput(
                text=f"DeepResearch unavailable (config import failed: {e})",
                error=True,
            )

        # Per-run overrides with restore in finally.
        saved = (
            drs_settings.research_rounds,
            drs_settings.searches_per_round,
            drs_settings.max_urls_per_round,
            drs_settings.max_passages_per_worker,
            drs_settings.max_iterations,
            drs_settings.max_parallel,
            drs_settings.search_backend,
            drs_settings.max_seconds,
            drs_settings.provider,
        )
        try:
            if depth:
                preset = DEPTH_PRESETS.get(str(depth).lower().strip())
                if preset is None:
                    return ToolOutput(
                        text=f"Unknown depth {depth!r}. Use quick, standard, deep, or max.",
                        error=True,
                    )
                drs_settings.research_rounds = preset["research_rounds"]
                drs_settings.searches_per_round = preset["searches_per_round"]
                drs_settings.max_urls_per_round = preset["max_urls_per_round"]
                drs_settings.max_passages_per_worker = preset["max_passages_per_worker"]
                drs_settings.max_iterations = preset["max_iterations"]
                drs_settings.max_parallel = preset["max_parallel"]
            if max_sources is not None:
                drs_settings.max_passages_per_worker = max(1, min(int(max_sources), 40))
            if max_iterations is not None:
                drs_settings.max_iterations = max(1, min(int(max_iterations), 5))
            if max_parallel is not None:
                drs_settings.max_parallel = max(1, min(int(max_parallel), 8))
            if search_backend:
                sb = str(search_backend).lower().strip()
                if sb not in _VALID_BACKENDS:
                    return ToolOutput(
                        text=f"Unknown search_backend {sb!r}. Use {', '.join(_VALID_BACKENDS)}.",
                        error=True,
                    )
                drs_settings.search_backend = sb
            if max_seconds is not None:
                drs_settings.max_seconds = max(0.0, float(max_seconds))
            if provider:
                p = str(provider).lower().strip()
                if p not in _VALID_PROVIDERS:
                    return ToolOutput(
                        text=f"Unknown provider {p!r}. Use {', '.join(_VALID_PROVIDERS)}.",
                        error=True,
                    )
                drs_settings.provider = p
            if dry_run:
                drs_settings.provider = "mock"
        except Exception:
            pass

        effective_query = query
        extra = (context or "").strip()
        if extra:
            effective_query = query + "\n\nAdditional context:\n" + extra[:2000]

        runs_before = self._snapshot_runs(drs_settings.runs_dir)
        try:
            markdown = await self._run_swarm(effective_query, dry_run=bool(dry_run))
        except Exception as e:
            self._restore(drs_settings, saved)
            return ToolOutput(text=f"DeepResearch failed: {e}", error=True)
        self._restore(drs_settings, saved)

        if not markdown or not markdown.strip():
            return ToolOutput(
                text="DeepResearch produced no report (empty result).",
                error=True,
            )

        run_dir = self._newest_run(drs_settings.runs_dir, runs_before)
        meta: dict = {
            "chars": len(markdown),
            "runs_dir": drs_settings.runs_dir,
        }
        if run_dir:
            meta["run_dir"] = str(run_dir)
            for fname in ("report.md", "evidence.json", "citation_audit.json", "plan.json"):
                p = run_dir / fname
                if p.exists():
                    meta[fname.replace(".", "_")] = str(p)
            # Source count from evidence.json (best-effort).
            try:
                import json as _json

                ev = _json.loads((run_dir / "evidence.json").read_text(encoding="utf-8"))
                if isinstance(ev, list):
                    meta["sources"] = len(ev)
                    meta["source_urls"] = [e.get("url", "") for e in ev[:20]]
            except Exception:
                pass

        text = markdown
        if len(text) > 12000:
            run_hint = f"\n\nFull report saved at: {run_dir / 'report.md'}" if run_dir else ""
            text = text[:12000] + f"\n\n... [truncated {len(markdown) - 12000} chars]{run_hint}"
        title = f"Deep Research: {query[:80]}"
        return ToolOutput(text=text, title=title, metadata=meta)

    # -- internals ------------------------------------------------------
    @staticmethod
    def _restore(drs_settings, saved) -> None:
        with contextlib.suppress(Exception):
            (
                drs_settings.research_rounds,
                drs_settings.searches_per_round,
                drs_settings.max_urls_per_round,
                drs_settings.max_passages_per_worker,
                drs_settings.max_iterations,
                drs_settings.max_parallel,
                drs_settings.search_backend,
                drs_settings.max_seconds,
                drs_settings.provider,
            ) = saved

    @staticmethod
    def _snapshot_runs(runs_dir: str) -> set[str]:
        try:
            base = Path(runs_dir)
            if not base.exists():
                return set()
            return {p.name for p in base.iterdir() if p.is_dir()}
        except Exception:
            return set()

    @staticmethod
    def _newest_run(runs_dir: str, before: set[str]):
        try:
            base = Path(runs_dir)
            candidates = [p for p in base.iterdir() if p.is_dir() and p.name not in before]
            if not candidates:
                # Fall back to the newest dir overall.
                candidates = [p for p in base.iterdir() if p.is_dir()]
            if not candidates:
                return None
            return max(candidates, key=lambda p: p.stat().st_mtime)
        except Exception:
            return None

    @staticmethod
    async def _run_swarm(query: str, dry_run: bool = False) -> str:
        from features.deep_research.graph import run_swarm
        from features.deep_research.models.router import get_llm

        llm = get_llm(dry_run=dry_run)
        # run_swarm is async; await directly (workers offload blocking I/O
        # to threads internally).
        return await run_swarm(query, dry_run=dry_run, no_tui=True, llm=llm)


register_tool(DeepResearchTool())
