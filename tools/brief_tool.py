"""
Brief tool - quick summaries of files and directories.
"""

from __future__ import annotations

from pathlib import Path

from .base import ToolBase, ToolOutput
from .registry import register_tool


class BriefTool(ToolBase):
    name = "Brief"
    description = "Get a concise summary of a file, directory, or any text. Use for quick orientation on unfamiliar code."
    aliases = ["Summarize", "Overview"]
    parameters = {
        "file_path": {"type": "string", "description": "Absolute path to a file to summarise."},
        "dir_path": {"type": "string", "description": "Absolute path to a directory to summarise."},
        "text": {"type": "string", "description": "Arbitrary text or code to summarise."},
        "focus": {"type": "string", "description": "Optional focus area for the summary."},
    }

    async def execute(
        self, file_path: str = "", dir_path: str = "", text: str = "", focus: str = ""
    ) -> ToolOutput:
        focus_text = f" (focusing on {focus})" if focus else ""

        if text:
            lines = text.strip().splitlines()
            total_lines = len(lines)
            total_chars = len(text)
            preview = text[:500]
            summary = (
                f"Text summary{focus_text}:\n"
                f"  Lines: {total_lines}\n"
                f"  Characters: {total_chars}\n\n"
                f"--- Preview (first 500 chars) ---\n{preview}"
            )
            return ToolOutput(
                text=summary,
                title="Text Brief",
                metadata={"lines": total_lines, "chars": total_chars},
            )

        if dir_path:
            p = Path(dir_path)
            if not p.exists():
                return ToolOutput(text=f"Directory not found: {dir_path}", error=True)
            if not p.is_dir():
                return ToolOutput(text=f"Not a directory: {dir_path}", error=True)

            files = []
            dirs = []
            for item in sorted(p.iterdir()):
                if item.name.startswith("."):
                    continue
                if item.is_file():
                    files.append(item.name)
                elif item.is_dir():
                    dirs.append(item.name)

            file_list = "\n".join(f"  - {f}" for f in files[:30])
            dir_list = "\n".join(f"  - {d}/" for d in dirs[:20])

            summary = (
                f"Directory: {p}{focus_text}\n"
                f"  Subdirectories: {len(dirs)}\n{dir_list if dirs else '  (none)'}\n"
                f"  Files: {len(files)}\n{file_list if files else '  (none)'}"
            )
            if len(files) > 30:
                summary += f"\n  ... and {len(files) - 30} more files"
            return ToolOutput(
                text=summary,
                title=f"Brief: {p.name}",
                metadata={"files": len(files), "dirs": len(dirs)},
            )

        if file_path:
            p = Path(file_path)
            if not p.exists():
                return ToolOutput(text=f"File not found: {file_path}", error=True)

            content = p.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines()
            total_lines = len(lines)

            imports = [
                line
                for line in lines[:50]
                if line.strip().startswith(("import ", "from ", "#include"))
            ]
            classes = [
                line
                for line in lines
                if line.strip().startswith(("class ", "struct ", "interface "))
            ]
            functions = [
                line for line in lines if "def " in line or "function " in line or "func " in line
            ]

            summary = (
                f"File: {p}{focus_text}\n"
                f"  Lines: {total_lines}\n"
                f"  Size: {p.stat().st_size:,} bytes\n"
                f"  Imports: {len(imports)}\n"
                f"  Classes: {len(classes)}\n"
                f"  Functions: {len(functions)}\n\n"
                f"--- Preview (first 10 lines) ---\n"
                + "\n".join(f"  {i + 1}: {line[:100]}" for i, line in enumerate(lines[:10]))
            )
            return ToolOutput(
                text=summary,
                title=f"Brief: {p.name}",
                metadata={"lines": total_lines, "size": p.stat().st_size},
            )

        return ToolOutput(text="Provide file_path, dir_path, or text to summarise.", error=True)


register_tool(BriefTool())
