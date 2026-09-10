"""
Skills marketplace tools — search, review, install, update, remove, publish.

Installing is deliberately two-step: SkillFetch downloads a skill, verifies
its hash, and shows you a review; SkillInstall(fetch_id) writes it. Only the
write step needs approval. Installed skills live in ~/.luckyd/skills and are
picked up by SkillList/SkillRun alongside the bundled ones.
"""

from __future__ import annotations

import time

from core.marketplace import (
    MarketplaceStore,
    RegistrySource,
    fetch_package,
    fetch_registry,
    install_package,
    publish_pack,
    remove_installed,
    review_text,
    stash_pending,
    take_pending,
)

from .base import ToolBase, ToolOutput
from .registry import register_tool

_store: MarketplaceStore | None = None


def get_store() -> MarketplaceStore:
    global _store
    if _store is None:
        _store = MarketplaceStore()
    return _store


# Cache registry listings briefly so repeated searches don't re-fetch remotes.
_registry_cache: dict[str, tuple[float, list]] = {}
_REGISTRY_TTL = 300.0


def _entries(source: RegistrySource) -> list[dict]:
    now = time.monotonic()
    hit = _registry_cache.get(source.name)
    if hit and now - hit[0] < _REGISTRY_TTL:
        return hit[1]
    entries = fetch_registry(source)
    _registry_cache[source.name] = (now, entries)
    return entries


def _find_entry(store: MarketplaceStore, name: str, source_name: str = ""):
    """Return (entry, source) for a skill name, or (None, None)."""
    for source in store.sources():
        if source_name and source.name != source_name:
            continue
        try:
            entries = _entries(source)
        except Exception:
            continue
        for entry in entries:
            if str(entry.get("name", "")).lower() == name.lower():
                return entry, source
    return None, None


def _version_key(v: str) -> tuple:
    parts = []
    for p in str(v).split("."):
        parts.append(int(p) if p.isdigit() else p)
    return tuple(parts)


class SkillSearchTool(ToolBase):
    name = "SkillSearch"
    description = (
        "Search the skills marketplace: installed, bundled, and community "
        "registries. Returns matching skills with name, version, author, and source."
    )
    aliases = ["SearchSkills", "MarketplaceSearch"]
    permission_level = "ALWAYS_ALLOW"
    parameters = {
        "query": {
            "type": "string",
            "description": "Search text (matches name, description, tags). Empty = list all.",
        },
        "source": {"type": "string", "description": "Only search one registry source (optional)."},
    }

    async def execute(self, query: str = "", source: str = "") -> ToolOutput:
        store = get_store()
        q = query.lower()
        hits = []
        for src in store.sources():
            if source and src.name != source:
                continue
            try:
                entries = _entries(src)
            except Exception as e:
                hits.append(f"  (source '{src.name}' unreachable: {e})")
                continue
            for e in entries:
                hay = f"{e.get('name', '')} {e.get('description', '')} {' '.join(e.get('tags', []) or [])}".lower()
                if q and q not in hay:
                    continue
                installed = store.get_installed(str(e.get("name", "")))
                mark = f" [installed v{installed['version']}]" if installed else ""
                hits.append(
                    f"• {e.get('name')} v{e.get('version', '?')} by {e.get('author', '?')}{mark}\n"
                    f"  {e.get('description', '')}\n"
                    f"  source: {src.name} · tags: {', '.join(e.get('tags', []) or [])}"
                )
        text = "\n".join(hits) if hits else "No skills matched."
        return ToolOutput(text=text, title="Marketplace search", metadata={"count": len(hits)})


class SkillInfoTool(ToolBase):
    name = "SkillInfo"
    description = "Show full details of a marketplace skill: description, version, author, tags, source, integrity hash."
    permission_level = "ALWAYS_ALLOW"
    parameters = {
        "name": {"type": "string", "description": "Skill name."},
        "source": {"type": "string", "description": "Registry source (optional)."},
    }

    async def execute(self, name: str, source: str = "") -> ToolOutput:
        entry, src = _find_entry(get_store(), name, source)
        if not entry:
            return ToolOutput(text=f"Error: skill {name!r} not found in any registry.", error=True)
        installed = get_store().get_installed(entry["name"])
        lines = [
            f"📦 {entry.get('name')} v{entry.get('version', '?')} by {entry.get('author', '?')}",
            entry.get("description", ""),
            f"Source: {src.name} ({entry.get('url', '')})",
            f"Tags: {', '.join(entry.get('tags', []) or []) or '—'}",
            f"Registry hash: {entry.get('sha256', 'not published')[:32]}",
            f"Installed: v{installed['version']} ({installed['installed_at']})"
            if installed
            else "Installed: no",
        ]
        return ToolOutput(text="\n".join(lines), metadata={"entry": entry, "source": src.name})


