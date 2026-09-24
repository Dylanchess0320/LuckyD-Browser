#!/usr/bin/env python3
"""Prepare a LuckyD Browser release: bump the version everywhere.

Usage:
    python scripts/prepare_release.py 10.6.0 --whats-new "One-line headline" [--notes-file notes.md]

This rewrites, in one pass:
  - pyproject.toml                        (project version)
  - browser/__init__.py                   (__version__ + WHATS_NEW banner)
  - browser/version_info.txt              (Windows filevers/prodvers + strings)
  - browser/installer/LuckyDBrowser.iss   (AppVersion, VersionInfo*, OutputBaseFilename)
  - browser/browser_core/ai_bridge.py     (User-Agent LuckyDBrowser/M.m)
  - main.py, ui.py, acp_server.py,
    browser/browser_core/terminal_server.py (vX.Y.Z display strings)
  - tests/test_security_40.py             (version assertions + stale guards)
  - tests/test_cov_ai_bridge.py,
    tests/test_cov_terminal_server.py   (version assertions)
  - docs/docs/index.md, docs/docs/release-notes.md (download links + "New in")
  - README.md                             ("What's new" section)
  - CHANGELOG.md                          (new section under [Unreleased])

Safety: every replacement is validated against the current tree BEFORE any
file is written. If any expected string/pattern is missing, the script
aborts with no files modified (atomic failure). Multiple edits to the same
file are applied in order to one in-memory copy.

Run from the repo root. Review the diff, run the tests, then commit.
"""

import argparse
import datetime
import re
import sys
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read_version() -> str:
    m = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M)
    if not m:
        sys.exit("could not find version in pyproject.toml")
    return m.group(1)


# Each op is (path, kind, old, new, count, description); kind is "lit" or "re".
# Planned first, validated all-at-once, written all-at-once.
OPS: list = []


def plan_sub(path: str, old: str, new: str, count: int = 0) -> None:
    OPS.append((path, "lit", old, new, count, f"{old!r} -> {new!r}"))


def plan_sub_re(path: str, pattern: str, repl: str, count: int = 0) -> None:
    OPS.append((path, "re", pattern, repl, count, f"/{pattern}/"))


