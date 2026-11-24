"""Paste-an-API-key credentials shared by LuckyD Code and the browser.

Both surfaces read the same per-user file
(``%LOCALAPPDATA%/LuckyDBrowser/.env`` on Windows), so a key saved by
LuckyD Code's ``/key`` command or by the browser AI assistant is available
everywhere on the next lookup -- no manual ``.env`` editing.

This module is deliberately dependency-light: it reads/writes a single
``KEY=value`` line, updates ``os.environ`` for the current process, and can
optionally verify the key against the provider with one cheap HTTP request.
Verification never blocks saving a key: an offline machine still stores it.
"""

from __future__ import annotations

import contextlib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from core.providers import (
    PROVIDER_DEFAULTS,
    PROVIDER_NAMES,
    VALID_PROVIDERS,
    normalize_provider_id,
)

__all__ = [
    "configured",
    "get_key",
    "key_providers",
    "mask_key",
    "provider_display",
    "provider_key_var",
    "set_api_key",
    "user_env_path",
    "verify_key",
]

#: Override for tests and unusual installs (an absolute .env path).
ENV_PATH_OVERRIDE = "LUCKYD_ENV_FILE"

#: Providers whose API uses a non-Bearer auth header for verification.
_ANTHROPIC_STYLE = frozenset({"anthropic", "minimax"})
_GOOGLE_STYLE = frozenset({"google", "gemini"})


def user_env_path() -> Path:
    """Writable per-user credential file shared by Code and the browser."""
    override = (os.environ.get(ENV_PATH_OVERRIDE, "") or "").strip()
    if override:
        return Path(override).expanduser()
    local = (os.environ.get("LOCALAPPDATA", "") or "").strip()
    if local:
        return Path(local) / "LuckyDBrowser" / ".env"
    return Path.home() / "AppData" / "Local" / "LuckyDBrowser" / ".env"


def provider_display(provider: str) -> str:
    """Human label for a provider id (falls back to the raw id)."""
    pid = normalize_provider_id(provider)
    return PROVIDER_NAMES.get(pid, pid or provider)


def provider_key_var(provider: str) -> str | None:
    """Env var that stores *provider*'s API key, or ``None`` when keyless."""
    pid = normalize_provider_id(provider)
    defaults = PROVIDER_DEFAULTS.get(pid)
    if not defaults:
        return None
    return defaults.get("env_key") or None


