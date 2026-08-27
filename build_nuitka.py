#!/usr/bin/env python3
"""
LuckyD Code v3.6 — Nuitka Standalone Compiler Script.

Compiles the interactive LuckyD Code CLI into a high-performance native Windows executable.
Usage:
    python build_nuitka.py
    python build_nuitka.py --onefile
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent


def build_nuitka(onefile: bool = True) -> int:
    print("=" * 60)
    print(" Compiling LuckyD Code v3.6 with Nuitka (Native Build)")
    print("=" * 60)

    icon_path = REPO_ROOT / "browser" / "assets" / "professional_icon.ico"
    if not icon_path.exists():
        icon_path = REPO_ROOT / "assets" / "professional_icon.ico"

    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--standalone" if not onefile else "--onefile",
        "--assume-yes-for-downloads",
        "--output-dir=dist",
        "--output-filename=luckyd-cli.exe",
        "--include-package-data=browser",
        "--include-package-data=core",
        "--include-package-data=tools",
        "--include-package-data=memory",
        "--include-package-data=project",
        "--include-package-data=features",
        "--include-package-data=llm",
        "--follow-imports",
        "main.py",
    ]

    if icon_path.exists() and sys.platform == "win32":
        cmd.append(f"--windows-icon-from-ico={icon_path}")

    print(f"Running command: {' '.join(cmd)}")
    env = os.environ.copy()
    env["LUCKYD_AGENT_VERSION"] = "v3.6.0"
    env["LUCKYD_AGENT_NAME"] = "Agent 1"

    result = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env)
    if result.returncode == 0:
        print("\n[SUCCESS] LuckyD Code v3.6 Nuitka build completed successfully in dist/")
        dist_exe = REPO_ROOT / "dist" / "luckyd-cli.exe"
        if dist_exe.exists():
            import shutil

            shutil.copy2(dist_exe, REPO_ROOT / "luckyd-cli.exe")
            shutil.copy2(dist_exe, REPO_ROOT / "luckyd-code-v3.6.exe")
            print(
                f"[OK] Refreshed {REPO_ROOT / 'luckyd-cli.exe'} and {REPO_ROOT / 'luckyd-code-v3.6.exe'}"
            )
    else:
        print(f"\n[ERROR] Nuitka build exited with code {result.returncode}")
    return result.returncode


if __name__ == "__main__":
    is_onefile = "--standalone" not in sys.argv
    sys.exit(build_nuitka(onefile=is_onefile))