def wrap_banner(version: str, headline: str) -> str:
    """Build the WHATS_NEW tuple in the repo's wrapped style."""
    text = f"LuckyD v{version} - {headline}"
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > 72:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    lines.append(cur)
    body = "\n".join(f'    "{ln} "' for ln in lines)
    return f"WHATS_NEW = (\n{body}\n)"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("version", help="new version, e.g. 10.6.0")
    ap.add_argument("--whats-new", required=True, help="one-line headline for README/WHATS_NEW")
    ap.add_argument("--notes-file", help="file with longer release notes for CHANGELOG")
    ap.add_argument("--docs-title", help="short title for the docs 'New in X' section")
    ap.add_argument("--docs-body", help="one-paragraph body for the docs 'New in X' section")
    args = ap.parse_args()

    new = args.version
    if not re.fullmatch(r"\d+\.\d+\.\d+", new):
        sys.exit("version must look like 10.6.0")
    old = read_version()
    if old == new:
        sys.exit(f"already at {new}")
    headline = args.whats_new.strip()
    old_mm = ".".join(old.split(".")[:2])
    new_mm = ".".join(new.split(".")[:2])
    old_quad = old + ".0"
    new_quad = new + ".0"
    old_tup = "(" + ", ".join(old.split(".")) + ", 0)"
    new_tup = "(" + ", ".join(new.split(".")) + ", 0)"
    today = datetime.date.today().isoformat()
    print(f"bumping {old} -> {new}")

    plan_sub("pyproject.toml", f'version = "{old}"', f'version = "{new}"', 1)
    plan_sub("browser/__init__.py", f'__version__ = "{old}"', f'__version__ = "{new}"', 1)
    # (?s): WHATS_NEW spans multiple lines; .*? must match newlines too.
    plan_sub_re(
        "browser/__init__.py", r"(?s)WHATS_NEW = \(.*?\)\n", wrap_banner(new, headline) + "\n"
    )

    plan_sub("browser/version_info.txt", f"filevers={old_tup}", f"filevers={new_tup}")
    plan_sub("browser/version_info.txt", f"prodvers={old_tup}", f"prodvers={new_tup}")
    plan_sub("browser/version_info.txt", f"'{old_quad}'", f"'{new_quad}'")

    iss = "browser/installer/LuckyDBrowser.iss"
    plan_sub(iss, f'#define AppVersion   "{old}"', f'#define AppVersion   "{new}"')
    plan_sub(iss, f"VersionInfoVersion={old_quad}", f"VersionInfoVersion={new_quad}")
    plan_sub(iss, f"VersionInfoProductVersion={old_quad}", f"VersionInfoProductVersion={new_quad}")
    plan_sub(
        iss,
        f"OutputBaseFilename=LuckyDBrowserSetup-{old}",
        f"OutputBaseFilename=LuckyDBrowserSetup-{new}",
    )
    plan_sub(
        iss,
        f"browser\\installer\\output\\LuckyDBrowserSetup-{old}.exe",
        f"browser\\installer\\output\\LuckyDBrowserSetup-{new}.exe",
    )

    plan_sub(
        "browser/browser_core/ai_bridge.py", f"LuckyDBrowser/{old_mm}", f"LuckyDBrowser/{new_mm}"
    )
    for f in ("main.py", "ui.py", "acp_server.py", "browser/browser_core/terminal_server.py"):
        plan_sub(f, f"v{old}", f"v{new}")

    t = "tests/test_security_40.py"
    plan_sub(t, f'browser.__version__ == "{old}"', f'browser.__version__ == "{new}"')
    plan_sub(t, f'["project"]["version"] == "{old}"', f'["project"]["version"] == "{new}"')
    plan_sub(t, f'"LuckyDBrowser/{old_mm}" in ai_bridge', f'"LuckyDBrowser/{new_mm}" in ai_bridge')
    plan_sub(t, f'"{old_quad}" in version_info', f'"{new_quad}" in version_info')
    plan_sub_re(
        t,
        r'assert "\d+\.\d+\.\d+\.\d+" not in version_info',
        f'assert "{old_quad}" not in version_info',
    )
    plan_sub(t, f"filevers={old_tup}", f"filevers={new_tup}")
    plan_sub(t, f"prodvers={old_tup}", f"prodvers={new_tup}")
    plan_sub(t, f'#define AppVersion   "{old}"', f'#define AppVersion   "{new}"')
    plan_sub(
        t,
        f"OutputBaseFilename=LuckyDBrowserSetup-{old}",
        f"OutputBaseFilename=LuckyDBrowserSetup-{new}",
    )
    plan_sub(t, f"VersionInfoVersion={old_quad}", f"VersionInfoVersion={new_quad}")
    plan_sub(t, f"VersionInfoProductVersion={old_quad}", f"VersionInfoProductVersion={new_quad}")

    # Other test files that assert the current version (historical "10.4"
    # mentions in comments/docstrings are left alone).
    plan_sub(
        "tests/test_cov_ai_bridge.py", f'"LuckyDBrowser/{old_mm}"', f'"LuckyDBrowser/{new_mm}"'
    )
    plan_sub(
        "tests/test_cov_terminal_server.py",
        f"LUCKYD_AGENT_VERSION=v{old}",
        f"LUCKYD_AGENT_VERSION=v{new}",
    )

    # Docs site (docs/docs/): download links must track the release, enforced
    # by tests/test_swarm_docs.py::test_installer_exe_name_matches_iss.
    idx = "docs/docs/index.md"
    plan_sub(idx, f"Download v{old}", f"Download v{new}")
    plan_sub(idx, f"/releases/download/v{old}/", f"/releases/download/v{new}/")
    plan_sub(idx, f"LuckyDBrowserSetup-{old}.exe", f"LuckyDBrowserSetup-{new}.exe")
    if args.docs_title and args.docs_body:
        plan_sub_re(
            idx,
            r"## New in \d+\.\d+\.\d+",
            f"## New in {new} — {args.docs_title}\n\n{args.docs_body}\n\n\\g<0>",
            1,
        )
        dl = f"https://github.com/Dylanchess0320/LuckyD-Browser/releases/download/v{new}"
        bullets = ""
        if args.notes_file:
            raw = (ROOT / args.notes_file).read_text(encoding="utf-8")
            lines = [ln for ln in raw.splitlines() if not ln.lstrip().startswith("###")]
            bullets = "\n".join(lines).strip() + "\n"
        section = (
            f"## [{new}] — {args.docs_title} — {today}\n\n"
            f"**[`LuckyDBrowserSetup-{new}.exe`]({dl}/LuckyDBrowserSetup-{new}.exe)**"
            " — Windows 10/11 x64 · per-user install · no admin needed.\n"
            f"**[`LuckyDBrowser-Portable-{new}.zip`]({dl}/LuckyDBrowser-Portable-{new}.zip)**"
            " — unzip and run, for locked-down PCs.\n"
            f"\n{bullets}\n"
            f"On top of everything from {old} below.\n\n"
        )
        plan_sub(
            "docs/docs/release-notes.md",
            "# LuckyD Browser — Release Notes\n\n",
            "# LuckyD Browser — Release Notes\n\n" + section,
            1,
        )

    # Install guides: exe names + download URLs must track the release.
    for doc in ("docs/docs/getting-started.md", "README-LuckyD-Browser.md"):
        plan_sub(doc, f"LuckyDBrowserSetup-{old}.exe", f"LuckyDBrowserSetup-{new}.exe")
        plan_sub(doc, f"/releases/download/v{old}/", f"/releases/download/v{new}/")

    # README "What's new" section — skip if the new version is already documented.
    readme = ROOT / "README.md"
    if f"**v{new}**" not in readme.read_text(encoding="utf-8"):
        plan_sub_re(
            "README.md",
            r"\*\*v" + re.escape(old) + r"\*\* — .*?(?=\n\n)",
            f"**v{new}** — {headline}",
        )
    else:
        print(f"  README.md: **v{new}** already documented, skipping")

    # CHANGELOG entry
    cl = ROOT / "CHANGELOG.md"
    notes = ""
    if args.notes_file:
        notes = (ROOT / args.notes_file).read_text(encoding="utf-8").strip() + "\n"
    entry = f"## [{new}] - {today}\n\n{headline.rstrip('.')}.\n"
    if notes:
        entry += f"\n{notes}"
    entry += "\n"
    s = cl.read_text(encoding="utf-8")
    marker = "## [Unreleased]\n"
    if marker not in s:
        sys.exit("## [Unreleased] marker not found in CHANGELOG.md -- aborting (no files modified)")
    plan_sub("CHANGELOG.md", marker, marker + "\n" + entry, 1)

    # Docs-site changelog mirror gets the same new section as CHANGELOG.md.
    plan_sub_re(
        "docs/docs/changelog.md",
        r"## \[\d+\.\d+\.\d+\]",
        entry + r"\g<0>",
        1,
    )

    # Phase 1: validate every op, threading multiple edits to the same file
    # through one in-memory copy. Any miss aborts BEFORE a single write.
    by_file: OrderedDict[str, list] = OrderedDict()
    for op in OPS:
        by_file.setdefault(op[0], []).append(op)
    planned = []
    for path, ops in by_file.items():
        p = ROOT / path
        text = p.read_text(encoding="utf-8")
        descs = []
        for _, kind, old_pat, new_text, count, desc in ops:
            if kind == "lit":
                if old_pat not in text:
                    sys.exit(
                        f"expected {old_pat!r} not found in {path} -- aborting (no files modified)"
                    )
                text = text.replace(old_pat, new_text, count if count else -1)
            else:
                text, n = re.subn(old_pat, new_text, text, count=count if count else 0)
                if not n:
                    sys.exit(
                        f"pattern {old_pat!r} not found in {path} -- aborting (no files modified)"
                    )
            descs.append(desc)
        planned.append((p, path, text, descs))

    # Phase 2: all validations passed — write everything.
    for p, path, text, descs in planned:
        p.write_text(text, encoding="utf-8")
        for d in descs:
            print(f"  {path}: {d}")

    print(f"\ndone: {old} -> {new}. Review with git diff, run pytest + ruff, then commit.")


if __name__ == "__main__":
    main()
