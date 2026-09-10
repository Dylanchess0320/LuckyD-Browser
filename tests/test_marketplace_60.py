"""
Marketplace tests — registries, integrity, install flow, tools.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import core.trust as trust_mod
from core.marketplace import (
    MarketplaceStore,
    RegistrySource,
    fetch_package,
    fetch_registry,
    install_package,
    parse_skill_markdown,
    remove_installed,
    review_text,
    sha256_text,
    stash_pending,
    take_pending,
)

REPO = Path(__file__).resolve().parent.parent

SAMPLE_MD = """---
name: test-skill
description: A skill used by tests
version: "2.1"
author: Test Author
tags: [demo, tests]
requires_tools: [WebSearch]
---

# Test Skill

Do the thing.
"""


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / ".luckyd"
    d.mkdir()
    monkeypatch.setattr(trust_mod, "_data_dir", lambda: d)
    return d


@pytest.fixture()
def store(data_dir):
    return MarketplaceStore(data_dir / "marketplace.db")


@pytest.fixture()
def file_registry(tmp_path):
    """A local registry.json + skill file, simulating a community registry."""
    reg_dir = tmp_path / "community"
    reg_dir.mkdir()
    (reg_dir / "cool-skill.md").write_text(SAMPLE_MD, encoding="utf-8")
    digest = hashlib.sha256(SAMPLE_MD.encode()).hexdigest()
    registry = {
        "registry": "community",
        "skills": [
            {
                "name": "test-skill",
                "description": "A skill used by tests",
                "version": "2.1",
                "author": "Test Author",
                "tags": ["demo"],
                "url": "cool-skill.md",
                "sha256": digest,
            }
        ],
    }
    reg_path = reg_dir / "registry.json"
    reg_path.write_text(json.dumps(registry), encoding="utf-8")
    return reg_path


# ── parsing ───────────────────────────────────────────────────────────────


class TestParse:
    def test_valid(self):
        p = parse_skill_markdown(SAMPLE_MD, "x.md")
        assert p["name"] == "test-skill"
        assert p["version"] == "2.1"
        assert p["author"] == "Test Author"
        assert p["tags"] == ["demo", "tests"]
        assert p["requires_tools"] == ["WebSearch"]
        assert "Do the thing." in p["prompt"]

    def test_no_frontmatter(self):
        assert parse_skill_markdown("# just markdown", "x.md") is None

    def test_defaults(self):
        p = parse_skill_markdown("---\nname: bare\n---\n\nbody\n", "bare.md")
        assert p["version"] == "1.0"
        assert p["author"] == "unknown"

    def test_sha256(self):
        assert sha256_text("abc") == hashlib.sha256(b"abc").hexdigest()


# ── bundled registry integrity ────────────────────────────────────────────


class TestBundledRegistry:
    def test_hashes_match_files(self):
        """The shipped registry.json must honestly describe the bundled skills."""
        reg = json.loads((REPO / "skills" / "registry.json").read_text(encoding="utf-8"))
        assert reg["skills"], "bundled registry is empty"
        for entry in reg["skills"]:
            md = (REPO / "skills" / entry["url"]).read_text(encoding="utf-8")
            assert sha256_text(md) == entry["sha256"], f"stale hash for {entry['name']}"

    def test_fetch_bundled_verifies(self, store):
        bundled = next(s for s in store.sources() if s.name == "bundled")
        entries = fetch_registry(bundled)
        assert len(entries) >= 4
        pkg = fetch_package(entries[0], bundled)
        assert pkg.verified
        assert pkg.name == entries[0]["name"]


# ── integrity enforcement ─────────────────────────────────────────────────


class TestIntegrity:
    def test_tampered_hash_refused(self, store, file_registry):
        src = RegistrySource("community", str(file_registry))
        (entry,) = fetch_registry(src)
        entry["sha256"] = "0" * 64
        with pytest.raises(ValueError, match="INTEGRITY FAILURE"):
            fetch_package(entry, src)

    def test_missing_hash_warns_but_installs(self, store, file_registry, tmp_path):
        src = RegistrySource("community", str(file_registry))
        (entry,) = fetch_registry(src)
        del entry["sha256"]
        pkg = fetch_package(entry, src)
        assert not pkg.verified
        assert "no hash" in review_text(pkg)

    def test_unparseable_markdown_refused(self, store, tmp_path):
        reg_dir = tmp_path / "bad"
        reg_dir.mkdir()
        (reg_dir / "junk.md").write_text("no frontmatter here", encoding="utf-8")
        reg = {"registry": "bad", "skills": [{"name": "junk", "url": "junk.md"}]}
        reg_path = reg_dir / "registry.json"
        reg_path.write_text(json.dumps(reg), encoding="utf-8")
        src = RegistrySource("bad", str(reg_path))
        (entry,) = fetch_registry(src)
        with pytest.raises(ValueError, match="could not parse"):
            fetch_package(entry, src)


# ── store: sources + manifest ─────────────────────────────────────────────


class TestStore:
    def test_bundled_source_present(self, store):
        names = [s.name for s in store.sources()]
        assert "bundled" in names

    def test_bundled_cannot_be_removed(self, store):
        with pytest.raises(ValueError, match="cannot be removed"):
            store.remove_source("bundled")

    def test_add_source_validates_eagerly(self, store, tmp_path):
        with pytest.raises(OSError):  # missing registry file
            store.add_source("bogus", str(tmp_path / "nope.json"))

    def test_add_remove_source_roundtrip(self, store, file_registry):
        store.add_source("community", str(file_registry))
        assert "community" in [s.name for s in store.sources()]
        assert store.remove_source("community")
        assert "community" not in [s.name for s in store.sources()]

    def test_install_manifest_roundtrip(self, store, file_registry):
        src = RegistrySource("community", str(file_registry))
        (entry,) = fetch_registry(src)
        pkg = fetch_package(entry, src)
        target = install_package(pkg, store)
        assert target.exists()
        manifest = store.get_installed("test-skill")
        assert manifest["version"] == "2.1"
        assert manifest["sha256"] == pkg.sha256
        assert manifest["source_name"] == "community"
        assert remove_installed("test-skill", store)
        assert not target.exists()
        assert store.get_installed("test-skill") is None


# ── pending fetch cache ───────────────────────────────────────────────────


class TestPending:
    def test_take_pops(self, store, file_registry):
        src = RegistrySource("community", str(file_registry))
        (entry,) = fetch_registry(src)
        pkg = fetch_package(entry, src)
        fid = stash_pending(pkg)
        assert take_pending(fid) is pkg
        assert take_pending(fid) is None

    def test_unknown_fetch_id(self):
        assert take_pending("nope1234") is None


# ── review ────────────────────────────────────────────────────────────────


class TestReview:
    def test_review_shows_key_facts(self, store, file_registry):
        src = RegistrySource("community", str(file_registry))
        (entry,) = fetch_registry(src)
        pkg = fetch_package(entry, src)
        text = review_text(pkg)
        for needle in (
            "test-skill",
            "2.1",
            "Test Author",
            "community",
            "hash verified",
            "WebSearch",
            pkg.sha256[:16],
        ):
            assert needle in text, needle


# ── agent tools ───────────────────────────────────────────────────────────


@pytest.fixture()
def market_tools(store, file_registry, monkeypatch):
    import tools.skill_market_tools as smt

    monkeypatch.setattr(smt, "_store", store)
    smt._registry_cache.clear()
    return smt


class TestMarketTools:
    async def test_search_finds_bundled(self, market_tools):
        out = await market_tools.SkillSearchTool().execute(query="movie")
        assert not out.error
        assert "movie-picker" in out.text

    async def test_search_empty_query_lists_all(self, market_tools):
        out = await market_tools.SkillSearchTool().execute(query="")
        assert out.metadata["count"] >= 4

    async def test_info(self, market_tools):
        out = await market_tools.SkillInfoTool().execute(name="ai-news-brief")
        assert not out.error
        assert "ai-news-brief" in out.text

    async def test_info_unknown(self, market_tools):
        out = await market_tools.SkillInfoTool().execute(name="nope")
        assert out.error

    async def test_fetch_install_remove_flow(self, market_tools, store, file_registry, data_dir):
        store.add_source("community", str(file_registry))
        fetch_out = await market_tools.SkillFetchTool().execute(name="test-skill")
        assert not fetch_out.error, fetch_out.text
        assert fetch_out.metadata["verified"]
        fid = fetch_out.metadata["fetch_id"]

        install_out = await market_tools.SkillInstallTool().execute(fetch_id=fid)
        assert not install_out.error, install_out.text
        assert (data_dir / "skills" / "test-skill.md").exists()

        # installed skills are visible to SkillRun
        from tools.skill_tools import SkillRunTool

        run_out = await SkillRunTool().execute(skill_name="test-skill")
        assert not run_out.error
        assert "Do the thing." in run_out.text

        remove_out = await market_tools.SkillRemoveTool().execute(name="test-skill")
        assert not remove_out.error
        assert not (data_dir / "skills" / "test-skill.md").exists()

    async def test_install_bad_fetch_id(self, market_tools):
        out = await market_tools.SkillInstallTool().execute(fetch_id="deadbeef")
        assert out.error

    async def test_update_detects_newer(self, market_tools, store, file_registry, tmp_path):
        store.add_source("community", str(file_registry))
        # install v1.0 first (registry will then advertise v2.1)
        src = RegistrySource("community", str(file_registry))
        (entry,) = fetch_registry(src)
        entry_v1 = dict(entry, version="1.0")
        pkg_v1 = fetch_package(entry_v1, src)
        install_package(pkg_v1, store)

        out = await market_tools.SkillUpdateTool().execute(name="test-skill")
        assert not out.error, out.text
        assert "2.1" in out.text
        # apply the update
        install_out = await market_tools.SkillInstallTool().execute(
            fetch_id=out.metadata["fetch_id"]
        )
        assert not install_out.error
        assert store.get_installed("test-skill")["version"] == "2.1"

    async def test_update_when_current(self, market_tools, store, file_registry):
        store.add_source("community", str(file_registry))
        src = RegistrySource("community", str(file_registry))
        (entry,) = fetch_registry(src)
        install_package(fetch_package(entry, src), store)
        out = await market_tools.SkillUpdateTool().execute(name="test-skill")
        assert "up to date" in out.text

    async def test_update_not_installed(self, market_tools):
        out = await market_tools.SkillUpdateTool().execute(name="ghost")
        assert out.error

    async def test_publish(self, market_tools, data_dir):
        out = await market_tools.SkillPublishTool().execute(name="ai-news-brief")
        assert not out.error, out.text
        pack_dir = Path(out.metadata["pack_dir"])
        assert (pack_dir / "ai-news-brief.md").exists()
        entry_file = pack_dir / "registry-entry.json"
        entry = json.loads(entry_file.read_text(encoding="utf-8"))
        assert entry["name"] == "ai-news-brief"
        assert len(entry["sha256"]) == 64

    async def test_publish_unknown(self, market_tools):
        out = await market_tools.SkillPublishTool().execute(name="ghost")
        assert out.error

    async def test_source_add_remove(self, market_tools, file_registry):
        out = await market_tools.SkillSourceAddTool().execute(
            name="community", url=str(file_registry)
        )
        assert not out.error, out.text
        sources_out = await market_tools.SkillSourcesTool().execute()
        assert "community" in sources_out.text
        rm_out = await market_tools.SkillSourceRemoveTool().execute(name="community")
        assert not rm_out.error

    async def test_permission_levels(self):
        import tools.skill_market_tools as smt
        from core.approval_hook import TOOL_PERMISSIONS, ToolPermissionLevel

        assert TOOL_PERMISSIONS["SkillSearch"] is ToolPermissionLevel.ALWAYS_ALLOW
        assert TOOL_PERMISSIONS["SkillInfo"] is ToolPermissionLevel.ALWAYS_ALLOW
        assert TOOL_PERMISSIONS["SkillFetch"] is ToolPermissionLevel.ALWAYS_ALLOW
        assert TOOL_PERMISSIONS["SkillSources"] is ToolPermissionLevel.ALWAYS_ALLOW
        assert TOOL_PERMISSIONS["SkillInstall"] is ToolPermissionLevel.REQUIRES_APPROVAL
        assert TOOL_PERMISSIONS["SkillUpdate"] is ToolPermissionLevel.REQUIRES_APPROVAL
        assert TOOL_PERMISSIONS["SkillRemove"] is ToolPermissionLevel.REQUIRES_APPROVAL
        assert TOOL_PERMISSIONS["SkillPublish"] is ToolPermissionLevel.NORMAL
        assert TOOL_PERMISSIONS["SkillSourceAdd"] is ToolPermissionLevel.REQUIRES_APPROVAL
        assert TOOL_PERMISSIONS["SkillSourceRemove"] is ToolPermissionLevel.REQUIRES_APPROVAL
        for tool in (
            smt.SkillSearchTool(),
            smt.SkillInfoTool(),
            smt.SkillFetchTool(),
            smt.SkillInstallTool(),
            smt.SkillUpdateTool(),
            smt.SkillRemoveTool(),
            smt.SkillPublishTool(),
            smt.SkillSourcesTool(),
            smt.SkillSourceAddTool(),
            smt.SkillSourceRemoveTool(),
        ):
            assert TOOL_PERMISSIONS[tool.name] is getattr(
                ToolPermissionLevel, tool.permission_level
            ), tool.name

    async def test_trust_scopes(self):
        from core.trust import TOOL_SCOPE_OVERRIDES

        for name in (
            "SkillSearch",
            "SkillInfo",
            "SkillFetch",
            "SkillInstall",
            "SkillUpdate",
            "SkillRemove",
            "SkillPublish",
            "SkillSources",
            "SkillSourceAdd",
            "SkillSourceRemove",
        ):
            assert TOOL_SCOPE_OVERRIDES[name] == "agents", name


# ── skill_tools integration ───────────────────────────────────────────────


class TestSkillToolsIntegration:
    async def test_user_dir_visible_in_list(self, data_dir):
        from tools.skill_tools import SkillListTool, _skills_dirs

        (data_dir / "skills").mkdir(parents=True, exist_ok=True)
        (data_dir / "skills" / "mine.md").write_text(SAMPLE_MD, encoding="utf-8")
        assert any("mine" not in str(d) for d in _skills_dirs())  # sanity
        out = await SkillListTool().execute()
        assert "test-skill" in out.text
        assert "(installed)" in out.text
