"""
Data source tools: SQLite query, CSV, JSON, document reading, and secrets access.
"""

from __future__ import annotations

import csv
import os
import sqlite3
from pathlib import Path

from .base import ToolBase, ToolOutput
from .registry import register_tool


class SQLiteTool(ToolBase):
    """Query or inspect a local SQLite database file."""

    name = "SQLite"
    description = "Query or inspect a local SQLite database file."
    parameters = {
        "op": {
            "type": "string",
            "description": "Operation: query, execute, schema, tables, or info",
        },
        "db_path": {
            "type": "string",
            "description": "Path to the SQLite database file (e.g. 'app.db')",
        },
        "sql": {
            "type": "string",
            "description": "SQL statement to execute (required for query and execute)",
        },
        "table": {
            "type": "string",
            "description": "Table name to filter schema output (optional)",
        },
    }

    async def execute(self, op, db_path, sql="", table=""):
        try:
            path = Path(db_path).expanduser().resolve()
            if not path.exists() and op != "execute":
                return ToolOutput(text=f"Database not found: {db_path}", error=True)

            if op == "tables":
                conn = sqlite3.connect(str(path))
                cur = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
                tables = [row[0] for row in cur.fetchall()]
                conn.close()
                return ToolOutput(
                    text="\n".join(tables) or "(no tables)",
                    title=f"Tables in {path.name} ({len(tables)})",
                    metadata={"tables": tables},
                )

            elif op == "schema":
                conn = sqlite3.connect(str(path))
                if table:
                    cur = conn.execute(
                        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                        (table,),
                    )
                else:
                    cur = conn.execute("SELECT sql FROM sqlite_master WHERE type='table'")
                schemas = [row[0] for row in cur.fetchall() if row[0]]
                conn.close()
                return ToolOutput(
                    text="\n\n".join(schemas) or "(no schema found)",
                    title=f"Schema for {path.name}",
                )

            elif op == "info":
                size = path.stat().st_size
                conn = sqlite3.connect(str(path))
                cur = conn.execute("PRAGMA page_count")
                pages = cur.fetchone()[0]
                cur = conn.execute("PRAGMA encoding")
                encoding = cur.fetchone()[0]
                cur = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'")
                table_count = cur.fetchone()[0]
                conn.close()
                return ToolOutput(
                    text=f"Size: {size:,} bytes\nPages: {pages}\nEncoding: {encoding}\nTables: {table_count}",
                    title=f"Info: {path.name}",
                )

            elif op == "query":
                if not sql:
                    return ToolOutput(text="SQL query is required for 'query' op", error=True)
                conn = sqlite3.connect(str(path))
                conn.row_factory = sqlite3.Row
                cur = conn.execute(sql)
                rows = [dict(row) for row in cur.fetchall()]
                conn.close()
                if not rows:
                    return ToolOutput(text="(empty result)", title="0 rows")
                cols = list(rows[0].keys())
                lines = [" | ".join(cols), " | ".join("---" for _ in cols)]
                for row in rows[:50]:
                    lines.append(" | ".join(str(row[c]) for c in cols))
                if len(rows) > 50:
                    lines.append(f"... and {len(rows) - 50} more rows")
                return ToolOutput(
                    text="\n".join(lines),
                    title=f"{len(rows)} rows",
                    metadata={"columns": cols, "row_count": len(rows)},
                )

            elif op == "execute":
                if not sql:
                    return ToolOutput(text="SQL statement is required for 'execute' op", error=True)
                conn = sqlite3.connect(str(path))
                conn.execute(sql)
                conn.commit()
                changes = conn.total_changes
                conn.close()
                return ToolOutput(
                    text=f"Executed: {sql[:100]}\nChanges: {changes}",
                    title="SQL executed",
                    metadata={"changes": changes},
                )

            return ToolOutput(text=f"Unknown operation: {op}", error=True)

        except sqlite3.Error as e:
            return ToolOutput(text=f"SQLite error: {e}", error=True)
        except Exception as e:
            return ToolOutput(text=f"Error: {e}", error=True)