def key_providers() -> list[str]:
    """Provider ids that accept an API key, in display order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for pid in sorted(VALID_PROVIDERS):
        var = provider_key_var(pid)
        if var and var not in seen:
            seen.add(var)
            ordered.append(pid)
    return ordered


def mask_key(key: str) -> str:
    """Show enough to recognise a key without leaking it (``sk-or…9f2a``)."""
    key = (key or "").strip()
    if not key:
        return "(not set)"
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:6]}...{key[-4:]}"


def _read_pairs(path: Path) -> dict[str, str]:
    pairs: dict[str, str] = {}
    try:
        if not path.exists():
            return pairs
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            pairs[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        return pairs
    return pairs


def get_key(provider: str, env_path: Path | None = None) -> str:
    """Current key for *provider*: process env first, then the per-user file."""
    var = provider_key_var(provider)
    if not var:
        return ""
    value = (os.environ.get(var, "") or "").strip()
    if value:
        return value
    return _read_pairs(env_path or user_env_path()).get(var, "")


def _set_line(lines: list[str], key: str, value: str) -> list[str]:
    """Replace an active ``key=…`` line in place, or append one."""
    prefix = key + "="
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(prefix) and not stripped.startswith("#"):
            lines[idx] = f"{key}={value}"
            return lines
    if lines and lines[-1].strip():
        lines.append("")
    lines.append(f"{key}={value}")
    return lines


def _write_pairs(path: Path, key: str, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    lines = _set_line(lines, key, value)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if os.name != "nt":
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)


def _headers(pid: str, key: str) -> dict[str, str]:
    if pid in _ANTHROPIC_STYLE:
        return {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "Accept": "application/json",
        }
    return {"Authorization": f"Bearer {key}", "Accept": "application/json"}


def verify_key(provider: str, key: str, timeout: float = 8.0) -> tuple[bool | None, str]:
    """Cheaply check *key* against the provider.

    Returns ``(verified, message)``.  ``verified`` is ``True`` (accepted),
    ``False`` (rejected), or ``None`` when the check could not run (offline,
    unknown endpoint).  A rejection is the only case worth acting on; the key
    is still saved in every case.
    """
    pid = normalize_provider_id(provider)
    defaults = PROVIDER_DEFAULTS.get(pid)
    if not defaults or not key:
        return None, "no verification endpoint for this provider"
    base = str(defaults.get("default_base") or "").rstrip("/")
    if not base:
        return None, "no verification endpoint for this provider"

    if pid == "openrouter":
        url = f"{base}/key"
    elif pid in _GOOGLE_STYLE:
        url = f"{base}/models?key={key}"
    else:
        url = f"{base}/models"

    headers = {} if pid in _GOOGLE_STYLE else _headers(pid, key)
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
            status = int(getattr(response, "status", 200) or 200)
            body = response.read(4096).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return False, f"the provider rejected this key (HTTP {exc.code})"
        return None, f"could not verify (HTTP {exc.code})"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return None, f"could not verify (offline: {exc})"

    if status != 200:
        return None, f"could not verify (HTTP {status})"
    if pid == "openrouter":
        try:
            payload = json.loads(body)
        except ValueError:
            return None, "key accepted, but the response was unreadable"
        if isinstance(payload, dict) and payload.get("error"):
            return False, str(payload.get("error"))
    return True, "key accepted by the provider"


def set_api_key(
    provider: str,
    key: str,
    *,
    env_path: Path | None = None,
    verify: bool = True,
    timeout: float = 8.0,
) -> dict[str, object]:
    """Save *provider*'s key to the shared per-user .env and apply it now.

    The result dict is JSON-friendly so the browser control API and the CLI
    can render the same outcome:

    ``ok`` / ``provider`` / ``env_var`` / ``path`` / ``masked`` /
    ``verified`` (True/False/None) / ``message``
    """
    pid = normalize_provider_id(provider)
    result: dict[str, object] = {
        "ok": False,
        "provider": pid,
        "env_var": "",
        "path": "",
        "masked": "(not set)",
        "verified": None,
        "message": "",
    }
    if pid not in VALID_PROVIDERS:
        result["message"] = (
            f"Unknown provider '{provider}'. Try one of: {', '.join(key_providers())}"
        )
        return result
    var = provider_key_var(pid)
    if not var:
        result["message"] = f"{provider_display(pid)} is a local provider and needs no API key."
        return result

    cleaned = (key or "").strip().strip('"').strip("'")
    if not cleaned:
        result["message"] = "No key provided."
        return result
    if any(ch.isspace() for ch in cleaned):
        result["message"] = "That key contains whitespace -- copy it again without spaces."
        return result

    path = env_path or user_env_path()
    try:
        _write_pairs(path, var, cleaned)
    except OSError as exc:
        result["message"] = f"Could not write {path}: {exc}"
        return result

    os.environ[var] = cleaned
    message = f"Saved {var} for {provider_display(pid)} to {path}."
    result.update(
        {
            "ok": True,
            "env_var": var,
            "path": str(path),
            "masked": mask_key(cleaned),
        }
    )

    if verify:
        verified, detail = verify_key(pid, cleaned, timeout=timeout)
        result["verified"] = verified
        if verified is True:
            message += " The provider accepted the key."
        elif verified is False:
            message += f" Warning: {detail}."
        else:
            message += f" ({detail}; saved anyway.)"
    result["message"] = message
    return result


def configured(env_path: Path | None = None) -> list[dict[str, str]]:
    """Providers with a key on disk or in the environment, masked for display."""
    path = env_path or user_env_path()
    pairs = _read_pairs(path)
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for pid in key_providers():
        var = provider_key_var(pid)
        if not var or var in seen:
            continue
        value = (os.environ.get(var, "") or "").strip() or pairs.get(var, "")
        if not value:
            continue
        seen.add(var)
        rows.append(
            {
                "provider": pid,
                "name": provider_display(pid),
                "env_var": var,
                "masked": mask_key(value),
            }
        )
    return rows
