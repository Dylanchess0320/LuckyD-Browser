"""
LuckyD Code Harness — REST API connector tool.

Connects to the luckyd-code.exe web server (the "Harness") running on
127.0.0.1:8000 and exposes all its endpoints as a single tool with
sub-actions, letting the agent leverage the commercial exe's 98+ tools,
memory graph, LSP, orchestration, and file operations via HTTP calls.
"""

from __future__ import annotations

import json
import os

import httpx

from .base import ToolBase, ToolOutput
from .registry import register_tool

_HARNESS_HOST = os.environ.get("LUCKYDHOST", "127.0.0.1")
_HARNESS_PORT = os.environ.get("LUCKYDPORT", "8000")
_HARNESS_BASE = f"http://{_HARNESS_HOST}:{_HARNESS_PORT}"


def _url(path):
    return f"{_HARNESS_BASE}{path}"


async def _get(path, timeout=15.0):
    try:
        to = httpx.Timeout(connect=5.0, read=timeout, write=5.0, pool=5.0)
        async with httpx.AsyncClient(timeout=to) as c:
            r = await c.get(_url(path))
            r.raise_for_status()
            if "application/json" in r.headers.get("content-type", ""):
                return r.json(), None
            return r.text, None
    except httpx.HTTPStatusError as e:
        d = ""
        try:
            d = e.response.json().get("detail", str(e))
        except Exception:
            d = str(e)
        return None, f"HTTP {e.response.status_code}: {d}"
    except httpx.RequestError as e:
        return None, f"Cannot reach harness at {_HARNESS_BASE}: {e}"
    except Exception as e:
        return None, str(e)


async def _post(path, body=None, timeout=30.0):
    try:
        to = httpx.Timeout(connect=5.0, read=timeout, write=10.0, pool=5.0)
        async with httpx.AsyncClient(timeout=to) as c:
            r = await c.post(
                _url(path), json=body or {}, headers={"Content-Type": "application/json"}
            )
            r.raise_for_status()
            ct = r.headers.get("content-type", "")
            if "application/json" in ct:
                return r.json(), None
            return r.text, None
    except httpx.HTTPStatusError as e:
        d = ""
        try:
            d = e.response.json().get("detail", str(e))
        except Exception:
            d = str(e)
        return None, f"HTTP {e.response.status_code}: {d}"
    except httpx.RequestError as e:
        return None, f"Cannot reach harness at {_HARNESS_BASE}: {e}"
    except Exception as e:
        return None, str(e)


def _fmt(data, indent=2):
    if isinstance(data, (dict, list)):
        try:
            return json.dumps(data, indent=indent, ensure_ascii=False, default=str)
        except Exception:
            return str(data)
    return str(data)


