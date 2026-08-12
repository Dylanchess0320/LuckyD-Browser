"""
File manipulation tools: Read, Write, Edit, Glob, Grep.
These are the core tools every coding agent needs.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from .base import ToolBase, ToolOutput
from .registry import register_tool

# Credential / system paths the agent should never read or write, even though
# these tools are intentionally unconfined to any single project directory.
_SENSITIVE_PATH_MARKERS = {
    ".env", ".ssh", ".aws", ".netrc", ".npmrc", ".docker", ".gnupg",
    "id_rsa", "id_ed25519", "id_ecdsa", "authorized_keys", "known_hosts",
    ".bash_history", ".zsh_history", ".python_history", ".bashrc", ".zshrc",
    ".bash_profile", ".profile", "shadow", "sam", "credentials",
}


def _is_sensitive_path(path: Path) -> bool:
    """Block reads/writes to credential stores and shell/system config,
    regardless of which project directory the agent is currently working in.
    Catches both the exact filename and any ancestor directory (e.g. ~/.ssh/config).
    """
    name_lower = path.name.lower()
    if name_lower in _SENSITIVE_PATH_MARKERS:
        return True
    return any(part.lower() in _SENSITIVE_PATH_MARKERS for part in path.parts)


class ReadTool(ToolBase):
    name = "Read"
    description = "Read the contents of a file. Supports line offsets and limits."
    parameters = {
        "file_path": {"type": "string", "description": "Absolute path to the file to read"},
        "offset": {
            "type": "integer",
            "description": "Line number to start reading from (0-indexed)",
        },
        "limit": {"type": "integer", "description": "Maximum number of lines to read"},
    }

    async def execute(
        self, file_path: str, offset: int = 0, limit: int | None = None
    ) -> ToolOutput:
        try:
            path = Path(file_path).expanduser().resolve()
            if _is_sensitive_path(path):
                return ToolOutput(
                    text=f"Error: Refusing to read credential/system path: {file_path}",
                    error=True,
                )
            if not path.exists():
                return ToolOutput(text=f"Error: File not found: {file_path}", error=True)
            if path.is_dir():
                return ToolOutput(text=f"Error: Path is a directory: {file_path}", error=True)

            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            display_lines = []
            end = len(lines) if limit is None else min(offset + limit, len(lines))
            for i in range(offset, end):
                display_lines.append(f"{i:4d} | {lines[i]}")

            output = "\n".join(display_lines)
            if len(output) > 30000:
                output = output[:30000] + f"\n\n... [truncated, {len(lines) - end} more lines]"

            return ToolOutput(
                text=output,
                title=f"{path.name} ({len(lines)} lines, showing {offset}-{end})",
                metadata={
                    "file": str(path),
                    "total_lines": len(lines),
                    "shown_range": [offset, end],
                },
            )
        except Exception as e:
            return ToolOutput(text=f"Error reading file: {e}", error=True)


class WriteTool(ToolBase):
    name = "Write"
    description = "Create a new file or overwrite an existing file with new content."
    permission_level = "REQUIRES_APPROVAL"
    tracks_files = True
    parameters = {
        "file_path": {"type": "string", "description": "Absolute path to the file to write"},
        "content": {"type": "string", "description": "Content to write to the file"},
    }

    async def execute(self, file_path: str, content: str) -> ToolOutput:
        try:
            path = Path(file_path).expanduser().resolve()
            if _is_sensitive_path(path):
                return ToolOutput(
                    text=f"Error: Refusing to write to credential/system path: {file_path}",
                    error=True,
                )
            # Snapshot before for checkpoint
            old_content = None
            if path.exists():
                old_content = path.read_text(encoding="utf-8", errors="replace")
            path.parent.mkdir(parents=True, exist_ok=True)
            existed = path.exists()
            path.write_text(content, encoding="utf-8")
            size = path.stat().st_size
            # Record checkpoint
            try:
                from core.checkpoint import get_checkpoint_manager

                cm = get_checkpoint_manager()
                cm.record_edit(str(path), old_content or "", content)
            except Exception:
                pass
            verb = "Updated" if existed else "Created"
            return ToolOutput(
                text=f"{verb} {path} ({size:,} bytes)",
                title=f"{verb} {path.name}",
                metadata={"file": str(path), "size": size, "created": not existed},
            )
        except Exception as e:
            return ToolOutput(text=f"Error writing file: {e}", error=True)


class EditTool(ToolBase):
    name = "Edit"
    description = "Edit an existing file by replacing text. Performs an exact string replacement."
    permission_level = "NORMAL"
    tracks_files = True
    parameters = {
        "file_path": {"type": "string", "description": "Absolute path to the file to edit"},
        "old_string": {
            "type": "string",
            "description": "Text to replace (must be unique in the file)",
        },
        "new_string": {"type": "string", "description": "Text to replace it with"},
        "replace_all": {
            "type": "boolean",
            "description": "Replace all occurrences instead of just the first",
        },
    }

    async def execute(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> ToolOutput:
        try:
            path = Path(file_path).expanduser().resolve()
            if _is_sensitive_path(path):
                return ToolOutput(
                    text=f"Error: Refusing to edit credential/system path: {file_path}",
                    error=True,
                )
            if not path.exists():
                return ToolOutput(text=f"Error: File not found: {file_path}", error=True)

            original = path.read_text(encoding="utf-8")
            if not replace_all:
                count = original.count(old_string)
                if count == 0:
                    return ToolOutput(text="Error: old_string not found in file", error=True)
                if count > 1:
                    return ToolOutput(
                        text=f"Error: old_string found {count} times in file. Use replace_all=True or make it more specific.",
                        error=True,
                    )
                new_content = original.replace(old_string, new_string, 1)
            else:
                count = original.count(old_string)
                if count == 0:
                    return ToolOutput(text="Error: old_string not found in file", error=True)
                new_content = original.replace(old_string, new_string)

            path.write_text(new_content, encoding="utf-8")
            try:
                from core.checkpoint import get_checkpoint_manager

                cm = get_checkpoint_manager()
                cm.record_edit(str(path), original, new_content)
            except Exception:
                pass
            return ToolOutput(
                text=f"Replaced {count} occurrence(s) in {path.name}",
                title=f"Edited {path.name}",
                metadata={"file": str(path), "replacements": count},
            )
        except Exception as e:
            return ToolOutput(text=f"Error editing file: {e}", error=True)


class GlobTool(ToolBase):
    name = "Glob"
    description = "Find files matching a glob pattern. Supports ** and * wildcards."
    parameters = {
        "pattern": {
            "type": "string",
            "description": "Glob pattern (e.g., '**/*.py', 'src/**/*.ts')",
        },
        "path": {
            "type": "string",
            "description": "Directory to search in (defaults to current directory)",
        },
    }

    async def execute(self, pattern: str, path: str = ".") -> ToolOutput:
        try:
            base = Path(path).expanduser().resolve()
            matches = sorted(base.glob(pattern))
            # Filter out common ignore dirs
            ignore = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tox", ".mypy_cache"}
            filtered = [str(m) for m in matches if not any(p.name in ignore for p in m.parents)]

            shown = filtered[:200]
            output = "\n".join(f"  {p}" for p in shown)
            if len(filtered) > 200:
                output += f"\n  ... and {len(filtered) - 200} more matches"
            return ToolOutput(
                text=output,
                title=f"{len(filtered)} matches for '{pattern}'",
                metadata={"pattern": pattern, "count": len(filtered)},
            )
        except Exception as e:
            return ToolOutput(text=f"Error in glob: {e}", error=True)


class GrepTool(ToolBase):
    name = "Grep"
    description = "Search for a regex pattern in file contents."
    parameters = {
        "pattern": {"type": "string", "description": "Regular expression pattern to search for"},
        "path": {"type": "string", "description": "Directory or file to search in"},
        "glob": {"type": "string", "description": "File glob pattern to filter (e.g., '*.py')"},
        "output_mode": {
            "type": "string",
            "description": "Output format: content, files_with_matches, or count",
        },
    }

    async def execute(
        self, pattern: str, path: str = ".", glob: str = "", output_mode: str = "content"
    ) -> ToolOutput:
        try:
            base = Path(path).expanduser().resolve()
            compiled = re.compile(pattern)

            files = list(base.rglob("*")) if base.is_dir() else [base]
            if glob:
                files = [f for f in files if fnmatch.fnmatch(f.name, glob)]

            results: list[str] = []
            match_count = 0
            seen_files = set()

            for f in sorted(files):
                if not f.is_file():
                    continue
                if any(
                    p.name in {".git", "__pycache__", "node_modules", ".venv"} for p in f.parents
                ):
                    continue
                try:
                    content = f.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue

                file_matches = []
                for i, line in enumerate(content.splitlines(), 1):
                    if compiled.search(line):
                        file_matches.append((i, line))
                        match_count += 1

                if file_matches:
                    seen_files.add(str(f))
                    if output_mode == "files_with_matches":
                        results.append(str(f))
                    elif output_mode == "count":
                        results.append(f"{f!s}: {len(file_matches)} matches")
                    else:
                        results.append(f"\n  {f}:")
                        for lineno, line in file_matches[:20]:
                            results.append(f"    {lineno:4d}: {line[:150]}")
                        if len(file_matches) > 20:
                            results.append(f"    ... and {len(file_matches) - 20} more matches")

            if not results:
                return ToolOutput(text=f"No matches for '{pattern}'", title="0 matches")
            output = "\n".join(results)
            if len(output) > 8000:
                output = output[:8000] + "\n... [truncated]"
            return ToolOutput(
                text=output,
                title=f"{match_count} matches in {len(seen_files)} files",
                metadata={
                    "pattern": pattern,
                    "match_count": match_count,
                    "files_count": len(seen_files),
                },
            )
        except re.error as e:
            return ToolOutput(text=f"Invalid regex: {e}", error=True)
        except Exception as e:
            return ToolOutput(text=f"Error in grep: {e}", error=True)


# Auto-register
for cls in [ReadTool, WriteTool, EditTool, GlobTool, GrepTool]:
    register_tool(cls())
