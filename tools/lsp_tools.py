"""
LSP (Language Server Protocol) integration tools — definition, references, hover, rename, symbols.
Uses jedi-language-server or pylsp for Python files.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from .base import ToolBase, ToolOutput
from .registry import register_tool

# ── Jedi-based introspection (works without LSP server) ──


def _get_jedi():
    try:
        import jedi

        return jedi
    except ImportError:
        raise RuntimeError("jedi not installed. Run: pip install jedi") from None


def _resolve_path(file_path: str) -> Path:
    return Path(file_path).expanduser().resolve()


class LspDefinitionTool(ToolBase):
    name = "LspDefinition"
    description = "Go to the definition of a symbol. Returns file, line, and definition text."
    aliases = ["GoToDef", "FindDef"]
    parameters = {
        "file_path": {"type": "string", "description": "Absolute path to the Python file"},
        "line": {"type": "integer", "description": "Line number (1-indexed)"},
        "character": {"type": "integer", "description": "Column offset (0-indexed, default 0)"},
    }

    async def execute(self, file_path: str, line: int, character: int = 0) -> ToolOutput:
        try:
            jedi = _get_jedi()
            path = _resolve_path(file_path)
            if not path.exists():
                return ToolOutput(text=f"File not found: {file_path}", error=True)

            source = path.read_text()
            results = await asyncio.to_thread(
                lambda: jedi.Script(code=source, path=str(path)).goto(line=line, column=character)
            )

            if not results:
                return ToolOutput(text="No definition found.", title="Go to Definition")

            parts = []
            for r in results:
                def_path = r.module_path or str(path)
                parts.append(f"{r.full_name} → {def_path}:{r.line}")
                if r.docstring():
                    parts.append(f"  {r.docstring()[:200]}")

            return ToolOutput(
                text="\n".join(parts),
                title=f"Definition: {results[0].full_name}" if results else "Go to Definition",
                metadata={"count": len(results), "symbol": results[0].full_name if results else ""},
            )
        except Exception as e:
            return ToolOutput(text=f"LSP error: {e}", error=True)


class LspReferencesTool(ToolBase):
    name = "LspReferences"
    description = (
        "Find all references to a symbol. Returns every file and line number that uses it."
    )
    aliases = ["FindRefs", "FindUsages"]
    parameters = {
        "file_path": {"type": "string", "description": "Absolute path to the Python file"},
        "line": {"type": "integer", "description": "Line number (1-indexed)"},
        "character": {"type": "integer", "description": "Column offset (0-indexed, default 0)"},
    }

    async def execute(self, file_path: str, line: int, character: int = 0) -> ToolOutput:
        try:
            jedi = _get_jedi()
            path = _resolve_path(file_path)
            source = path.read_text()
            results = await asyncio.to_thread(
                lambda: jedi.Script(code=source, path=str(path)).get_references(
                    line=line, column=character
                )
            )

            if not results:
                return ToolOutput(text="No references found.", title="Find References")

            parts = []
            for r in results[:30]:
                fname = Path(r.module_path).name if r.module_path else "?"
                parts.append(f"  {fname}:{r.line}:{r.column} — {r.code[:120]}")

            if len(results) > 30:
                parts.append(f"  ... and {len(results) - 30} more")

            return ToolOutput(
                text="\n".join(parts),
                title=f"{len(results)} References",
                metadata={"count": len(results)},
            )
        except Exception as e:
            return ToolOutput(text=f"LSP error: {e}", error=True)


class LspHoverTool(ToolBase):
    name = "LspHover"
    description = "Get type, docstring, and signature for a symbol at a given position."
    aliases = ["Hover", "TypeInfo"]
    parameters = {
        "file_path": {"type": "string", "description": "Absolute path to the Python file"},
        "line": {"type": "integer", "description": "Line number (1-indexed)"},
        "character": {"type": "integer", "description": "Column offset (0-indexed, default 0)"},
    }

    async def execute(self, file_path: str, line: int, character: int = 0) -> ToolOutput:
        try:
            jedi = _get_jedi()
            path = _resolve_path(file_path)
            source = path.read_text()
            script = jedi.Script(code=source, path=str(path))
            results = script.help(line=line, column=character)

            if not results:
                return ToolOutput(text="No type info found.", title="Hover")

            parts = []
            for r in results:
                parts.append(f"Name: {r.name}")
                parts.append(f"Type: {r.type}")
                if r.docstring():
                    parts.append(f"\n{r.docstring()[:500]}")

            return ToolOutput(
                text="\n".join(parts),
                title=f"Type Info: {results[0].name}",
                metadata={"name": results[0].name, "type": results[0].type},
            )
        except Exception as e:
            return ToolOutput(text=f"LSP error: {e}", error=True)


class LspRenameTool(ToolBase):
    name = "LspRename"
    description = (
        "Safely rename a symbol across all files in the project using LSP reference finding."
    )
    aliases = ["Rename", "Refactor"]
    parameters = {
        "file_path": {"type": "string", "description": "File containing the symbol to rename"},
        "line": {"type": "integer", "description": "Line number (1-indexed)"},
        "new_name": {"type": "string", "description": "New name for the symbol"},
        "character": {"type": "integer", "description": "Column offset (0-indexed, default 0)"},
    }

    async def execute(
        self, file_path: str, line: int, new_name: str, character: int = 0
    ) -> ToolOutput:
        try:
            jedi = _get_jedi()
            path = _resolve_path(file_path)
            source = path.read_text()
            script = jedi.Script(code=source, path=str(path))
            refs = script.get_references(line=line, column=character)

            if not refs:
                return ToolOutput(text="No references found to rename.", error=True)

            # Group refs by file and apply edits bottom-to-top
            by_file: dict[str, list] = {}
            for r in refs:
                fp = r.module_path
                if fp:
                    by_file.setdefault(fp, []).append(r)

            changes = 0
            old_name = refs[0].name
            for fp, file_refs in by_file.items():
                lines = Path(fp).read_text().splitlines()
                # Apply from bottom up to preserve line numbers
                sorted_refs = sorted(file_refs, key=lambda r: r.line, reverse=True)
                for r in sorted_refs:
                    line_str = lines[r.line - 1]
                    lines[r.line - 1] = (
                        line_str[: r.column] + new_name + line_str[r.column + len(old_name) :]
                    )
                    changes += 1
                Path(fp).write_text("\n".join(lines) + "\n")

            return ToolOutput(
                text=f"Renamed '{old_name}' → '{new_name}' in {changes} location(s) across {len(by_file)} file(s).",
                title=f"🔀 Renamed {old_name}",
                metadata={
                    "old_name": old_name,
                    "new_name": new_name,
                    "changes": changes,
                    "files": len(by_file),
                },
            )
        except Exception as e:
            return ToolOutput(text=f"Rename error: {e}", error=True)


class LspDocumentSymbolsTool(ToolBase):
    name = "LspDocumentSymbols"
    description = (
        "Get the full symbol outline of a file — all classes, functions, methods, variables."
    )
    aliases = ["Symbols", "Outline"]
    parameters = {
        "file_path": {"type": "string", "description": "Absolute path to the Python file"},
    }

    async def execute(self, file_path: str) -> ToolOutput:
        try:
            jedi = _get_jedi()
            path = _resolve_path(file_path)
            source = path.read_text()
            names = await asyncio.to_thread(
                lambda: jedi.Script(code=source, path=str(path)).get_names(
                    all_scopes=True, definitions=True
                )
            )

            parts = []
            for n in names:
                indent = "  " * (n.full_name.count(".") if n.full_name else 0)
                kind = n.type
                parts.append(f"{indent}{kind}: {n.name} (line {n.line})")

            return ToolOutput(
                text="\n".join(parts[:100]),
                title=f"Symbols in {path.name} ({len(names)} symbols)",
                metadata={"count": len(names)},
            )
        except Exception as e:
            return ToolOutput(text=f"Symbols error: {e}", error=True)


class LspWorkspaceSymbolsTool(ToolBase):
    name = "LspWorkspaceSymbols"
    description = "Search for symbols across the entire project. Returns matches from all files."
    aliases = ["SearchSymbols", "FindSymbol"]
    parameters = {
        "query": {"type": "string", "description": "Symbol name to search for (empty = all)"},
    }

    async def execute(self, query: str = "") -> ToolOutput:
        try:
            import jedi

            # Search across all Python files in cwd
            cwd = Path.cwd()
            results = []
            for py_file in cwd.rglob("*.py"):
                if any(
                    p.name in {".git", "__pycache__", "node_modules", ".venv"}
                    for p in py_file.parents
                ):
                    continue
                try:
                    source = py_file.read_text(errors="replace")
                    script = jedi.Script(code=source, path=str(py_file))
                    names = script.get_names(all_scopes=True)
                    for n in names:
                        if not query or query.lower() in n.name.lower():
                            results.append(f"  {n.type}: {n.name} — {py_file.name}:{n.line}")
                except Exception:
                    continue

            shown = results[:50]
            output = "\n".join(shown) if shown else f"No symbols found matching '{query}'"
            if len(results) > 50:
                output += f"\n  ... and {len(results) - 50} more"

            return ToolOutput(
                text=output,
                title=f"Workspace Symbols ({len(shown)})",
                metadata={"count": len(results), "query": query},
            )
        except Exception as e:
            return ToolOutput(text=f"Workspace search error: {e}", error=True)


class LspImplementationTool(ToolBase):
    name = "LspImplementation"
    description = "Find concrete implementations of an abstract method or interface."
    aliases = ["FindImpl", "Implementations"]
    parameters = {
        "file_path": {"type": "string", "description": "Absolute path to the Python file"},
        "line": {"type": "integer", "description": "Line number (1-indexed)"},
    }

    async def execute(self, file_path: str, line: int) -> ToolOutput:
        try:
            jedi = _get_jedi()
            path = _resolve_path(file_path)
            source = path.read_text()
            script = jedi.Script(code=source, path=str(path))
            results = script.goto(line=line, column=0)

            if not results or not results[0].name:
                return ToolOutput(text="No implementations found.", title="Implementations")

            name = results[0].name
            # Search project for this method name in class bodies
            cwd = Path.cwd()
            found = []
            for py_file in cwd.rglob("*.py"):
                if any(
                    p.name in {".git", "__pycache__", "node_modules", ".venv"}
                    for p in py_file.parents
                ):
                    continue
                try:
                    content = py_file.read_text(errors="replace")
                    # Simple heuristic: find 'def name' in class context
                    pattern = re.compile(
                        rf"class\s+\w+.*:[\s\S]*?def\s+{re.escape(name)}\s*\(", re.MULTILINE
                    )
                    for match in pattern.finditer(content):
                        line_num = content[: match.start()].count("\n") + 1
                        found.append(f"  {py_file.name}:{line_num}")
                except Exception:
                    continue

            if not found:
                return ToolOutput(
                    text=f"No implementations found for '{name}'.", title="Implementations"
                )

            return ToolOutput(
                text=f"Implementations of '{name}':\n" + "\n".join(found[:30]),
                title=f"Implementations of {name}",
                metadata={"count": len(found)},
            )
        except Exception as e:
            return ToolOutput(text=f"Implementation search error: {e}", error=True)


class LspIncomingCallsTool(ToolBase):
    name = "LspIncomingCalls"
    description = "Find everything that calls this function (who calls this)."
    aliases = ["Callers", "WhoCalls"]
    parameters = {
        "file_path": {"type": "string", "description": "Absolute path to the Python file"},
        "line": {"type": "integer", "description": "Line number (1-indexed)"},
        "character": {"type": "integer", "description": "Column offset (0-indexed, default 0)"},
    }

    async def execute(self, file_path: str, line: int, character: int = 0) -> ToolOutput:
        try:
            jedi = _get_jedi()
            path = _resolve_path(file_path)
            source = path.read_text()
            script = jedi.Script(code=source, path=str(path))
            results = await asyncio.to_thread(lambda: script.goto(line=line, column=character))

            if not results:
                return ToolOutput(text="No callers found.", title="Callers")

            name = results[0].name
            callers = await asyncio.to_thread(
                lambda: script.get_references(line=line, column=character, include_builtins=False)
            )

            parts = []
            for c in callers[:30]:
                fname = Path(c.module_path).name if c.module_path else "?"
                parts.append(f"  {fname}:{c.line} — {c.code[:120]}")

            output = (
                f"Callers of '{name}':\n" + "\n".join(parts)
                if parts
                else f"No callers found for '{name}'."
            )
            return ToolOutput(
                text=output,
                title=f"Callers of {name}",
                metadata={"count": len(callers)},
            )
        except Exception as e:
            return ToolOutput(text=f"Caller search error: {e}", error=True)


class LspOutgoingCallsTool(ToolBase):
    name = "LspOutgoingCalls"
    description = "Find every function called by this function (what this calls)."
    aliases = ["Callees", "WhatCalls"]
    parameters = {
        "file_path": {"type": "string", "description": "Absolute path to the Python file"},
        "line": {"type": "integer", "description": "Line number (1-indexed)"},
        "character": {"type": "integer", "description": "Column offset (0-indexed, default 0)"},
    }

    async def execute(self, file_path: str, line: int, character: int = 0) -> ToolOutput:
        try:
            jedi = _get_jedi()
            path = _resolve_path(file_path)
            source = path.read_text()
            script = jedi.Script(code=source, path=str(path))

            # Get the function definition
            func = script.get_context(line=line, column=character)
            if not func or func.type != "function":
                return ToolOutput(text="Not inside a function.", title="Callees")

            # Find all names called within the function
            func.get_line_code()
            # Parse the function body
            callees = set()
            tree = jedi.Script(code=source, path=str(path))
            names = tree.get_names(all_scopes=True)
            for n in names:
                if (
                    n.line > func.line and n.line < func.line + 100 and n.type == "function"
                ):  # rough bounds
                    callees.add(f"  {n.full_name} — {path.name}:{n.line}")

            output = (
                f"Callees from '{func.name}':\n" + "\n".join(sorted(callees)[:30])
                if callees
                else "No callees found."
            )
            return ToolOutput(text=output, title=f"Callees of {func.name}")
        except Exception as e:
            return ToolOutput(text=f"Callee search error: {e}", error=True)


# Auto-register
for cls in [
    LspDefinitionTool,
    LspReferencesTool,
    LspHoverTool,
    LspRenameTool,
    LspDocumentSymbolsTool,
    LspWorkspaceSymbolsTool,
    LspImplementationTool,
    LspIncomingCallsTool,
    LspOutgoingCallsTool,
]:
    register_tool(cls())
