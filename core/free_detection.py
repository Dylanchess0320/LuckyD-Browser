"""Live free-model detection — the free tier, probed, not assumed (10.8.8).

Dylan, 2026-11-10: "whatever is free — OpenCode, Antigravity, MuseCode,
anything — the Browser should AUTO-DETECT it live at all times so it never
goes outdated and he never has to keep switching or updating us to find it.
And change the pick based on live benchmarks + my judgment of what's good."

What this module does
---------------------
At startup (and at most once a day after that) it re-probes the free
gateways and rebuilds the free list from what is ACTUALLY being served
right now — a listing is never proof on its own (precedent: the 10.8.3
live-probe audit, and the Oct 23 OpenCode free lane dying mid-task on a
usage cap). Dead / 404 / capped ids drop out automatically; new free ids
appear automatically. The free default / rotation then re-ranks from LIVE
benchmark data (RankLLMs' free JSON API, which republishes Artificial
Analysis intelligence / coding / agentic indices — no key needed) plus the
in-code snapshot below when the live fetch fails.

Probes (all best-effort, never raise, short timeouts):
  * OpenRouter ``GET /api/v1/models`` (public, no key) — ``:free`` ids whose
    prompt+completion price is exactly $0. A listed ``:free`` id is
    "listed-free": serving it still needs an OpenRouter key and the free
    per-request limits apply (20 req/min, 50/day under $10 credits).
  * Cline gateway ``GET /api/v1/models`` (public, no key) — confirms which
    curated free ids are still listed. Cline is NOT admitted to the auto
    free list: its free credits are ending, so a listed id is not a usable
    $0 route any more. The result is reported, not trusted.
  * OpenCode Zen ``GET /zen/v1/models`` (public, no key) — paid-catalog
    freshness only. Zen has no free tier (keyed since 2026-09).
  * ``opencode models`` (local CLI, only when the binary exists) — the
    OpenCode app's own free roster (``opencode/*-free`` ids). These work
    ONLY inside the OpenCode app / LuckyD Code; a direct API call with one
    403s (FreeTierError). They feed the LuckyD Code default and the
    switchable-options list, never the HTTP rotation.
  * Google AI Studio free tier — keyed (GOOGLE_API_KEY), free by plan, not
    per-model, so there is nothing to probe per model; recorded as a known
    $0 route when the key is present.

Registry (verified 2026-11-10, NOT auto-selected — no API route exists):
  * Antigravity — free tier is Gemini 3.8 Flash + Claude Sonnet/Opus 4.6
    and GPT-OSS-120b ONLY until 2026-11-18, then Claude is removed and
    free is Gemini 3.8 Flash alone. IDE/CLI app, no API. The expiry is
    date-gated in code: after 2026-11-18 the Claude entries drop out.
  * MuseCode — VERIFIED NO FREE TIER (Meta's own pages, read Oct 14,
    2026): no free plan, no trial, no credits. The CLI binary is free;
    Muse Spark tokens are billed ($1.25/$4.25 standard, $0.10/$0.20
    contributor — contributor is subscribers-only). Excluded, with reason.
  * DeepSeek chat / Kimi chat — free in their own apps, no free API tier.
    The APIs are pay-per-token; excluded from auto-detection.

Guardrails (hard — see also ``core/free_rotation.py``):
  * The DEFAULT is always Dylan's own model: ``("ollama", "luckyd:8b")``.
    Detection governs the cloud FREE fallback / rotation and the
    switchable options only; it never changes the default.
  * Only $0 / no-card routes are ever auto-selected. Paid models stay in
    the catalog as switchable options and are NEVER auto-picked.
  * Never swap the ACTIVE model mid-conversation: detection refreshes the
    CACHED list (at startup, at most daily); the rotator snapshots its
    list when it starts and only changes pick on failure / cap-out.
  * Every automatic change is visible: the detection result carries a
    human-readable report (from -> to + why: benchmark + price) and the
    cache records the previous list so a change is always diffable.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

__all__ = [
    "ANTIGRAVITY_CLAUDE_REMOVED",
    "AUTO_FREE_PROVIDERS",
    "DEFAULT_PAIR",
    "DetectionResult",
    "cached_detection",
    "detect_free_models",
    "ensure_fresh_detection",
    "free_rotation_report",
    "refresh_free_detection",
]

# ── Guardrail constants ────────────────────────────────────────────────

#: The default never changes: Dylan's own model, local Ollama.
DEFAULT_PAIR: tuple[str, str] = ("ollama", "luckyd:8b")

#: Providers whose $0 routes may enter the AUTO free rotation. Anything
#: else (Cline, Zen, Anthropic, OpenAI, …) is switchable-only, never auto.
AUTO_FREE_PROVIDERS = frozenset({"openrouter", "gemini", "groq", "ollama"})

#: Antigravity's free Claude dies on this date (Google's models page,
#: verified 2026-11-03/09). After it, free Antigravity = Gemini 3.8 Flash.
ANTIGRAVITY_CLAUDE_REMOVED: date = date(2026, 11, 18)

#: Re-check at most this often (startup + daily). The cache is the
#: single source the rotation reads; the network is never on its path.
CACHE_TTL_SECONDS = 24 * 60 * 60

_CACHE_ENV = "LUCKYD_FREE_MODELS_CACHE"
_CACHE_DIRNAME = ".luckyd"
_CACHE_FILENAME = "free_models_cache.json"

_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_CLINE_MODELS_URL = "https://api.cline.bot/api/v1/models"
_ZEN_MODELS_URL = "https://opencode.ai/zen/v1/models"
# RankLLMs free JSON API (no key): AA intelligence / coding / agentic
# indices + prices + is_free, republished with attribution to AA.
_RANKLLMS_URL = "https://api.rankllms.com/api/v1/leaderboard?limit=200"

_HTTP_TIMEOUT = 8.0

# Benchmark snapshot — Artificial Analysis Intelligence Index v5 + LMArena
# coding Elo, verified 2026-11-10. Used ONLY when the live RankLLMs fetch
# fails; the detection result says so. Keys are normalized model slugs.
SNAPSHOT_DATE = "2026-11-10"
FREE_BENCHMARK_SNAPSHOT: dict[str, dict[str, float]] = {
    # model slug -> {"aa": Intelligence Index, "lmarena": coding Elo}
    "deepseek-v4.1-flash": {"aa": 32.0, "lmarena": 1525.0},
    "deepseek-v4.1-flash-1017": {"aa": 32.0, "lmarena": 1525.0},
    "glm-5.3-flash": {"aa": 40.0, "lmarena": 1500.0},
    "nemotron-3-ultra-550b-a55b": {"aa": 40.0, "lmarena": 1450.0},
    "nemotron-3.5-lightning": {"aa": 38.0, "lmarena": 1420.0},
    "gemini-2.5-flash": {"aa": 35.0, "lmarena": 1400.0},
    "gemini-3.8-flash": {"aa": 38.0, "lmarena": 1450.0},
    "groq/compound-mini": {"aa": 30.0, "lmarena": 1350.0},
    "muse-ember": {"aa": 36.0, "lmarena": 1400.0},
}

# OpenCode in-app free roster as last observed (Oct 19, 2026 test on
# OpenCode 1.19.3, no account). The roster ROTATES without notice — this
# is only the match set for the live `opencode models` probe, never an
# assertion that an id is free right now.
KNOWN_OPENCODE_APP_FREE = (
    "opencode/deepseek-v4.1-flash-free",
    "opencode/glm-5.3-flash-free",
    "opencode/beam-free",
    "opencode/mimo-v2.6-flash-free",
    "opencode/nemotron-3-ultra-free",
    "opencode/nemotron-3.5-lightning-free",
    "opencode/space-bunny-free",
)


@dataclass
class DetectionResult:
    """One detection run: ranked auto-free pairs + app-only free ids."""

    pairs: list[tuple[str, str]] = field(default_factory=list)
    opencode_app_free: list[str] = field(default_factory=list)
    antigravity_free: list[str] = field(default_factory=list)
    report: str = ""
    sources: list[str] = field(default_factory=list)
    fetched_at: str = ""
    live_benchmarks: bool = False
    previous_pairs: list[tuple[str, str]] = field(default_factory=list)
    # Live benchmark scores keyed by normalized model slug (empty when the
    # live fetch failed and the snapshot was used instead).
    scores: dict[str, dict[str, float]] = field(default_factory=dict)

    def changed_from_previous(self) -> bool:
        return self.previous_pairs != self.pairs


# ── Cache ──────────────────────────────────────────────────────────────


def cache_path() -> Path:
    override = (os.environ.get(_CACHE_ENV, "") or "").strip()
    if override:
        return Path(override)
    return Path.home() / _CACHE_DIRNAME / _CACHE_FILENAME


def _read_cache() -> dict | None:
    try:
        raw = cache_path().read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_cache(payload: dict) -> None:
    try:
        path = cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass  # cache is best-effort; detection still returns its result


def cached_detection() -> DetectionResult | None:
    """The last cached detection, or None when missing / stale / corrupt."""
    import time

    data = _read_cache()
    if not data:
        return None
    try:
        fetched_at = float(data.get("fetched_at_ts") or 0)
    except (TypeError, ValueError):
        return None
    if time.time() - fetched_at > CACHE_TTL_SECONDS:
        return None
    pairs = [
        (str(p), str(m))
        for p, m in (data.get("pairs") or [])
        if isinstance(p, str) and isinstance(m, str)
    ]
    return DetectionResult(
        pairs=pairs,
        opencode_app_free=[str(x) for x in (data.get("opencode_app_free") or [])],
        antigravity_free=[str(x) for x in (data.get("antigravity_free") or [])],
        report=str(data.get("report") or ""),
        sources=[str(x) for x in (data.get("sources") or [])],
        fetched_at=str(data.get("fetched_at") or ""),
        live_benchmarks=bool(data.get("live_benchmarks")),
        previous_pairs=pairs,
    )


# ── Probes (never raise) ───────────────────────────────────────────────


def _http_get_json(url: str, timeout: float = _HTTP_TIMEOUT) -> dict | None:
    # nosec B310: every caller passes one of the hardcoded https:// module
    # constants above — no user input ever reaches urlopen (same convention
    # as browser/browser_core/updater.py and tools/jules_delegate.py).
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "LuckyD-free-detect/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            if resp.status != 200:
                return None
            data = json.loads(resp.read().decode("utf-8", "replace"))
            return data if isinstance(data, dict) else None
    except Exception:
        return None


def _is_zero_price(pricing: dict | None) -> bool:
    if not isinstance(pricing, dict):
        return False
    try:
        prompt = float(pricing.get("prompt") or 0)
        completion = float(pricing.get("completion") or 0)
    except (TypeError, ValueError):
        return False
    return prompt == 0 and completion == 0


def probe_openrouter_free() -> list[str]:
    """Live ``:free`` ids on OpenRouter with exactly $0 token pricing."""
    data = _http_get_json(_OPENROUTER_MODELS_URL)
    if not data:
        return []
    free: list[str] = []
    for model in data.get("data") or []:
        if not isinstance(model, dict):
            continue
        mid = str(model.get("id") or "")
        if not mid or mid == "openrouter/free":
            continue
        if ":free" in mid and _is_zero_price(model.get("pricing")):
            free.append(mid)
    return free


def probe_cline_gateway_ids() -> set[str]:
    """Ids the Cline gateway lists right now (existence only — NOT a free
    signal: Cline's free credits are ending, so listing proves nothing
    about cost). Used for the report, never for auto-admission."""
    data = _http_get_json(_CLINE_MODELS_URL)
    if not data:
        return set()
    ids: set[str] = set()
    for model in data.get("data") or []:
        if isinstance(model, dict) and model.get("id"):
            ids.add(str(model["id"]))
    return ids


def probe_zen_ids() -> set[str]:
    """Ids OpenCode Zen lists right now (paid catalog freshness only —
    Zen has no free tier)."""
    data = _http_get_json(_ZEN_MODELS_URL)
    if not data:
        return set()
    ids: set[str] = set()
    for model in data.get("data") or []:
        if isinstance(model, dict) and model.get("id"):
            ids.add(str(model["id"]))
    return ids


def probe_opencode_app_free() -> list[str] | None:
    """The OpenCode app's own free roster via the local ``opencode`` CLI.

    Returns None when the binary is absent (this machine has no OpenCode
    install — e.g. CI); a list (possibly empty) when it ran. The ids are
    IN-APP-ONLY: they work inside the OpenCode app / LuckyD Code and 403
    on any direct API call, so they never enter the HTTP rotation.
    """
    import shutil

    if shutil.which("opencode") is None:
        return None
    try:
        proc = subprocess.run(
            ["opencode", "models"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception:
        return []
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    found: list[str] = []
    for token in text.replace(",", " ").replace("|", " ").split():
        token = token.strip().strip("`\"'")
        if token.startswith("opencode/") and token.endswith("-free") and token not in found:
            found.append(token)
    return found


def fetch_rankllms_scores() -> dict[str, dict[str, float]]:
    """Live benchmark scores from RankLLMs' free JSON API (AA indices).

    Returns {normalized key: {"intelligence": x, "coding": y, "agentic": z}}.
    Empty on any failure — the caller falls back to the snapshot.
    """
    data = _http_get_json(_RANKLLMS_URL)
    if not data:
        return {}
    scores: dict[str, dict[str, float]] = {}
    for row in data.get("rankings") or []:
        if not isinstance(row, dict):
            continue
        intel_raw = row.get("intelligence_index")
        coding_raw = row.get("coding_index")
        if intel_raw is None or coding_raw is None:
            continue
        try:
            intel = float(intel_raw)
            coding = float(coding_raw)
        except (TypeError, ValueError):
            continue
        keys = {
            _norm_key(str(row.get("openrouter_id") or "")),
            _norm_key(str(row.get("slug") or "")),
            _norm_key(str(row.get("name") or "")),
        }
        entry = {
            "intelligence": intel,
            "coding": coding,
            "agentic": float(row.get("agentic_index") or 0),
        }
        for key in keys:
            if key:
                scores.setdefault(key, entry)
    return scores


def _norm_key(model_id: str) -> str:
    """Normalize a model id for benchmark matching: lowercase, strip any
    provider prefix and any ``:free`` / ``-free`` suffix."""
    mid = (model_id or "").strip().lower()
    if "/" in mid:
        mid = mid.rsplit("/", 1)[-1]
    mid = mid.removesuffix(":free").removesuffix("-free")
    return mid


def _score_for(model_id: str, live: dict[str, dict[str, float]]) -> tuple[float, float]:
    """(coding, intelligence) for a model id — live RankLLMs first, then
    the Nov 10 snapshot. (0, 0) when neither knows the model: unscored
    models rank below scored ones, never at zero-as-score."""
    key = _norm_key(model_id)
    entry = live.get(key)
    if entry:
        return (entry.get("coding", 0.0), entry.get("intelligence", 0.0))
    snap = FREE_BENCHMARK_SNAPSHOT.get(key)
    if snap:
        return (0.0, float(snap.get("aa", 0.0)))
    snap = FREE_BENCHMARK_SNAPSHOT.get(model_id.lower())
    if snap:
        return (0.0, float(snap.get("aa", 0.0)))
    return (0.0, 0.0)


def antigravity_free_today(today: date | None = None) -> list[str]:
    """Antigravity's free roster today, date-gated: Claude Sonnet/Opus 4.6
    (and GPT-OSS-120b) are removed on 2026-11-18; after that free is
    Gemini 3.8 Flash alone. App-only (IDE/CLI) — no API route."""
    today = today or date.today()
    free = ["gemini-3.8-flash"]
    if today < ANTIGRAVITY_CLAUDE_REMOVED:
        free += ["claude-sonnet-4.6", "claude-opus-4.6", "gpt-oss-120b"]
    return free


# ── Detection ──────────────────────────────────────────────────────────


def detect_free_models() -> DetectionResult:
    """Probe the free gateways + live benchmarks; rank the auto-free list.

    Never raises and never touches the network on the selection path —
    callers read the cache via :func:`cached_detection`. Paid ids can
    never enter ``pairs``: only ``AUTO_FREE_PROVIDERS`` routes are
    admitted, and OpenRouter ids must price at exactly $0.
    """
    import time

    previous = cached_detection()
    previous_pairs = list(previous.pairs) if previous else []

    sources: list[str] = []
    live_scores = fetch_rankllms_scores()
    live_benchmarks = bool(live_scores)
    sources.append(
        "RankLLMs live (AA indices)" if live_benchmarks else f"benchmark snapshot {SNAPSHOT_DATE}"
    )

    # OpenRouter :free, ranked by live benchmarks (coding first — this is
    # a coding/agent rotation — then intelligence). Unscored ids sort last.
    or_free = probe_openrouter_free()
    sources.append(f"OpenRouter /models ({len(or_free)} :free ids at $0)")
    ranked_or = sorted(or_free, key=lambda m: _score_for(m, live_scores), reverse=True)

    cline_ids = probe_cline_gateway_ids()
    sources.append(
        f"Cline /models ({len(cline_ids)} ids listed; free credits ending — not auto-admitted)"
    )
    zen_ids = probe_zen_ids()
    sources.append(f"Zen /models ({len(zen_ids)} ids listed; keyed-only, no free tier)")

    app_free = probe_opencode_app_free()
    if app_free is None:
        sources.append("opencode CLI absent here — in-app free roster unverified on this machine")
        app_free = []
    else:
        sources.append(f"opencode models ({len(app_free)} in-app free ids)")

    # The auto rotation: default first, then the best live-verified free
    # cloud routes, Cline dead-last (legacy only — credits ending).
    pairs: list[tuple[str, str]] = [DEFAULT_PAIR]
    for mid in ranked_or[:3]:
        pairs.append(("openrouter", mid))
    pairs.append(("openrouter", "openrouter/free"))  # meta-router: never 404s
    pairs.append(("gemini", "gemini-2.5-flash"))
    pairs.append(("groq", "groq/compound-mini"))
    pairs.append(("cline-usage", "minimax/minimax-m2.5"))
    # De-duplicate, keep order.
    seen: set[tuple[str, str]] = set()
    deduped: list[tuple[str, str]] = []
    for pair in pairs:
        if pair not in seen:
            seen.add(pair)
            deduped.append(pair)

    antigravity = antigravity_free_today()
    fetched = time.strftime("%Y-%m-%d %H:%M %Z", time.localtime())
    result = DetectionResult(
        pairs=deduped,
        opencode_app_free=sorted(app_free),
        antigravity_free=antigravity,
        sources=sources,
        fetched_at=fetched,
        live_benchmarks=live_benchmarks,
        previous_pairs=previous_pairs,
        scores=live_scores,
    )
    result.report = free_rotation_report(result)
    _write_cache(
        {
            "pairs": [list(p) for p in deduped],
            "opencode_app_free": result.opencode_app_free,
            "antigravity_free": antigravity,
            "report": result.report,
            "sources": sources,
            "fetched_at": fetched,
            "fetched_at_ts": time.time(),
            "live_benchmarks": live_benchmarks,
        }
    )
    return result


def refresh_free_detection() -> DetectionResult:
    """Synchronous refresh (startup path). Same as :func:`detect_free_models`."""
    return detect_free_models()


def ensure_fresh_detection(background: bool = True) -> DetectionResult | None:
    """Return the cached detection when fresh; otherwise refresh it.

    With ``background=True`` the refresh runs in a daemon thread (Browser
    startup path — never block the UI on the network) and the stale cache
    (or None) is returned immediately. With ``background=False`` the
    refresh runs now (CLI startup path).
    """
    cached = cached_detection()
    if cached is not None:
        return cached
    if background:
        import threading

        threading.Thread(target=detect_free_models, name="luckyd-free-detect", daemon=True).start()
        return None
    return detect_free_models()


def free_rotation_report(result: DetectionResult) -> str:
    """Human-readable report: what the free rotation is, from -> to, why."""
    lines: list[str] = []
    lines.append(f"Free rotation (probed {result.fetched_at}):")
    lines.append(
        f"  default: {result.pairs[0][0]}/{result.pairs[0][1]} (LuckyD model, local, free, unlimited)"
    )
    for provider, model in result.pairs[1:]:
        coding, intel = _score_for(model, result.scores)
        why = ""
        if coding and intel:
            why = f" — coding {coding:g}, AA {intel:g}"
        elif intel:
            why = f" — AA {intel:g}"
        if provider == "openrouter" and model == "openrouter/free":
            why = " — meta-router, auto-picks any $0 model (never 404s)"
        elif provider == "cline-usage":
            why = " — LAST: Cline free credits ending"
        lines.append(f"  fallback: {provider}/{model}{why}")
    if result.opencode_app_free:
        lines.append(
            "  OpenCode app free (in-app only, switch in LuckyD Code): "
            + ", ".join(result.opencode_app_free)
        )
    else:
        lines.append(
            "  OpenCode app free: not probed here (opencode CLI absent); "
            "in LuckyD Code the free roster is opencode/deepseek-v4.1-flash-free "
            "first, then glm-5.3-flash-free, nemotron-3-ultra-free."
        )
    if result.antigravity_free:
        claude = [m for m in result.antigravity_free if "claude" in m]
        if claude:
            lines.append(
                "  Antigravity free: Gemini 3.8 Flash + Claude 4.6 until "
                f"{ANTIGRAVITY_CLAUDE_REMOVED} (then Gemini only)"
            )
        else:
            lines.append(
                "  Antigravity free: Gemini 3.8 Flash only "
                f"(Claude removed {ANTIGRAVITY_CLAUDE_REMOVED})"
            )
    lines.append("  MuseCode: no free tier (verified) — not offered as free.")
    if result.changed_from_previous() and result.previous_pairs:
        before = " → ".join(f"{p}/{m}" for p, m in result.previous_pairs)
        after = " → ".join(f"{p}/{m}" for p, m in result.pairs)
        lines.append(f"  CHANGED: {before}  ==>  {after}")
    lines.append(
        "  Sources: "
        + "; ".join(result.sources)
        + (
            "; benchmarks LIVE"
            if result.live_benchmarks
            else f"; benchmarks snapshot {SNAPSHOT_DATE}"
        )
    )
    return "\n".join(lines)
