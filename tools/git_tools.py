"""
Git integration tools: status, diff, log, commit, add, push, branch, PR.
"""

from __future__ import annotations

import asyncio

from .base import ToolBase, ToolOutput
from .registry import register_tool


async def _run_git(args: list[str], cwd: str = ".") -> tuple[int, str, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60.0)
        return (
            proc.returncode or 0,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )
    except FileNotFoundError:
        return -1, "", "git not found. Install git or add it to PATH."
    except Exception as e:
        return -1, "", str(e)


class GitStatus(ToolBase):
    name = "GitStatus"
    description = "Show git working tree status."
    aliases = ["gits", "status"]
    parameters = {}

    async def execute(self) -> ToolOutput:
        code, out, err = await _run_git(["status", "--short"])
        if code != 0:
            return ToolOutput(text=err or "git error", error=True)
        if not out.strip():
            return ToolOutput(text="Working tree clean.", title="git status")
        return ToolOutput(text=out.strip(), title="git status", metadata={"dirty": True})


class GitDiff(ToolBase):
    name = "GitDiff"
    description = "Show git diff of changes."
    aliases = ["gitd", "diff"]
    parameters = {
        "staged": {"type": "boolean", "description": "Show staged changes only"},
    }

    async def execute(self, staged: bool = False) -> ToolOutput:
        args = ["diff"]
        if staged:
            args.append("--staged")
        code, out, err = await _run_git(args)
        if code != 0:
            return ToolOutput(text=err or "git error", error=True)
        if not out.strip():
            return ToolOutput(text="No changes.", title="git diff")
        return ToolOutput(
            text=out[:8000], title="git diff", metadata={"lines": len(out.splitlines())}
        )


class GitLog(ToolBase):
    name = "GitLog"
    description = "Show recent commit history."
    aliases = ["gitl", "log"]
    parameters = {
        "count": {"type": "integer", "description": "Number of commits to show"},
    }

    async def execute(self, count: int = 10) -> ToolOutput:
        code, out, err = await _run_git(["log", f"-{count}", "--oneline", "--decorate", "--graph"])
        if code != 0:
            return ToolOutput(text=err or "git error", error=True)
        return ToolOutput(text=out.strip() or "(no commits)", title=f"Last {count} commits")


class GitCommit(ToolBase):
    name = "GitCommit"
    description = "Create a git commit with a message."
    aliases = ["gitc", "commit"]
    parameters = {
        "message": {"type": "string", "description": "Commit message"},
    }

    async def execute(self, message: str) -> ToolOutput:
        code, out, err = await _run_git(["commit", "-m", message])
        if code != 0:
            return ToolOutput(text=err or "git error", error=True)
        return ToolOutput(text=out.strip(), title="Committed")


class GitAdd(ToolBase):
    name = "GitAdd"
    description = "Stage files for commit."
    aliases = ["gita", "add"]
    parameters = {
        "files": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Files to stage (default: all)",
        },
    }

    async def execute(self, files: list[str] | None = None) -> ToolOutput:
        args = ["add"]
        if files:
            args.extend(files)
        else:
            args.append(".")
        code, _out, err = await _run_git(args)
        if code != 0:
            return ToolOutput(text=err or "git error", error=True)
        return ToolOutput(text="Staged.", title="git add")


class GitPush(ToolBase):
    name = "GitPush"
    description = "Push commits to remote."
    aliases = ["gitp", "push"]
    parameters = {
        "branch": {"type": "string", "description": "Branch to push"},
    }

    async def execute(self, branch: str = "") -> ToolOutput:
        args = ["push"]
        if branch:
            args.extend(["origin", branch])
        code, out, err = await _run_git(args)
        if code != 0:
            return ToolOutput(text=err or "git error", error=True)
        return ToolOutput(text=out.strip() or "Pushed.", title="git push")


class GitBranch(ToolBase):
    name = "GitBranch"
    description = "List and manage git branches."
    aliases = ["gitb", "branch"]
    parameters = {}

    async def execute(self) -> ToolOutput:
        code, out, err = await _run_git(["branch", "-a"])
        if code != 0:
            return ToolOutput(text=err or "git error", error=True)
        return ToolOutput(text=out.strip(), title="Branches")


class GitPR(ToolBase):
    name = "GitPR"
    description = "Push current branch and create a DRAFT pull request on GitHub."
    aliases = ["pr"]
    parameters = {
        "title": {"type": "string", "description": "PR title"},
        "body": {"type": "string", "description": "PR body/description"},
    }

    async def execute(self, title: str, body: str = "") -> ToolOutput:
        # Push current branch first
        code, out, err = await _run_git(["push", "origin", "HEAD"])
        if code != 0:
            return ToolOutput(text=f"Push failed: {err}", error=True)

        # Create PR via gh CLI
        try:
            args = ["gh", "pr", "create", "--draft", "--title", title]
            if body:
                args.extend(["--body", body])
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60.0)
            out = stdout.decode("utf-8", errors="replace")
            err = stderr.decode("utf-8", errors="replace")
            if proc.returncode != 0:
                return ToolOutput(text=f"gh CLI error: {err}", error=True)
            return ToolOutput(text=out.strip(), title=f"PR: {title}")
        except FileNotFoundError:
            return ToolOutput(
                text="gh CLI not found. Install GitHub CLI: https://cli.github.com/", error=True
            )


for _tool in (
    GitStatus(),
    GitDiff(),
    GitLog(),
    GitCommit(),
    GitAdd(),
    GitPush(),
    GitBranch(),
    GitPR(),
):
    register_tool(_tool)