class SkillFetchTool(ToolBase):
    name = "SkillFetch"
    description = (
        "Download a marketplace skill and VERIFY it (hash check against the "
        "registry). Read-only — returns a full review plus a fetch_id. "
        "Pass the fetch_id to SkillInstall to actually install it."
    )
    permission_level = "ALWAYS_ALLOW"
    parameters = {
        "name": {"type": "string", "description": "Skill name."},
        "source": {"type": "string", "description": "Registry source (optional)."},
    }

    async def execute(self, name: str, source: str = "") -> ToolOutput:
        store = get_store()
        entry, src = _find_entry(store, name, source)
        if not entry:
            return ToolOutput(text=f"Error: skill {name!r} not found in any registry.", error=True)
        try:
            pkg = fetch_package(entry, src)
        except (ValueError, RuntimeError, OSError) as e:
            return ToolOutput(text=f"Error: {e}", error=True)
        fid = stash_pending(pkg)
        text = (
            review_text(pkg)
            + f"\n\nReview looks good? Install with: SkillInstall(fetch_id={fid!r})"
        )
        return ToolOutput(
            text=text,
            title=f"Review: {pkg.name}",
            metadata={
                "fetch_id": fid,
                "name": pkg.name,
                "version": pkg.version,
                "verified": pkg.verified,
            },
        )


class SkillInstallTool(ToolBase):
    name = "SkillInstall"
    description = (
        "Install a fetched skill (from SkillFetch or SkillUpdate) into your "
        "personal skills. Needs approval — this writes files."
    )
    aliases = ["InstallSkill"]
    permission_level = "REQUIRES_APPROVAL"
    parameters = {
        "fetch_id": {
            "type": "string",
            "description": "The fetch_id returned by SkillFetch/SkillUpdate.",
        },
    }

    async def execute(self, fetch_id: str) -> ToolOutput:
        pkg = take_pending(fetch_id)
        if not pkg:
            return ToolOutput(
                text="Error: unknown or expired fetch_id. Run SkillFetch again to get a review.",
                error=True,
            )
        store = get_store()
        try:
            target = install_package(pkg, store)
        except OSError as e:
            return ToolOutput(text=f"Error: install failed: {e}", error=True)
        # New skills must be visible to SkillList/SkillRun immediately.
        from tools.skill_tools import _skill_cache_invalidate

        _skill_cache_invalidate()
        return ToolOutput(
            text=f"✅ Installed {pkg.name} v{pkg.version} → {target}\n"
            f"Provenance: {pkg.source_name} ({pkg.source_url}), sha256 {pkg.sha256[:16]}…\n"
            f"Use it with SkillRun(skill_name={pkg.name!r}).",
            title=f"Installed {pkg.name}",
            metadata={"name": pkg.name, "version": pkg.version, "path": str(target)},
        )


class SkillUpdateTool(ToolBase):
    name = "SkillUpdate"
    description = (
        "Check a skill for newer versions across registries. If one exists, "
        "downloads and verifies it and returns a review + fetch_id — "
        "call SkillInstall(fetch_id=...) to apply."
    )
    permission_level = "REQUIRES_APPROVAL"
    parameters = {"name": {"type": "string", "description": "Installed skill name."}}

    async def execute(self, name: str) -> ToolOutput:
        store = get_store()
        installed = store.get_installed(name)
        if not installed:
            return ToolOutput(text=f"Error: {name!r} is not installed.", error=True)
        best = None
        best_src = None
        for src in store.sources():
            try:
                entries = _entries(src)
            except Exception:
                continue
            for e in entries:
                if (
                    str(e.get("name", "")).lower() == name.lower()
                    and _version_key(str(e.get("version", "0")))
                    > _version_key(str(installed["version"]))
                    and (
                        best is None
                        or _version_key(str(e.get("version")))
                        > _version_key(str(best.get("version")))
                    )
                ):
                    best, best_src = e, src
        if not best:
            return ToolOutput(text=f"{name} is up to date (v{installed['version']}).")
        try:
            pkg = fetch_package(best, best_src)
        except (ValueError, RuntimeError, OSError) as e:
            return ToolOutput(text=f"Error: {e}", error=True)
        fid = stash_pending(pkg)
        text = (
            f"⬆️ Update available: {name} v{installed['version']} → v{pkg.version}\n\n"
            + review_text(pkg)
            + f"\n\nApply with: SkillInstall(fetch_id={fid!r})"
        )
        return ToolOutput(
            text=text,
            title=f"Update: {name}",
            metadata={"fetch_id": fid, "name": name, "version": pkg.version},
        )