class HarnessTool(ToolBase):
    """Connect to the LuckyD Code Harness (luckyd-code.exe web server)."""

    name = "Harness"
    description = (
        "Connect to the LuckyD Code Harness (luckyd-code.exe web server) "
        "to use its 98+ tools, file system, memory graph, LSP, and more. "
        "Actions: harness, list_files, read_file, write_file, edit_file, "
        "brain_search, brain_stats, list_tools, list_models, list_tasks, "
        "list_sessions, list_memories, orchestrate, parallel, "
        "get_settings, get_context, get_cost, health."
    )
    aliases = ["HarnessAPI", "LuckyDHarness", "LuckyDAPI", "ExeLink"]
    parameters = {
        "action": {
            "type": "string",
            "description": (
                "Operation: harness, list_files, read_file, write_file, "
                "edit_file, brain_search, brain_stats, list_tools, "
                "list_models, list_tasks, list_sessions, list_memories, "
                "orchestrate, parallel, get_settings, get_context, get_cost, health"
            ),
        },
        "path": {"type": "string", "description": "File/directory path"},
        "content": {"type": "string", "description": "Content to write"},
        "old_string": {"type": "string", "description": "Text to replace"},
        "new_string": {"type": "string", "description": "Replacement text"},
        "query": {"type": "string", "description": "Search query"},
        "task_prompt": {"type": "string", "description": "Task description"},
    }

    async def execute(
        self,
        action="harness",
        path="",
        content="",
        old_string="",
        new_string="",
        query="",
        task_prompt="",
    ):
        try:
            m = {
                "harness": self._harness_status,
                "list_files": self._list_files,
                "read_file": self._read_file,
                "write_file": self._write_file,
                "edit_file": self._edit_file,
                "brain_search": self._brain_search,
                "brain_stats": self._brain_stats,
                "list_tools": self._list_tools,
                "list_models": self._list_models,
                "list_tasks": self._list_tasks,
                "list_sessions": self._list_sessions,
                "list_memories": self._list_memories,
                "orchestrate": self._orchestrate,
                "parallel": self._parallel,
                "get_settings": self._get_settings,
                "get_context": self._get_context,
                "get_cost": self._get_cost,
                "health": self._health,
            }
            fn = m.get(action)
            if fn is None:
                return ToolOutput(text=f"Unknown action: {action}", error=True)
            return await fn(
                path=path,
                content=content,
                old_string=old_string,
                new_string=new_string,
                query=query,
                task_prompt=task_prompt,
            )
        except Exception as e:
            return ToolOutput(text=f"Harness error: {e}", error=True)

    async def _harness_status(self, **kw):
        spec, err = await _get("/openapi.json", timeout=8.0)
        if err:
            _, e2 = await _get("/", timeout=5.0)
            if e2:
                return ToolOutput(
                    text=f"\u274c Harness unreachable at {_HARNESS_BASE}\n{e2}", error=True
                )
        endpoints = []
        if isinstance(spec, dict):
            for p, methods in spec.get("paths", {}).items():
                for m in methods:
                    endpoints.append(f"  {m.upper():6s} {p}")
            endpoints.sort()
        lines = [f"\u2705 **Harness** at {_HARNESS_BASE}", f"**{len(endpoints)} endpoints**"]
        lines.extend(endpoints[:40])
        return ToolOutput(text="\n".join(lines), title="\U0001f50c Harness Status")

    async def _list_files(self, path="", **kw):
        data, err = await _get("/api/files", timeout=10.0)
        if err:
            return ToolOutput(text=f"Error: {err}", error=True)
        files = data.get("files", []) if isinstance(data, dict) else []
        cur = data.get("current", "") if isinstance(data, dict) else ""
        lines = [f"**{cur}**"]
        for f in files:
            n, s, d = f.get("name", "?"), f.get("size", 0), f.get("is_dir", False)
            icon = "📁" if d else "📄"
            name_str = f"{icon} {n}" + ("/" if d else f" ({s}B)")
            lines.append(f"  {name_str}")
        return ToolOutput(text="\n".join(lines), title=f"\U0001f4c1 Files ({len(files)})")

    async def _read_file(self, path="", **kw):
        if not path:
            return ToolOutput(text="Error: path required", error=True)
        data, err = await _post("/api/read-file", {"path": path}, timeout=10.0)
        if err:
            return ToolOutput(text=f"Error: {err}", error=True)
        c = data.get("content", _fmt(data)) if isinstance(data, dict) else str(data)
        return ToolOutput(text=c[:8000], title=f"\U0001f4d6 {path}")

    async def _write_file(self, path="", content="", **kw):
        if not path:
            return ToolOutput(text="Error: path + content required", error=True)
        _, err = await _post("/api/write-file", {"path": path, "content": content}, timeout=10.0)
        if err:
            return ToolOutput(text=f"Error: {err}", error=True)
        return ToolOutput(
            text=f"\u2705 Written {len(content)}B to {path}", title=f"\u270d\ufe0f {path}"
        )

    async def _edit_file(self, path="", old_string="", new_string="", **kw):
        if not path or not old_string:
            return ToolOutput(text="Error: path + old_string required", error=True)
        _, err = await _post(
            "/api/edit-file",
            {"path": path, "old_string": old_string, "new_string": new_string},
            timeout=10.0,
        )
        if err:
            return ToolOutput(text=f"Error: {err}", error=True)
        return ToolOutput(text=f"\u2705 Edited {path}", title=f"\u270f\ufe0f {path}")

    async def _brain_search(self, query="", **kw):
        if not query:
            return ToolOutput(text="Error: query required", error=True)
        data, err = await _get(f"/api/brain/search?q={query}", timeout=15.0)
        if err:
            data, err = await _post("/api/brain/search", {"query": query}, timeout=15.0)
        if err:
            return ToolOutput(text=f"Error: {err}", error=True)
        return ToolOutput(text=_fmt(data)[:6000], title=f"\U0001f9e0 {query[:60]}")

    async def _brain_stats(self, **kw):
        data, err = await _get("/api/brain/stats", timeout=10.0)
        if err:
            data, err = await _get("/api/brain", timeout=10.0)
        if err:
            return ToolOutput(text=f"Error: {err}", error=True)
        return ToolOutput(text=_fmt(data)[:4000], title="\U0001f9e0 Brain Stats")

    async def _list_tools(self, **kw):
        data, err = await _get("/api/tools", timeout=10.0)
        if err:
            return ToolOutput(text=f"Error: {err}", error=True)
        tools = (
            data.get("tools", data.get("data", []))
            if isinstance(data, dict)
            else (data if isinstance(data, list) else [])
        )
        lines = [f"**{len(tools)} tools:**"]
        for t in tools:
            lines.append(f"  \U0001f527 {t.get('name', t) if isinstance(t, dict) else t}")
        return ToolOutput(text="\n".join(lines), title=f"\U0001f527 Tools ({len(tools)})")

    async def _list_models(self, **kw):
        data, err = await _get("/api/models", timeout=10.0)
        if err:
            return ToolOutput(text=f"Error: {err}", error=True)
        return ToolOutput(text=_fmt(data)[:4000], title="\U0001f916 Models")

    async def _list_tasks(self, **kw):
        data, err = await _get("/api/tasks", timeout=10.0)
        if err:
            return ToolOutput(text=f"Error: {err}", error=True)
        return ToolOutput(text=_fmt(data)[:4000], title="\U0001f4cb Tasks")

    async def _list_sessions(self, **kw):
        data, err = await _get("/api/sessions", timeout=10.0)
        if err:
            return ToolOutput(text=f"Error: {err}", error=True)
        return ToolOutput(text=_fmt(data)[:4000], title="\U0001f4be Sessions")

    async def _list_memories(self, **kw):
        data, err = await _get("/api/memories", timeout=10.0)
        if err:
            data, err = await _get("/api/memory", timeout=10.0)
        if err:
            return ToolOutput(text=f"Error: {err}", error=True)
        return ToolOutput(text=_fmt(data)[:4000], title="\U0001f4ad Memories")

    async def _orchestrate(self, task_prompt="", **kw):
        if not task_prompt:
            return ToolOutput(text="Error: task_prompt required", error=True)
        data, err = await _post("/api/orchestrate", {"task": task_prompt}, timeout=120.0)
        if err:
            return ToolOutput(text=f"Error: {err}", error=True)
        return ToolOutput(text=_fmt(data)[:8000], title="\U0001f3ad Orchestration")

    async def _parallel(self, task_prompt="", **kw):
        if not task_prompt:
            return ToolOutput(text="Error: task_prompt required", error=True)
        data, err = await _post("/api/parallel", {"task": task_prompt}, timeout=120.0)
        if err:
            return ToolOutput(text=f"Error: {err}", error=True)
        return ToolOutput(text=_fmt(data)[:8000], title="\u26a1 Parallel")

    async def _get_settings(self, **kw):
        data, err = await _get("/api/settings", timeout=10.0)
        if err:
            return ToolOutput(text=f"Error: {err}", error=True)
        return ToolOutput(text=_fmt(data)[:4000], title="\u2699\ufe0f Settings")

    async def _get_context(self, **kw):
        data, err = await _get("/api/context", timeout=10.0)
        if err:
            return ToolOutput(text=f"Error: {err}", error=True)
        return ToolOutput(text=_fmt(data)[:4000], title="\U0001f4d0 Context")

    async def _get_cost(self, **kw):
        data, err = await _get("/api/cost", timeout=10.0)
        if err:
            return ToolOutput(text=f"Error: {err}", error=True)
        return ToolOutput(text=_fmt(data)[:2000], title="\U0001f4b0 Cost")

    async def _health(self, **kw):
        data, err = await _get("/health", timeout=5.0)
        if err:
            return ToolOutput(text=f"Health failed: {err}", error=True)
        return ToolOutput(text=_fmt(data)[:2000], title="\U0001f49a Health")


register_tool(HarnessTool())
