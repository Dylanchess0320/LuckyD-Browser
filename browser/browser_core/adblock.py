"""Domain-suffix & URL-pattern ad/tracker blocking via QWebEngineUrlRequestInterceptor."""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtWebEngineCore import QWebEngineUrlRequestInterceptor

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
LIST_PATH = ASSETS_DIR / "adblock.txt"

# ── Google CAPTCHA-safe allowlist ─────────────────────────────────────
# Every google.com search was landing in an unsolvable "I'm not a robot"
# sorry-image CAPTCHA. The adblocker must never break the challenge flow:
# google /search, /sorry/*, and consent.google.com must always load even if
# an aggressive list blocks "google.com" outright.
_CAPTCHA_SAFE_HOSTS = frozenset(
    {"www.google.com", "google.com", "consent.google.com", "sorry.google.com"}
)

# CAPTCHA-safe URL regexes — any match means NEVER block (checked before
# both Layer 1 domain blocking and Layer 2 pattern blocking).
_CAPTCHA_SAFE_RES: list[re.Pattern] = [
    re.compile(r"google\.com/sorry/"),  # CAPTCHA-safe: challenge flow
    re.compile(r"consent\.google\.com"),  # CAPTCHA-safe: consent redirect
    re.compile(r"[?&]id="),  # CAPTCHA-safe: challenge token (scoped in _is_captcha_safe_url)
    re.compile(r"/sorry/image"),  # CAPTCHA-safe: challenge image
    re.compile(r"sorry/image"),  # CAPTCHA-safe: challenge image (any prefix)
    re.compile(r"sorry/index"),  # CAPTCHA-safe: challenge page
]


def _is_captcha_safe_url(url_str: str, host: str) -> bool:
    """True when the URL is part of Google search/consent/challenge flow.

    Checked before every blocking layer so an aggressive blocklist entry
    (e.g. "google.com") can never break CAPTCHA rendering.
    """
    lowered = url_str.lower()
    # CAPTCHA-safe: "[?&]id=" is the sorry-image challenge token — but only
    # for Google consent/sorry challenge URLs, NOT every "?id=" URL on the
    # web (otherwise ordinary ad URLs with an id param would slip through).
    id_token = re.search(r"[?&]id=", url_str)
    if id_token and (
        "google.com/sorry" in lowered
        or "consent.google" in lowered
        or "sorry/image" in lowered
        or "sorry/index" in lowered
    ):
        return True
    # CAPTCHA-safe: never break the sorry/consent challenge rendering.
    for pattern in _CAPTCHA_SAFE_RES:
        if pattern.pattern == r"[?&]id=":
            continue  # handled above with google/sorry scoping
        if pattern.search(url_str) or pattern.search(lowered):
            return True
    # CAPTCHA-safe: Google search/consent hosts always allow their
    # search and challenge paths.
    host_lower = (host or "").lower()
    path = _url_path(url_str)
    # CAPTCHA-safe: return the host/path condition directly (ruff SIM103).
    return host_lower in _CAPTCHA_SAFE_HOSTS and (
        path.startswith("/search") or path.startswith("/sorry") or "consent.google" in lowered
    )


def _url_path(url_str: str) -> str:
    """Extract the URL path without requiring a real QUrl (test fakes too)."""
    try:
        from urllib.parse import urlsplit

        return urlsplit(url_str).path or ""
    except Exception:
        return ""


