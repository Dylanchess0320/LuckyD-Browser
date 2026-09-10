"""Docs accuracy checks for the overnight hardening swarm (docs worker).

Verifies that ``docs/`` matches reality:
- internal relative markdown links resolve to real files,
- no stale "4.x" references describe the *current* release (5.0),
- shell commands / referenced files in the docs actually exist,
- documented code behavior (auth model, ports, installer name) matches the code.

Run only this file: ``python -m pytest tests/test_swarm_docs.py -q -p no:cacheprovider``
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"
DOCS_SITE = DOCS / "docs"

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def iter_md_files() -> list[Path]:
    return sorted(p for p in DOCS.rglob("*.md") if p.is_file())


MD_LINK_RE = re.compile(r"\]\(([^)\s]+)\)")


def relative_md_links(path: Path) -> list[str]:
    """Return relative (non-http, non-anchor, non-mailto) markdown link targets."""
    targets: list[str] = []
    for m in MD_LINK_RE.finditer(path.read_text(encoding="utf-8")):
        target = m.group(1)
        if target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        # strip any anchor
        target = target.split("#", 1)[0]
        if target.endswith(".md"):
            targets.append(target)
    return targets


# ---------------------------------------------------------------------------
# 1. internal links resolve
# ---------------------------------------------------------------------------


def test_internal_md_links_resolve():
    broken: list[str] = []
    for md in iter_md_files():
        for target in relative_md_links(md):
            resolved = (md.parent / target).resolve()
            if not resolved.is_file():
                broken.append(f"{md.relative_to(REPO)} -> {target}")
    assert not broken, "broken internal doc links:\n" + "\n".join(broken)


def test_mkdocs_nav_pages_exist():
    import yaml  # type: ignore[import-not-found]

    nav = yaml.safe_load((REPO / "mkdocs.yml").read_text(encoding="utf-8"))["nav"]
    missing: list[str] = []
    for entry in nav:
        for _title, page in entry.items():
            if not (DOCS_SITE / page).is_file():
                missing.append(page)
    assert not missing, "mkdocs nav pages missing: " + ", ".join(missing)


# ---------------------------------------------------------------------------
# 2. no stale 4.x references describing the current release
# ---------------------------------------------------------------------------

# Lines matching these patterns talk *about* 4.0 historically (features
# introduced in 4.0, the 4.0.0 changelog section, "New in 4.0" blurbs).
HISTORICAL_40 = re.compile(
    r"\(4\.0\)|in 4\.0|from 4\.0|since 4\.0|4\.0\.0\]|4\.0\.0 —|"
    r"4\.0 (security|auth model)|New in 4\.0|everything (in|from) 4\.0|"
    r"RELEASE_NOTES_4\.0|Hardened in 4\.0|Authentication required|Frontier",
    re.IGNORECASE,
)
# (?<![\d.]) keeps "1.4.0" / "2.4.0" historical browser versions from matching.
STALE_40 = re.compile(r"(?<![\d.])4\.0(\.0)?(?![\d.])")


def test_no_stale_4x_for_current_release():
    # Only the live docs site describes the current release; root-level
    # CHANGELOG.md / RELEASE_NOTES_*.md are historical archives by design.
    stale: list[str] = []
    for md in sorted(DOCS_SITE.glob("*.md")):
        for lineno, line in enumerate(md.read_text(encoding="utf-8").splitlines(), start=1):
            if STALE_40.search(line) and not HISTORICAL_40.search(line):
                stale.append(f"{md.relative_to(REPO)}:{lineno}: {line.strip()}")
    assert not stale, "stale 4.x refs about the current release:\n" + "\n".join(stale)


def test_current_release_docs_say_5x():
    for page in ("index.md", "getting-started.md", "release-notes.md"):
        text = (DOCS_SITE / page).read_text(encoding="utf-8")
        assert "5.0.0" in text, f"docs/docs/{page} never mentions 5.0.0"


# ---------------------------------------------------------------------------
# 3. commands / files referenced in docs exist
# ---------------------------------------------------------------------------


def test_getting_started_build_inputs_exist():
    for rel in (
        "browser/requirements.txt",
        "browser/run_browser.bat",
        "browser/installer/build_installer.ps1",
    ):
        assert (REPO / rel).is_file(), f"docs reference missing file: {rel}"


def test_installer_exe_name_matches_iss():
    iss = (REPO / "browser/installer/LuckyDBrowser.iss").read_text(encoding="utf-8")
    m = re.search(r"OutputBaseFilename=(LuckyDBrowserSetup-[\d.]+)", iss)
    assert m, "OutputBaseFilename not found in LuckyDBrowser.iss"
    exe = m.group(1) + ".exe"
    # Historical release-note archives name their own era's installer.
    historical = {"RELEASE_NOTES.md", "RELEASE_NOTES_3.9.md", "RELEASE_NOTES_4.0.md"}
    for md in iter_md_files():
        if md.name in historical:
            continue
        text = md.read_text(encoding="utf-8")
        for found in re.findall(r"LuckyDBrowserSetup-[\d.]+\.exe", text):
            assert found == exe, f"{md.relative_to(REPO)} names {found} but the .iss builds {exe}"


def test_promo_kit_screenshots_exist():
    text = (DOCS / "PROMO_KIT.md").read_text(encoding="utf-8")
    for shot in re.findall(r"docs/screenshots/(\S+?\.(?:png|svg))", text):
        assert (DOCS / "screenshots" / shot).is_file(), f"screenshot missing: {shot}"


def test_skill_loader_path_in_luckyd_rules():
    text = (DOCS / "LUCKYD.md").read_text(encoding="utf-8")
    assert "tools/skill_tools.py" in text
    assert (REPO / "tools/skill_tools.py").is_file()


def test_no_reference_to_missing_external_dir():
    text = (DOCS / "HYBRID_EDIT_STRATEGY.md").read_text(encoding="utf-8")
    assert "external/" not in text, (
        "HYBRID_EDIT_STRATEGY.md references external/ which does not exist in the repo"
    )


# ---------------------------------------------------------------------------
# 4. documented behavior matches the code
# ---------------------------------------------------------------------------


def test_dashboard_requires_auth_401():
    src = (REPO / "browser/browser_core/control_server.py").read_text(encoding="utf-8")
    assert '"/dashboard"' in src, "Control API has no /dashboard route"
    assert "_unauthorized_html(), 401" in src, "nav auth no longer returns 401"


def test_documented_service_ports_match_code():
    checks = {
        "browser/browser_core/control_server.py": "DEFAULT_PORT = 9777",
        "browser/browser_core/terminal_server.py": "DEFAULT_PORT = 9881",
        "cline_bridge.py": '"8317"',
        "web_server.py": "default=8000",
    }
    for rel, needle in checks.items():
        src = (REPO / rel).read_text(encoding="utf-8")
        assert needle in src, f"{rel} no longer contains {needle!r} (docs cite it)"


def test_security_curl_example_avoids_powershell_alias():
    text = (DOCS_SITE / "security.md").read_text(encoding="utf-8")
    assert "curl.exe http://127.0.0.1:9777/dashboard" in text, (
        "security.md verify example should use curl.exe (bare `curl` is "
        "Invoke-WebRequest on Windows PowerShell)"
    )


def test_deep_research_claims_match_code():
    src = (REPO / "tools/deep_research_tool.py").read_text(encoding="utf-8")
    for preset in ('"quick"', '"standard"', '"deep"', '"max"'):
        assert preset in src, f"depth preset {preset} missing from deep_research_tool.py"
    cache = (REPO / "features/deep_research/tools/cache.py").read_text(encoding="utf-8")
    assert "sqlite3" in cache, "SQLite result cache claimed by docs not found"
