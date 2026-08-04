"""Read the Cline CLI session so the browser can use ClinePass with no API key.

The Cline CLI stores auth in ~/.cline/data/settings/providers.json under
providers["cline-pass" | "cline"].settings.auth — a WorkOS access token
(~1h lifetime) plus a long-lived refresh token. The CLI renews the access
token only while it runs, so this module refreshes it directly via the
WorkOS API once every stored token expires, writing the (possibly rotated)
credentials back to the same file for the CLI to pick up. Net effect:
ClinePass stays available in the browser with the terminal closed, no
matter which model the CLI itself is set to.

For a permanent setup, create a long-lived key at app.cline.bot ->
Settings -> API Keys and set CLINEPASS_API_KEY in the repo .env — the file
session is only the fallback. Set CLINE_DATA_DIR to override the lookup
directory (used by tests).
"""

from __future__ import annotations

import base64
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

_WORKOS_AUTH_URL = "https://api.workos.com/user_management/authenticate"
# Public WorkOS client id of the Cline CLI (embedded in its JWTs) — only a
# fallback for when the id cannot be decoded from a stored token.
_DEFAULT_CLIENT_ID = "client_01K3A541FN8TA3EPPHTD2325AR"
# Provider entries that share the one Cline account session.
_SESSION_PROVIDERS = ("cline-pass", "cline")


def _providers_path() -> Path:
    override = os.environ.get("CLINE_DATA_DIR", "").strip()
    base = Path(override) if override else Path.home() / ".cline" / "data"
    return base / "settings" / "providers.json"


def _read_providers() -> dict:
    path = _providers_path()
    if not path.exists():
        raise RuntimeError(f"Cline CLI session not found ({path}) — run `cline auth` once")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"cannot parse {path}: {exc}") from exc


def _iter_tokens(data: dict | None = None) -> list[tuple[str, str, int, str]]:
    """Stored session entries as (provider, access_token, expires_ms, refresh)."""
    if data is None:
        data = _read_providers()
    providers = data.get("providers", {})
    out: list[tuple[str, str, int, str]] = []
    for name in _SESSION_PROVIDERS:
        settings = (providers.get(name) or {}).get("settings") or {}
        auth = settings.get("auth") or {}
        token = str(auth.get("accessToken", "")).strip()
        refresh = str(auth.get("refreshToken", "")).strip()
        if token or refresh:
            out.append((name, token, int(auth.get("expiresAt", 0) or 0), refresh))
    return out


def has_session() -> bool:
    """True when any Cline session credential exists (even an expired one)."""
    try:
        return bool(_iter_tokens())
    except RuntimeError:
        return False


def load_session() -> tuple[str, int]:
    """Return (access_token, expires_at_ms) — prefers cline-pass over cline."""
    tokens = _iter_tokens()
    if not tokens:
        raise RuntimeError("no Cline session token found — run `cline auth` once")
    _, token, expires_at, _ = tokens[0]
    return token, expires_at


def _valid(expires_at: int) -> bool:
    return not expires_at or time.time() * 1000 <= expires_at - 60_000


def stored_token() -> str:
    """First currently-valid stored access token — pure file read, no network."""
    for _name, token, expires_at, _refresh in _iter_tokens():
        if token and _valid(expires_at):
            return token
    raise RuntimeError("no valid Cline session token stored")


def _decode_jwt(token: str) -> dict:
    """Claims of a JWT (tolerates the CLI's `workos:` prefix); {} on failure."""
    raw = token.split(":", 1)[1] if token.startswith("workos:") else token
    parts = raw.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload.encode()))
    except Exception:
        return {}


def _client_id(tokens: list[tuple[str, str, int, str]]) -> str:
    """WorkOS client id decoded from a stored token, else the CLI default."""
    for _name, token, _exp, _refresh in tokens:
        claims = _decode_jwt(token)
        cid = str(claims.get("client_id", "")).strip()
        if cid:
            return cid
        iss = str(claims.get("iss", ""))
        if "/client_" in iss:
            return "client_" + iss.rsplit("/client_", 1)[1]
    return _DEFAULT_CLIENT_ID


def _http_post(url: str, payload: dict) -> dict:
    """POST JSON and return the decoded body (seam for tests)."""
    import httpx  # lazy: file-only code paths stay importable without httpx

    resp = httpx.post(url, json=payload, timeout=15.0)
    if resp.status_code >= 400:
        raise RuntimeError(f"WorkOS HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def _atomic_write(data: dict) -> None:
    path = _providers_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _refresh_and_store(refresh_token: str) -> str:
    """Swap a refresh token for a fresh access token and persist the result.

    Both session provider entries share the one account session, so every
    entry holding the *used* refresh token is updated — the CLI reads its
    tokens back from this file and stays consistent.
    """
    data = _read_providers()  # re-read: freshest possible token state
    tokens = _iter_tokens(data)
    body = _http_post(
        _WORKOS_AUTH_URL,
        {
            "client_id": _client_id(tokens),
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
    )
    access = str(body.get("access_token", "")).strip()
    if not access:
        raise RuntimeError("WorkOS refresh returned no access_token")
    if not access.startswith("workos:"):
        access = "workos:" + access  # match the CLI's storage format
    new_refresh = str(body.get("refresh_token", "")).strip() or refresh_token
    exp = _decode_jwt(access).get("exp")
    expires_at = int(exp) * 1000 if exp else int(time.time() * 1000) + 3_600_000

    providers = data.get("providers", {})
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    touched = False
    for name in _SESSION_PROVIDERS:
        entry = providers.get(name) or {}
        auth = (entry.get("settings") or {}).get("auth") or {}
        if str(auth.get("refreshToken", "")).strip() != refresh_token:
            continue
        auth["accessToken"] = access
        auth["refreshToken"] = new_refresh
        auth["expiresAt"] = expires_at
        entry["updatedAt"] = now
        touched = True
    if touched:
        _atomic_write(data)
    return access


def fresh_token() -> str:
    """Return a non-expired session token, refreshing via WorkOS if needed.

    The CLI renews the ~1h access tokens only while it runs; the stored
    refresh token lets the browser do it too, so ClinePass keeps working
    with the terminal closed. Raises a helpful error when logged out.
    """
    tokens = _iter_tokens()
    for _name, token, expires_at, _refresh in tokens:
        if token and _valid(expires_at):
            return token
    refresh = next((r for _n, _t, _e, r in tokens if r), "")
    if not refresh:
        if tokens:
            raise RuntimeError(
                "Cline session expired — run `cline` (or `cline auth`) once "
                "to refresh, or set CLINEPASS_API_KEY in the repo .env"
            )
        raise RuntimeError("no Cline session token found — run `cline auth` once")
    try:
        return _refresh_and_store(refresh)
    except Exception as exc:
        # A concurrent CLI/browser may have rotated the token mid-flight:
        # re-read and use whatever is freshest before giving up.
        try:
            tokens = _iter_tokens()
        except RuntimeError:
            tokens = []
        for _name, token, expires_at, _r in tokens:
            if token and _valid(expires_at):
                return token
        newer = next((r for _n, _t, _e, r in tokens if r and r != refresh), "")
        if newer:
            try:
                return _refresh_and_store(newer)
            except Exception:
                pass
        raise RuntimeError(
            "Cline session expired and auto-refresh failed "
            f"({exc}) — run `cline` once to re-login, or set "
            "CLINEPASS_API_KEY in the repo .env"
        ) from exc
