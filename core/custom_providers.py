"""User-defined model providers backed by a JSON store (LuckyD 9.8)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

VALID_API_FORMATS = (
    "openai-completions",
    "anthropic-messages",
    "openai-responses",
)

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def providers_file(home: Path | None = None) -> Path:
    """Path of the JSON store (``~/.luckyd-code/providers.json``)."""
    base = home or Path.home()
    return base / ".luckyd-code" / "providers.json"


def validate_provider_id(provider_id: str) -> str:
    """Normalize + validate a provider id (raises ValueError when bad)."""
    pid = (provider_id or "").strip().lower()
    if not pid or not _ID_RE.match(pid):
        raise ValueError(
            f"invalid provider id: {provider_id!r} (use letters/digits/.-_, starting with alnum)"
        )
    return pid


def validate_api_format(api_format: str) -> str:
    """Normalize + validate an api-format string (raises ValueError)."""
    fmt = (api_format or "").strip().lower()
    if fmt not in VALID_API_FORMATS:
        raise ValueError(
            f"invalid --api-format: {api_format!r} (choose from {', '.join(VALID_API_FORMATS)})"
        )
    return fmt


@dataclass
class CustomProvider:
    """One user-defined provider entry."""

    id: str = ""
    name: str = ""
    base_url: str = ""
    api_format: str = "openai-completions"
    models: list[str] = field(default_factory=list)
    api_key_env: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CustomProvider:
        models = data.get("models", [])
        if isinstance(models, str):
            models = [models]
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "") or data.get("id", "")),
            base_url=str(data.get("base_url", "")),
            api_format=str(data.get("api_format", "openai-completions")),
            models=[str(m) for m in (models or [])],
            api_key_env=str(data.get("api_key_env", "")),
        )


def _read_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"providers": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"providers": []}
    if not isinstance(data, dict):
        return {"providers": []}
    return data


def _write_store(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def list_providers(store: Path | None = None) -> list[CustomProvider]:
    """Return stored custom providers (empty when none). Never raises."""
    path = store or providers_file()
    data = _read_store(path)
    out: list[CustomProvider] = []
    providers = data.get("providers", [])
    if not isinstance(providers, list):
        return out
    for entry in providers:
        if isinstance(entry, dict):
            try:
                out.append(CustomProvider.from_dict(entry))
            except Exception:
                continue
    return out


def get_provider(provider_id: str, store: Path | None = None) -> CustomProvider | None:
    """Fetch one provider by id (None when missing)."""
    pid = (provider_id or "").strip().lower()
    for provider in list_providers(store):
        if provider.id.lower() == pid:
            return provider
    return None


def add_provider(
    provider_id: str,
    base_url: str,
    api_format: str,
    models: list[str] | None = None,
    api_key_env: str = "",
    name: str = "",
    store: Path | None = None,
    overwrite: bool = False,
) -> CustomProvider:
    """Add (or overwrite) a custom provider entry. Raises ValueError."""
    pid = validate_provider_id(provider_id)
    fmt = validate_api_format(api_format)
    base = (base_url or "").strip().rstrip("/")
    if not base or not (base.startswith("http://") or base.startswith("https://")):
        raise ValueError(f"invalid --base-url: {base_url!r} (must be http(s)://...)")
    model_list = [m.strip() for m in (models or []) if m and m.strip()]
    if not model_list:
        raise ValueError("at least one --model is required")
    key_env = (api_key_env or "").strip()
    path = store or providers_file()
    data = _read_store(path)
    providers = data.get("providers", [])
    if not isinstance(providers, list):
        providers = []
    existing = [p for p in providers if isinstance(p, dict) and str(p.get("id", "")).lower() == pid]
    if existing and not overwrite:
        raise ValueError(f"provider {pid!r} already exists (use overwrite to replace)")
    providers = [
        p for p in providers if not (isinstance(p, dict) and str(p.get("id", "")).lower() == pid)
    ]
    provider = CustomProvider(
        id=pid,
        name=(name or "").strip() or pid,
        base_url=base,
        api_format=fmt,
        models=model_list,
        api_key_env=key_env,
    )
    providers.append(provider.to_dict())
    data["providers"] = providers
    _write_store(path, data)
    return provider


def remove_provider(provider_id: str, store: Path | None = None) -> bool:
    """Delete a provider entry. True when something was removed."""
    pid = (provider_id or "").strip().lower()
    path = store or providers_file()
    data = _read_store(path)
    providers = data.get("providers", [])
    if not isinstance(providers, list):
        return False
    kept = [
        p for p in providers if not (isinstance(p, dict) and str(p.get("id", "")).lower() == pid)
    ]
    if len(kept) == len(providers):
        return False
    data["providers"] = kept
    _write_store(path, data)
    return True


def use_provider_env(provider: CustomProvider, model: str = "") -> dict[str, str]:
    """Env mapping selecting this provider (persisted to .env by CLI)."""
    chosen = (model or "").strip() or (provider.models[0] if provider.models else "")
    env: dict[str, str] = {"CODING_AGENT_PROVIDER": provider.id}
    if chosen:
        env["CODING_AGENT_MODEL"] = chosen
    return env


def test_provider(
    provider: CustomProvider,
    model: str = "",
    timeout_sec: float = 15.0,
    client_factory: Any = None,
) -> dict[str, Any]:
    """Probe GET {base}/models with the stored key. Never raises."""
    headers: dict[str, str] = {}
    if provider.api_key_env:
        key = os.environ.get(provider.api_key_env, "")
        if key:
            if provider.api_format == "anthropic-messages":
                headers["x-api-key"] = key
            else:
                headers["Authorization"] = f"Bearer {key}"
    url = f"{provider.base_url.rstrip('/')}/models"
    try:
        import httpx as _httpx

        factory = client_factory or _httpx.Client
        client = factory(timeout=timeout_sec)
    except Exception as exc:
        return {"ok": False, "status": None, "models": [], "error": f"{type(exc).__name__}: {exc}"}
    try:
        if hasattr(client, "__enter__"):
            with client as session:
                resp = session.get(url, headers=headers)
        else:
            resp = client.get(url, headers=headers)
        status = int(getattr(resp, "status_code", 0) or 0)
        if status < 200 or status >= 300:
            return {"ok": False, "status": status, "models": [], "error": f"HTTP {status}"}
        try:
            payload = resp.json()
        except Exception as exc:
            return {"ok": False, "status": status, "models": [], "error": f"bad JSON: {exc}"}
        models: list[str] = []
        data = payload.get("data", []) if isinstance(payload, dict) else []
        if isinstance(data, list):
            for entry in data:
                if isinstance(entry, dict) and entry.get("id"):
                    models.append(str(entry["id"]))
                elif isinstance(entry, str):
                    models.append(entry)
        _ = model
        return {"ok": True, "status": status, "models": models, "error": ""}
    except Exception as exc:
        return {"ok": False, "status": None, "models": [], "error": f"{type(exc).__name__}: {exc}"}