class CSVTool(ToolBase):
    """Read CSV files."""

    name = "CSV"
    description = "Read and parse a CSV file."
    parameters = {
        "file_path": {"type": "string", "description": "Path to the CSV file"},
        "delimiter": {"type": "string", "description": "Field delimiter (default: comma)"},
        "limit": {"type": "integer", "description": "Max rows to return (default: 200)"},
    }

    def _read_csv(self, path, delimiter, limit):
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            rows = []
            for row in reader:
                rows.append(row)
                if len(rows) >= limit:
                    break

        if not rows:
            return None, None, None

        cols = list(rows[0].keys())
        lines = [" | ".join(cols), " | ".join("---" for _ in cols)]
        for row in rows:
            lines.append(" | ".join(str(row.get(c, ""))[:80] for c in cols))

        return rows, cols, lines

    async def execute(self, file_path, delimiter=",", limit=200):
        try:
            import asyncio

            path = Path(file_path).expanduser().resolve()
            if not path.exists():
                return ToolOutput(text=f"File not found: {file_path}", error=True)

            rows, cols, lines = await asyncio.to_thread(self._read_csv, path, delimiter, limit)

            if not rows:
                return ToolOutput(text="(empty file or no rows)", title="0 rows")

            return ToolOutput(
                text="\n".join(lines),
                title=f"{len(rows)} rows from {path.name}",
                metadata={"columns": cols, "row_count": len(rows), "file": str(path)},
            )
        except Exception as e:
            return ToolOutput(text=f"Error reading CSV: {e}", error=True)


class SecretsTool(ToolBase):
    """Read API keys and secrets from .env files."""

    name = "Secrets"
    description = "Read API keys and secrets safely without dumping to terminal."
    parameters = {
        "op": {
            "type": "string",
            "description": "Operation: get_env, list_env, check_env, get_key, set_key, del_key",
        },
        "key": {
            "type": "string",
            "description": "Environment variable name or keychain username",
        },
        "env_file": {
            "type": "string",
            "description": "Path to .env file (optional, auto-discovered)",
        },
    }

    async def execute(self, op, key="", env_file=""):
        try:
            if op == "list_env":
                target = env_file or ".env"
                if not os.path.exists(target):
                    return ToolOutput(text=f"{target} not found", error=True)
                keys = []
                with open(target, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            keys.append(line.split("=")[0].strip())
                return ToolOutput(
                    text="\n".join(keys) if keys else "(no keys found)",
                    title=f"Keys in {target} ({len(keys)})",
                    metadata={"keys": keys},
                )

            elif op == "check_env":
                if not key:
                    return ToolOutput(text="key parameter is required", error=True)
                target = env_file or ".env"
                if not os.path.exists(target):
                    return ToolOutput(text=f"{target} not found", error=True)
                found = False
                with open(target, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("#") or "=" not in line:
                            continue
                        if line.split("=")[0].strip() == key:
                            found = True
                            break
                return ToolOutput(
                    text=f"Key '{key}': {'found' if found else 'not found'}",
                    metadata={"key": key, "found": found},
                )

            elif op == "get_env":
                if not key:
                    return ToolOutput(text="key parameter is required", error=True)
                target = env_file or ".env"
                if not os.path.exists(target):
                    return ToolOutput(text=f"{target} not found", error=True)
                with open(target, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        if k.strip() == key:
                            return ToolOutput(
                                text=f"Key '{key}' found and set (value hidden)",
                                metadata={
                                    "key": key,
                                    "found": True,
                                    "length": len(v.strip()),
                                },
                            )
                return ToolOutput(text=f"Key '{key}' not found", error=True)

            else:
                return ToolOutput(
                    text=f"Secret operation '{op}' not implemented. Use list_env, check_env, or get_env.",
                    error=True,
                )

        except Exception as e:
            return ToolOutput(text=f"Error: {e}", error=True)


register_tool(SQLiteTool())
register_tool(CSVTool())
register_tool(SecretsTool())