# ── YouTube ad-serving URL regexes ────────────────────────────────────
# These match against the full request URL.  googlevideo.com / ytimg.com
# are deliberately NOT blocked by domain (they serve content); ad patterns
# on those hosts are matched individually.
_YT_AD_PATTERNS: list[re.Pattern] = [
    # Google IMA SDK — THE primary ad delivery mechanism for YouTube
    re.compile(r"imasdk\.googleapis\.com"),
    re.compile(r"googleads\.g\.doubleclick\.net"),
    # YouTube page / pagead frames
    re.compile(r"youtube\.com/pagead/"),
    re.compile(r"youtube\.com/get_midroll_info"),
    re.compile(r"youtube\.com/ptracking"),
    re.compile(r"youtube\.com/adunit/"),
    # YouTube Stats for Ads (telemetry)
    re.compile(r"youtube\.com/api/stats/ads"),
    # YouTube API: block requests with ad params
    re.compile(r"youtubei/v1/player\?.*(?:ad|advertisement)"),
    re.compile(r"youtubei/v1/next\?.*(?:ad|advertisement)"),
    re.compile(r"youtubei/v1/account/playback"),
    # googlevideo.com ad-specific query params
    re.compile(r"googlevideo\.com/.*[?&]ad=(?:1|true)"),
    re.compile(r"googlevideo\.com/.*[?&]ad_type="),
    re.compile(r"googlevideo\.com/.*[?&]adunit="),
    # YouTube ad module on googlevideo
    re.compile(r"googlevideo\.com/.*[?&]sideplay="),
    re.compile(r"googlevideo\.com/.*[?&]adblock="),
    re.compile(r"googlevideo\.com/.*[?&]oad="),
    # DoubleClick / AdSense
    re.compile(r"(?:pagead|static2|tdns)\.googlesyndication\.com/"),
    re.compile(r"securepubads\.g\.doubleclick\.net/"),
    re.compile(r"ad\.doubleclick\.net/"),
    re.compile(r"static\.doubleclick\.net/"),
    re.compile(r"adservice\.google\.com/"),
    # Funding Choices / Consent
    re.compile(r"fundingchoicesmessages\.google\.com/"),
    # GTM & Optimize
    re.compile(r"googletagmanager\.com/"),
    re.compile(r"googleoptimize\.com/"),
    # Third-party ad servers
    re.compile(r"2mdn\.net/"),
    re.compile(r"partneradserver\.dailymotion\.com/"),
    # YouTube ad event beacons
    re.compile(r"youtube\.com/api/stats/qoe\?.*ad"),
    re.compile(r"youtube\.com/api/stats/playback\?.*ad"),
    # ── Additional YouTube ad channels ─────────────────────────────────
    # YouTube's newer ad delivery methods (server-side injected)
    re.compile(r"youtube\.com/api/stats/ads/tv"),
    re.compile(r"youtube\.com/youtubei/v1/player/ad_break"),
    re.compile(r"youtube\.com/youtubei/v1/player/get_ad_schedule"),
    re.compile(r"youtube\.com/youtubei/v1/ad"),
    re.compile(r"youtube\.com/youtubei/v1/next\?.*ad_break"),
    re.compile(r"youtube\.com/api/timedtext\?.*ad"),
    re.compile(r"youtube\.com/s/player/.*ad"),
    # googlevideo.com ad-serving patterns
    re.compile(r"googlevideo\.com/.*\boad\b"),
    # NOTE: no \bctier\b pattern here — ctier is YouTube's content-TIER param
    # on ordinary streams (Shorts especially), not an ad marker. Matching it
    # blocked real videoplayback fetches: Shorts stalled at 0:00 / glitched.
    re.compile(r"googlevideo\.com/.*\bid\b.*\bad\b"),
    re.compile(r"googlevideo\.com/.*[?&]mime=.*/mp4.*[?&]ad"),
    re.compile(r"googlevideo\.com/videoplayback\?.*\bad\b"),
    re.compile(r"googlevideo\.com/.*[?&]xtags=.*ad_"),
    # YouTube ads served through their billing/shopping infrastructure
    re.compile(r"youtube\.com/shopping/"),
    re.compile(r"youtube\.com/oembed\?.*ads"),
    # YouTube Premium upsell / ad block detection
    re.compile(r"youtube\.com/get_midroll_"),
    re.compile(r"youtube\.com/api/an_"),
    # VPAID / VAST ad containers (only from known ad domains)
    re.compile(r"(?:doubleclick|googlesyndication|2mdn)\.net/.*vast"),
    re.compile(r"(?:doubleclick|googlesyndication|2mdn)\.net/.*vpaid"),
]


class AdBlockInterceptor(QWebEngineUrlRequestInterceptor):
    """Blocks ad/tracker requests by domain suffix or known ad URL patterns.

    Three-layer approach:
      1. Domain-suffix blocklist (text file) — cheap, catches most trackers
      2. URL path patterns — catch YouTube/Google ads co-located on content domains
      3. Query-param stripping — removes ad params from allowed URLs (googlevideo.com)
    """

    def __init__(self, enabled: bool = True, list_path: Path = LIST_PATH):
        super().__init__()
        self._enabled = enabled
        self._list_path = list_path
        self._blocked_domains = self._load(list_path)
        self.blocked_count = 0

    # ── helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _load(path: Path) -> set[str]:
        domains: set[str] = set()
        try:
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip().lower()
                    if line and not line.startswith("#"):
                        domains.add(line)
        except Exception:
            pass
        return domains

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def is_enabled(self) -> bool:
        return self._enabled

    def reload(self) -> None:
        self._blocked_domains = self._load(self._list_path)

    def _domain_is_blocked(self, host: str) -> bool:
        host = host.lower()
        while host:
            if host in self._blocked_domains:
                return True
            _, _, host = host.partition(".")
        return False

    def _url_matches_ad_pattern(self, url_str: str) -> bool:
        # CAPTCHA-safe: never treat Google sorry/consent challenge URLs as
        # ads, even if an aggressive ad pattern happens to match (e.g. a
        # googlevideo "?id=" + "ad" pattern against /sorry/image?id=...).
        lowered = url_str.lower()
        if "google.com/sorry" in lowered or "consent.google" in lowered:
            return False
        if "sorry/image" in lowered or "sorry/index" in lowered:
            return False
        return any(pattern.search(url_str) for pattern in _YT_AD_PATTERNS)

    # ── Qt interceptor ────────────────────────────────────────────────

    def interceptRequest(self, info) -> None:  # noqa: N802 (Qt API name)
        if not self._enabled:
            return

        url = info.requestUrl()
        host = url.host()
        url_str = url.toString()
        if not host or not url_str:
            return

        # CAPTCHA-safe allowlist: checked BEFORE Layer 1 so Google
        # search/consent/challenge URLs always load even when the domain
        # list aggressively blocks "google.com".
        if _is_captcha_safe_url(url_str, host):
            return

        # Layer 1: Domain-suffix blocklist
        if self._domain_is_blocked(host):
            info.block(True)
            self.blocked_count += 1
            return

        # Layer 2: URL path patterns
        if self._url_matches_ad_pattern(url_str):
            info.block(True)
            self.blocked_count += 1
            return
