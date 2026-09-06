"""Deprecated standalone CLI (LuckyD-only going forward).

Canonical entrypoint is the LuckyD ``DeepResearch`` tool
(``tools/deep_research_tool.py``). This module remains for debugging /
programmatic use but the ``swarm`` console script is no longer installed.

Usage (debugging only):
  python -m features.deep_research.cli "your research question" --dry-run
"""

from __future__ import annotations

import asyncio
import sys

import click

from .config import settings
from .graph import run_swarm


class _DefaultGroup(click.Group):
    """A group that runs a default subcommand when the first arg isn't a command."""

    def __init__(self, *args, default=None, **kwargs):
        self.default_cmd = default
        super().__init__(*args, **kwargs)

    def parse_args(self, ctx, args):
        if args and args[0] not in self.commands and not args[0].startswith("-"):
            args = [self.default_cmd, *args]
        return super().parse_args(ctx, args)


@click.group(cls=_DefaultGroup, default="research")
@click.version_option(package_name="deep-research-swarm")
def main() -> None:
    """Deep Research Swarm — a multi-agent, citation-grounded research engine."""


@main.command()
@click.argument("query")
@click.option("--dry-run", is_flag=True, help="Run with a mock LLM; no API key needed.")
@click.option("--no-tui", is_flag=True, help="Disable the live TUI; print plain logs.")
@click.option("--no-cache", is_flag=True, help="Disable the SQLite cache for this run.")
@click.option("--max-iterations", type=int, default=None, help="Override DRS_MAX_ITERATIONS.")
@click.option("--max-seconds", type=float, default=None, help="Override DRS_MAX_SECONDS budget.")
@click.option(
    "--search",
    type=click.Choice(["gemini", "ddg", "none"]),
    default=None,
    help="Override search backend.",
)
def research(query, dry_run, no_tui, no_cache, max_iterations, max_seconds, search):
    """Run the swarm on a research QUESTION."""
    if max_iterations is not None:
        settings.max_iterations = max_iterations
    if max_seconds is not None:
        settings.max_seconds = max_seconds
    if search is not None:
        settings.search_backend = search
    if no_cache:
        settings.cache_enabled = False

    if dry_run:
        click.secho("Running in DRY-RUN mode (mock LLM, no real search).", fg="yellow")

    try:
        md = asyncio.run(run_swarm(query, dry_run=dry_run, no_tui=no_tui))
    except KeyboardInterrupt:
        click.secho("\nInterrupted.", fg="red")
        sys.exit(1)

    if md:
        click.echo("\n" + "=" * 70)
        click.echo(md)
        click.echo("=" * 70)
        click.secho(f"\nDone. Artifacts saved under {settings.runs_dir}/", fg="green")


@main.command()
def config():
    """Show the effective configuration."""
    click.echo(f"GEMINI_API_KEY set : {'yes' if settings.api_key else 'no'}")
    click.echo(f"worker model         : {settings.model_worker}")
    click.echo(f"planner model        : {settings.model_planner}")
    click.echo(f"synthesizer model    : {settings.model_synthesizer}")
    click.echo(f"critic model         : {settings.model_critic}")
    click.echo(f"search backend       : {settings.search_backend}")
    click.echo(f"max iterations       : {settings.max_iterations}")
    click.echo(f"max parallel workers : {settings.max_parallel}")
    click.echo(f"cache enabled        : {settings.cache_enabled} (ttl {settings.cache_ttl_days}d)")
    click.echo(
        f"budget limits        : llm={settings.max_llm_calls or 'unlimited'} "
        f"search={settings.max_search_queries or 'unlimited'} "
        f"fetch={settings.max_fetches or 'unlimited'} "
        f"secs={settings.max_seconds or 'unlimited'}"
    )


@main.command()
def doctor():
    """Verify the Gemini API works: structured call + grounded search + write artifact."""
    if not settings.api_key:
        click.secho("No GEMINI_API_KEY set. Use --dry-run for offline testing.", fg="red")
        sys.exit(1)
    from .models.gemini import GeminiProvider
    from .schemas import EvidenceCard, ResearchPlan

    prov = GeminiProvider()
    click.echo("1/3 structured planner call ... ", nl=False)
    plan = prov.structured(
        role="planner",
        system="You decompose research questions.",
        user="Decompose: what is rust?",
        schema=ResearchPlan,
        temperature=0.2,
    )
    click.secho(f"OK ({len(plan.tasks)} tasks)", fg="green")
    click.echo("2/3 grounded search call ... ", nl=False)
    _text, ev = prov.grounded(
        role="worker", system="Answer with grounded citations.", user="What is rust?"
    )
    if ev:
        click.secho(f"OK ({len(ev)} sources, e.g. {ev[0].url[:50]})", fg="green")
    else:
        click.secho("WARN (no grounding chunks returned)", fg="yellow")
    click.echo("3/3 artifact write ... ", nl=False)
    from .runtime.run_store import RunStore

    store = RunStore("doctor")
    store.save_evidence(ev or [EvidenceCard(id="e0", url="https://example.com", title="test")])
    store.close()
    click.secho("OK", fg="green")
    click.secho("\nAll checks passed.", fg="green")


@main.command()
@click.option("--dry-run", is_flag=True, help="Eval with mock LLM (sanity check).")
def eval(dry_run):
    """Run the built-in benchmark harness."""
    from .evals.harness import run_evals

    asyncio.run(run_evals(dry_run=dry_run))


@main.group()
def cache():
    """Inspect or clear the local SQLite cache."""


@cache.command("stats")
def cache_stats():
    """Show cache hit/size statistics."""
    from .tools.cache import get_cache

    stats = get_cache().stats()
    if not stats.get("enabled"):
        click.secho("Cache is disabled (DRS_CACHE_ENABLED=false).", fg="yellow")
        return
    click.echo(f"Cache dir   : {settings.cache_dir}")
    click.echo(f"Documents   : {stats.get('documents', 0)}")
    click.echo(f"Search results: {stats.get('search_results', 0)}")
    click.echo(f"TTL (days)  : {settings.cache_ttl_days}")


@cache.command("clear")
def cache_clear():
    """Clear all cached documents and search results."""
    from .tools.cache import get_cache

    get_cache().clear()
    click.secho("Cache cleared.", fg="green")


if __name__ == "__main__":
    main()
