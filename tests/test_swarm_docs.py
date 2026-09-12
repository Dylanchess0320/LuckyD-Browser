"""Docs accuracy checks for the overnight hardening swarm (docs worker).

Verifies that ``docs/`` matches reality:
- internal relative markdown links resolve to real files,
- no stale "5.x" references describe the *current* release (6.0),
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
# 2. no stale version references describing the current release
# ---------------------------------------------------------------------------


# docs/docs/changelog.md keeps a historical "## [X.Y.Z] - ..." section per
# release; only the current version's section describes the live product.
def _current_version() -> str:
    import ast

    tree = ast.parse((REPO / "browser" / "__init__.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("browser/__init__.py has no __version__")


_VERSION_HEADING = re.compile(r"## \[(\d+\.\d+\.\d+)\]")


def _iter_live_lines(path: Path) -> list[tuple[int, str]]:
    """Lines of a docs-site page, skipping archived "## [old] - ..." sections."""
    current = _current_version()
    out: list[tuple[int, str]] = []
    in_old_section = False
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        hm = _VERSION_HEADING.match(line)
        if hm:
            in_old_section = hm.group(1) != current
            continue
        if not in_old_section:
            out.append((lineno, line))
    return out


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


# Lines matching these patterns talk *about* 5.0 historically (features
# introduced in 5.0, the 5.0.0 changelog section, "New in 5.0" blurbs).
HISTORICAL_50 = re.compile(
    r"\(5\.0\)|in 5\.0|from 5\.0|since 5\.0|5\.0\.0\]|5\.0\.0 —|5\.0\.0 -|"
    r"New in 5\.0|everything (in|from) 5\.0|"
    r"RELEASE_NOTES_5\.0|Unified in 5\.0|Final",
    re.IGNORECASE,
)
# (?<![\d.]) keeps "1.5.0" / "2.5.0"-style versions from matching.
STALE_50 = re.compile(r"(?<![\d.])5\.0(\.0)?(?![\d.])")


def test_no_stale_5x_for_current_release():
    # Only the live docs site describes the current release; root-level
    # CHANGELOG.md / RELEASE_NOTES_*.md are historical archives by design.
    stale: list[str] = []
    for md in sorted(DOCS_SITE.glob("*.md")):
        for lineno, line in _iter_live_lines(md):
            if STALE_50.search(line) and not HISTORICAL_50.search(line):
                stale.append(f"{md.relative_to(REPO)}:{lineno}: {line.strip()}")
    assert not stale, "stale 5.x refs about the current release:\n" + "\n".join(stale)


def test_current_release_docs_say_6x():
    for page in ("index.md", "getting-started.md", "release-notes.md"):
        text = (DOCS_SITE / page).read_text(encoding="utf-8")
        assert "8.0.0" in text, f"docs/docs/{page} never mentions 8.0.0"


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
    vm = re.search(r'#define AppVersion\s+"([\d.]+)"', iss)
    assert vm, "AppVersion not found in LuckyDBrowser.iss"
    current_version = vm.group(1)
    # Historical release-note archives name their own era's installer.
    historical = {
        "RELEASE_NOTES.md",
        "RELEASE_NOTES_3.9.md",
        "RELEASE_NOTES_4.0.md",
        "RELEASE_NOTES_5.0.md",
        "RELEASE_NOTES_6.0.md",
        "RELEASE_NOTES_7.0.md",
    }
    # Changelog sections for older versions ("## [5.0.0] - ...") name their
    # own era's installer too; only the current version's section is checked.
    version_heading = re.compile(r"## \[(\d+\.\d+\.\d+)\]")
    for md in iter_md_files():
        if md.name in historical:
            continue
        in_old_section = False
        for line in md.read_text(encoding="utf-8").splitlines():
            hm = version_heading.match(line)
            if hm:
                in_old_section = hm.group(1) != current_version
                continue
            if in_old_section:
                continue
            for found in re.findall(r"LuckyDBrowserSetup-[\d.]+\.exe", line):
                assert found == exe, (
                    f"{md.relative_to(REPO)} names {found} but the .iss builds {exe}"
                )


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
