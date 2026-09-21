"""Local + official plugin management (LuckyD 9.8)."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

OFFICIAL_MARKETPLACE = "official"
LOCAL_MARKETPLACE = "local"

_MANIFEST_FILES = ("plugin.json", "SKILL.md")


def plugins_dir(home: Path | None = None) -> Path:
    """Local plugin root (``~/.luckyd-code/plugins``)."""
    base = home or Path.home()
    return base / ".luckyd-code" / "plugins"


def plugins_state_file(home: Path | None = None) -> Path:
    """Disabled-set JSON (``~/.luckyd-code/plugins.json``)."""
    base = home or Path.home()
    return base / ".luckyd-code" / "plugins.json"


def official_catalog_dir(repo_root: Path | None = None) -> Path:
    """Official catalog root (``kit/skills`` in the repo)."""
    if repo_root is not None:
        return repo_root / "kit" / "skills"
    return Path(__file__).resolve().parent.parent / "kit" / "skills"


def split_name_ref(ref: str) -> tuple[str, str]:
    """Split ``name[@marketplace]`` into (name, marketplace-or-empty)."""
    text = (ref or "").strip()
    if "@" in text:
        name, _, market = text.partition("@")
        return (name.strip(), market.strip().lower())
    return (text, "")


@dataclass
class PluginInfo:
    """One plugin row for list/add/enable/disable/remove views."""

    name: str = ""
    marketplace: str = LOCAL_MARKETPLACE
    enabled: bool = True
    description: str = ""
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"disabled": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"disabled": []}
    return data if isinstance(data, dict) else {"disabled": []}


def _write_state(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def disabled_set(home: Path | None = None) -> set[str]:
    data = _read_state(plugins_state_file(home))
    raw = data.get("disabled", [])
    if not isinstance(raw, list):
        return set()
    return {str(n) for n in raw if str(n).strip()}


def set_enabled(name: str, enabled: bool, home: Path | None = None) -> None:
    key = (name or "").strip()
    if not key:
        raise ValueError("plugin name is required")
    path = plugins_state_file(home)
    data = _read_state(path)
    disabled = disabled_set(home)
    if enabled:
        disabled.discard(key)
    else:
        disabled.add(key)
    data["disabled"] = sorted(disabled)
    _write_state(path, data)


def _describe_manifest(plugin_dir: Path) -> str:
    manifest = plugin_dir / "plugin.json"
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("description"):
                return str(data["description"])
        except (OSError, ValueError):
            pass
    skill = plugin_dir / "SKILL.md"
    if skill.is_file():
        try:
            for line in skill.read_text(encoding="utf-8").splitlines()[:20]:
                if line.strip().lower().startswith("description:"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
    return ""


def list_official(repo_root: Path | None = None) -> list[PluginInfo]:
    """Official catalog rows from kit/skills (installable)."""
    root = official_catalog_dir(repo_root)
    out: list[PluginInfo] = []
    if not root.is_dir():
        return out
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if not any((child / m).exists() for m in _MANIFEST_FILES):
            continue
        out.append(
            PluginInfo(
                name=child.name,
                marketplace=OFFICIAL_MARKETPLACE,
                enabled=True,
                description=_describe_manifest(child),
                path=str(child),
            )
        )
    return out


def list_local(home: Path | None = None) -> list[PluginInfo]:
    """Installed local plugin rows (enabled flag from plugins.json)."""
    root = plugins_dir(home)
    out: list[PluginInfo] = []
    if not root.is_dir():
        return out
    disabled = disabled_set(home)
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if not any((child / m).exists() for m in _MANIFEST_FILES):
            continue
        name = child.name
        out.append(
            PluginInfo(
                name=name,
                marketplace=LOCAL_MARKETPLACE,
                enabled=name not in disabled,
                description=_describe_manifest(child),
                path=str(child),
            )
        )
    return out


def list_plugins(
    marketplace: str = "",
    include_available: bool = False,
    home: Path | None = None,
    repo_root: Path | None = None,
) -> list[PluginInfo]:
    """List plugins; marketplace filters to official|local when given."""
    want = (marketplace or "").strip().lower()
    if want and want not in (OFFICIAL_MARKETPLACE, LOCAL_MARKETPLACE):
        raise ValueError(f"unknown marketplace: {marketplace!r} (use official|local)")
    rows: list[PluginInfo] = []
    if not want or want == LOCAL_MARKETPLACE:
        rows.extend(list_local(home))
    if include_available and (not want or want == OFFICIAL_MARKETPLACE):
        installed = {p.name for p in list_local(home)}
        for item in list_official(repo_root):
            if item.name not in installed:
                rows.append(item)
    return rows


def marketplace_names(home: Path | None = None, repo_root: Path | None = None) -> list[str]:
    """Marketplace names for `plugin marketplace list`."""
    _ = (home, repo_root)
    return [OFFICIAL_MARKETPLACE, LOCAL_MARKETPLACE]


def add_plugin(ref: str, home: Path | None = None, repo_root: Path | None = None) -> PluginInfo:
    """Install a plugin: official catalog copies into the local dir."""
    name, market = split_name_ref(ref)
    if not name:
        raise ValueError("plugin name is required")
    if name.startswith(("http://", "https://", "git@")) or "://" in name:
        raise ValueError("GitHub-URL plugin installs are not supported (like upstream)")
    if market and market not in (OFFICIAL_MARKETPLACE, LOCAL_MARKETPLACE):
        raise ValueError(f"unknown marketplace: {market!r} (use official|local)")
    dest_root = plugins_dir(home)
    dest = dest_root / name
    if (dest / "plugin.json").exists() or (dest / "SKILL.md").exists():
        raise ValueError(f"plugin {name!r} is already installed")
    catalog = {p.name: p for p in list_official(repo_root)}
    if name in catalog:
        src = Path(catalog[name].path)
        dest_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest)
        set_enabled(name, True, home)
        return PluginInfo(
            name=name,
            marketplace=LOCAL_MARKETPLACE,
            enabled=True,
            description=catalog[name].description,
            path=str(dest),
        )
    if market == OFFICIAL_MARKETPLACE:
        raise ValueError(f"plugin {name!r} is not in the official catalog")
    known = ", ".join(sorted(catalog)) or "nothing"
    raise ValueError(f"plugin {name!r} not found (official catalog has: {known})")


def remove_plugin(name: str, home: Path | None = None) -> bool:
    """Remove an installed local plugin. True when something was removed."""
    key = (name or "").strip()
    if not key:
        return False
    target = plugins_dir(home) / key
    removed = False
    if target.is_dir():
        shutil.rmtree(target, ignore_errors=True)
        removed = True
    data_path = plugins_state_file(home)
    data = _read_state(data_path)
    disabled = disabled_set(home)
    if key in disabled:
        disabled.discard(key)
        data["disabled"] = sorted(disabled)
        _write_state(data_path, data)
    return removed


def enable_plugin(name: str, home: Path | None = None) -> bool:
    """Enable an installed plugin. False when not installed."""
    key = (name or "").strip()
    if not key or not (plugins_dir(home) / key).is_dir():
        return False
    set_enabled(key, True, home)
    return True


def disable_plugin(name: str, home: Path | None = None) -> bool:
    """Disable an installed plugin. False when not installed."""
    key = (name or "").strip()
    if not key or not (plugins_dir(home) / key).is_dir():
        return False
    set_enabled(key, False, home)
    return True
