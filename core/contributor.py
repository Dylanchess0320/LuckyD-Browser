"""Contributor tier — explicit opt-in for training-data-discounted models.

Some gateways sell a much cheaper "contributor" class where the provider may
use your prompts and outputs to train its models. LuckyD never enables this
silently: the tier is off unless the user explicitly turns it on
(``/contributor on`` in the REPL, or ``LUCKYD_CONTRIBUTOR_TIER=1``), and the
one-line warning is shown at the moment of opting in and whenever the tier is
listed as active.
"""

from __future__ import annotations

import os
from pathlib import Path

# Env flag that gates every contributor-tier model.
CONTRIBUTOR_ENV = "LUCKYD_CONTRIBUTOR_TIER"

# Canonical contributor model id (muse-spark on the Cline gateway; the model
# itself predates the 9.8 OpenCode Zen retirement, pricing unchanged).
CONTRIBUTOR_MODEL = "muse-spark-1.3-contributor"

# The one-line warning shown on opt-in and while the tier is active.
CONTRIBUTOR_WARNING = (
    "Contributor tier: this model is cheaper because the provider may use your "
    "prompts and outputs as training data."
)

# (model_id, input $/1M, output $/1M, cached-input $/1M, tier)
CONTRIBUTOR_MODELS: dict[str, dict[str, object]] = {
    "muse-spark-1.3": {
        "input": 1.25,
        "output": 4.25,
        "cached_input": 0.15,
        "contributor": False,
    },
    CONTRIBUTOR_MODEL: {
        "input": 0.10,
        "output": 0.20,
        "cached_input": 0.002,
        "contributor": True,
    },
}


def contributor_enabled() -> bool:
    """True only when the user explicitly opted in (never a silent default)."""
    return (os.environ.get(CONTRIBUTOR_ENV, "") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def contributor_status() -> dict[str, object]:
    """Small JSON-shaped status for the UI/tests."""
    return {
        "enabled": contributor_enabled(),
        "env": CONTRIBUTOR_ENV,
        "model": CONTRIBUTOR_MODEL,
        "warning": CONTRIBUTOR_WARNING,
        "pricing": CONTRIBUTOR_MODELS[CONTRIBUTOR_MODEL],
    }


def contributor_models_enabled_only(models: list[str]) -> list[str]:
    """Drop contributor-tier models from a list unless the tier is enabled."""
    if contributor_enabled():
        return list(models)
    return [m for m in models if not is_contributor_model(m)]


def is_contributor_model(model: str) -> bool:
    """True for contributor-tier ids (suffix match keeps aliases working)."""
    m = (model or "").strip().lower()
    if not m:
        return False
    if m == CONTRIBUTOR_MODEL:
        return True
    # "muse-spark-1.3-contributor-free" (gateway promo) counts too.
    return m.startswith("muse-spark") and "contributor" in m


def set_contributor_enabled(enabled: bool, persist: bool = True) -> None:
    """Turn the contributor tier on/off; optionally persist to ``.env``.

    Never called implicitly — every caller is a user-facing toggle.
    """
    os.environ[CONTRIBUTOR_ENV] = "1" if enabled else "0"
    if not persist:
        return
    try:
        from config import ENV_FILE

        _write_env(ENV_FILE, CONTRIBUTOR_ENV, "1" if enabled else "0")
    except Exception:
        pass  # session-only toggle still works


def _write_env(env_file: Path, key: str, value: str) -> None:
    lines = env_file.read_text(encoding="utf-8-sig").splitlines() if env_file.exists() else []
    prefix = f"{key}="
    found = False
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(prefix) and not stripped.startswith("#"):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        if out and out[-1].strip():
            out.append("")
        out.append(f"{key}={value}")
    env_file.write_text("\n".join(out) + "\n", encoding="utf-8")