class SkillRemoveTool(ToolBase):
    name = "SkillRemove"
    description = "Uninstall a marketplace skill from your personal skills."
    aliases = ["UninstallSkill"]
    permission_level = "REQUIRES_APPROVAL"
    parameters = {"name": {"type": "string", "description": "Installed skill name."}}

    async def execute(self, name: str) -> ToolOutput:
        store = get_store()
        if not store.get_installed(name):
            return ToolOutput(text=f"Error: {name!r} is not installed.", error=True)
        remove_installed(name, store)
        from tools.skill_tools import _skill_cache_invalidate

        _skill_cache_invalidate()
        return ToolOutput(text=f"🗑️ Removed {name}.")


class SkillPublishTool(ToolBase):
    name = "SkillPublish"
    description = (
        "Package one of your skills for sharing: builds a portable folder with "
        "the skill file plus the registry JSON entry to paste into any hosted "
        "registry.json. Tell the user the folder path and the entry."
    )
    permission_level = "NORMAL"
    parameters = {"name": {"type": "string", "description": "Skill name (bundled or installed)."}}

    async def execute(self, name: str) -> ToolOutput:
        import json as _json

        try:
            pack = publish_pack(name)
        except ValueError as e:
            return ToolOutput(text=f"Error: {e}", error=True)
        return ToolOutput(
            text=f"📤 Share pack ready at {pack['pack_dir']}\n\n"
            f"1. Host {pack['skill']}.md anywhere (GitHub repo, gist, your site).\n"
            f'2. Add this entry to your registry.json\'s "skills" list:\n'
            f"{_json.dumps(pack['entry'], indent=2)}\n"
            f"3. Share the registry URL — others add it with SkillSourceAdd and install with SkillFetch.",
            title=f"Publish {pack['skill']}",
            metadata={"pack_dir": pack["pack_dir"], "entry": pack["entry"]},
        )


class SkillSourcesTool(ToolBase):
    name = "SkillSources"
    description = "List the registry sources the marketplace searches (bundled + community)."
    permission_level = "ALWAYS_ALLOW"
    parameters = {}

    async def execute(self) -> ToolOutput:
        sources = get_store().sources()
        lines = [
            f"• {s.name}: {s.url}" + (" (remote)" if s.is_remote else " (local)") for s in sources
        ]
        return ToolOutput(text="Registry sources:\n" + "\n".join(lines) if lines else "No sources.")


class SkillSourceAddTool(ToolBase):
    name = "SkillSourceAdd"
    description = (
        "Add a community registry source (URL or local path to a registry.json). "
        "The registry is validated immediately — typos fail fast."
    )
    permission_level = "REQUIRES_APPROVAL"
    parameters = {
        "name": {"type": "string", "description": "Short name for the source."},
        "url": {"type": "string", "description": "https://… or /path/to/registry.json"},
    }

    async def execute(self, name: str, url: str) -> ToolOutput:
        try:
            get_store().add_source(name, url)
        except (ValueError, RuntimeError, OSError) as e:
            return ToolOutput(text=f"Error: {e}", error=True)
        return ToolOutput(text=f"✅ Registry source {name!r} added.")


class SkillSourceRemoveTool(ToolBase):
    name = "SkillSourceRemove"
    description = "Remove a community registry source (the bundled source can't be removed)."
    permission_level = "REQUIRES_APPROVAL"
    parameters = {"name": {"type": "string", "description": "Source name."}}

    async def execute(self, name: str) -> ToolOutput:
        try:
            ok = get_store().remove_source(name)
        except ValueError as e:
            return ToolOutput(text=f"Error: {e}", error=True)
        return ToolOutput(
            text=f"Removed {name!r}." if ok else f"Error: unknown source {name!r}.", error=not ok
        )


for _t in (
    SkillSearchTool(),
    SkillInfoTool(),
    SkillFetchTool(),
    SkillInstallTool(),
    SkillUpdateTool(),
    SkillRemoveTool(),
    SkillPublishTool(),
    SkillSourcesTool(),
    SkillSourceAddTool(),
    SkillSourceRemoveTool(),
):
    register_tool(_t)
