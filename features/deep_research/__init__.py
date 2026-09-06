"""LuckyD Deep Research — vendored + modernized from deep-research-swarm.

Pipeline: planner -> parallel grounded research workers -> citation-grounded
synthesizer -> critic loop -> verifier -> finalizer.

Backends:
- ``gemini``: Google Gemini with Search grounding (needs GEMINI_API_KEY or
  GOOGLE_API_KEY).
- ``luckyd``: any LuckyD-configured provider (Ollama local, DeepSeek, OpenAI,
  OpenRouter, ...) via OpenAI-compatible chat completions + DDG search.
- ``mock``: offline deterministic provider for tests.
"""

from .config import settings
from .graph import build_graph, run_swarm

__version__ = "1.0.0"
__all__ = ["build_graph", "run_swarm", "settings"]
