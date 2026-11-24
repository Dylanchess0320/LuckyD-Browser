"""
Configuration — paths and provider routing.

Single source of truth:
  config.py         → project paths + runtime settings
  core/providers.py → provider detection / API routing

Backward-compatible aliases (do NOT remove):
  MAX_OUTPUT_CHARS, COMMAND_TIMEOUT_SEC, PROJECT_DIR
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from core.providers import resolve_provider_config


def _project_dir() -> Path:
    """Working root. In a frozen (PyInstaller) build, __file__ points inside the
    bundle, which is wrong for locating the user's live .env and runtime data.
    Use the exe's directory so a shipped app reads the .env sitting next to it."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).parent.resolve()


PROJECT_DIR = _project_dir()
BASE_ENV_FILE = PROJECT_DIR / ".env"
# Per-user credentials (OpenRouter and other keys).  A frozen install's
# bundled .env is a clean template that every upgrade overwrites, so real
# keys live in %LOCALAPPDATA%/LuckyDBrowser/.env and survive reinstalls.  The
# browser's AI bridge reads the same file.
_local_appdata = (os.environ.get("LOCALAPPDATA", "") or "").strip()
USER_ENV_FILE = (
    Path(_local_appdata) / "LuckyDBrowser" / ".env"
    if _local_appdata
    else Path.home() / "AppData" / "Local" / "LuckyDBrowser" / ".env"
)
# The browser gives its two terminal agents distinct slots.  A slot-specific
# overlay preserves each agent's live /model choice without changing the
# other agent or the shared provider credentials in .env.
_agent_slot = "".join(
    c for c in os.environ.get("LUCKYD_AGENT_SLOT", "") if c.isalnum() or c in "-_"
)
# Writable credential/selection file.  A frozen install must persist /model
# choices to the per-user file (the bundled _internal/.env is a template that
# upgrades overwrite).  Slot overlays stay per-agent in the same directory.
_env_dir = USER_ENV_FILE.parent if getattr(sys, "frozen", False) else PROJECT_DIR
if _agent_slot:
    ENV_FILE = _env_dir / f".luckyd-agent-{_agent_slot}.env"
elif getattr(sys, "frozen", False):
    ENV_FILE = USER_ENV_FILE
else:
    ENV_FILE = BASE_ENV_FILE

# Runtime data lives under data/ so the repo root stays clean
DATA_DIR = PROJECT_DIR / "data"
MEMORY_DIR = DATA_DIR / "memory_store"
TASKS_DIR = DATA_DIR / "tasks"
WORKSPACE_DIR = DATA_DIR / "workspace"
CHECKPOINTS_DIR = DATA_DIR / "checkpoints"
SKILLS_DIR = PROJECT_DIR / "skills"
ASSETS_DIR = PROJECT_DIR / "assets"

for d in [DATA_DIR, MEMORY_DIR, TASKS_DIR, WORKSPACE_DIR, CHECKPOINTS_DIR, SKILLS_DIR]:
    d.mkdir(parents=True, exist_ok=True)


def load_env() -> None:
    """Load .env -- .env values override any stale pre-existing env vars.
    Uses utf-8-sig to safely strip a leading BOM.
    """
    # Shared credentials/config first, then this terminal agent's model
    # overlay. Both source checkouts and frozen installs fall back to the
    # per-user credentials file (%LOCALAPPDATA%/LuckyDBrowser/.env).
    paths = [USER_ENV_FILE, BASE_ENV_FILE, ENV_FILE]
    for path in dict.fromkeys(paths):
        if not path.exists():
            continue
        raw = path.read_text(encoding="utf-8-sig")
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ[key.strip()] = val.strip().strip('"').strip("'")


load_env()

COMMAND_TIMEOUT_SEC = int(os.environ.get("CODING_AGENT_CMD_TIMEOUT", "60"))
MAX_OUTPUT_CHARS_CFG = int(os.environ.get("CODING_AGENT_MAX_OUTPUT", "4000"))


def get_config() -> dict:
    """Get configuration — delegates provider detection to core/providers.py."""
    provider_cfg = resolve_provider_config()

    return {
        **provider_cfg,
        "max_turns": int(os.environ.get("CODING_AGENT_MAX_TURNS", "30")),
        "temperature": float(os.environ.get("CODING_AGENT_TEMP", "0.0")),
        "max_tokens": int(os.environ.get("CODING_AGENT_MAX_TOKENS", "8192")),
        "timeout_sec": int(os.environ.get("CODING_AGENT_TIMEOUT", "120")),
        "max_output_chars": int(os.environ.get("CODING_AGENT_MAX_OUTPUT", "4000")),
        "command_timeout_sec": int(os.environ.get("CODING_AGENT_CMD_TIMEOUT", "60")),
    }


# Backward-compatible aliases
MAX_OUTPUT_CHARS = MAX_OUTPUT_CHARS_CFG
