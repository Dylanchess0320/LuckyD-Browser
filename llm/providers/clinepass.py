"""ClinePass provider — token resolution for the api.cline.bot gateway.

The gateway authenticates with the live WorkOS session token from the Cline
CLI (`cline auth`). That token expires (~1h) and the CLI rotates it silently,
so this module resolves the freshest token on every call instead of caching.

Order of precedence:
  1. ``CLINEPASS_API_KEY`` env var (explicit override, e.g. from repo .env)
  2. The logged-in Cline CLI session in ``~/.cline/data/settings/providers.json``,
     refreshed via WorkOS when expired (``browser.browser_core.cline_session``).

Imported lazily by ``core.agent_loop._make_token_resolver`` so the rest of the
LLM stack never pays this import cost unless ClinePass is the active provider.
"""

from __future__ import annotations

import os

__all__ = ["ClinePassProvider"]


class ClinePassProvider:
    """Namespace for ClinePass auth helpers (no instance state)."""

    @staticmethod
    def get_cline_token() -> str | None:
        """Return a usable bearer token for the Cline gateway, or None.

        Re-reads the CLI session file each call so token refreshes (and even
        fresh `cline auth` logins) take effect without restarting LuckyD.
        Returns None — never raises — so callers can fall back to whatever
        key was configured at startup.
        """
        # 1. Explicit env override wins (set via repo .env or the shell).
        key = os.environ.get("CLINEPASS_API_KEY", "").strip()
        if key:
            return key

        # 2. Live CLI session, refreshing via WorkOS when expired.
        try:
            from browser.browser_core.cline_session import fresh_token

            token = fresh_token()
            return token or None
        except Exception:
            pass

        # 3. Last resort: any stored token, even a stale one — the gateway's
        #    401 will surface the real problem, which beats a silent None.
        try:
            from browser.browser_core.cline_session import stored_token

            token = stored_token()
            return token or None
        except Exception:
            return None
