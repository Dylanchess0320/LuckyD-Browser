"""
WebMCP client tools — discover and call site-exposed agent tools.

WebMCP (Web Model Context Protocol, proposed at Google I/O 2026) lets
websites expose structured tools to browser agents via
``navigator.modelContext`` — an imperative API (``registerTool()`` with
typed parameters) and a declarative API (annotated HTML forms). Calling a
WebMCP tool replaces dozens of brittle click/type/screenshot interactions
with one typed function call.

These tools run against the agent's Playwright page (tools/browser_tools).
Discovery is read-only; calling a tool executes the site's own handler in
the page, so it requires approval like any other browser-control action.
"""

from __future__ import annotations

import json
from pathlib import Path

from .base import ToolBase, ToolOutput
from .browser_tools import _get_page
from .registry import register_tool

_SHIM_PATH = Path(__file__).resolve().parent.parent / "static" / "webmcp-shim.js"

# Runs in the page. Defensive across preview-build API drift: tolerates
# listTools/getTools and callTool/invokeTool spellings, and reports which
# surface was actually found.
_DISCOVERY_JS = """async () => {
  const out = { supported: false, shim: null, tools: [], declarative_forms: [], api: {} };
  for (const f of document.querySelectorAll('form[data-webmcp-tool]')) {
    out.declarative_forms.push({
      tool: f.getAttribute('data-webmcp-tool'),
      description: f.getAttribute('data-webmcp-description') || '',
      fields: Array.from(f.querySelectorAll('input[name], select[name], textarea[name]'))
        .map(el => ({ name: el.name, type: el.type || 'text', required: !!el.required })),
    });
  }
  const mc = navigator.modelContext;
  if (!mc) return out;
  out.supported = true;
  out.shim = mc._shim || null;
  out.api = {
    listTools: typeof mc.listTools === 'function',
    getTools: typeof mc.getTools === 'function',
    callTool: typeof mc.callTool === 'function',
    invokeTool: typeof mc.invokeTool === 'function',
    registerTool: typeof mc.registerTool === 'function',
  };
  try {
    let list = [];
    if (typeof mc.listTools === 'function') list = await mc.listTools();
    else if (typeof mc.getTools === 'function') list = await mc.getTools();
    else if (Array.isArray(mc.tools)) list = mc.tools;
    out.tools = (list || []).map(t => ({
      name: t.name, description: t.description || '',
      parameters: t.parameters || t.inputSchema || {},
    }));
  } catch (e) { out.error = String(e); }
  return out;
}"""

_CALL_JS = """async ([name, args]) => {
  const mc = navigator.modelContext;
  if (!mc) throw new Error('WebMCP not supported on this page (no navigator.modelContext)');
  for (const m of ['callTool', 'invokeTool', 'executeTool', 'runTool']) {
    if (typeof mc[m] === 'function') return await mc[m](name, args || {});
  }
  throw new Error('WebMCP: page exposes no tool-invocation method');
}"""


def _parse_args(arguments) -> dict:
    if arguments is None:
        return {}
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        text = arguments.strip()
        if not text:
            return {}
        return json.loads(text)
    raise TypeError(f"arguments must be a dict or JSON string, got {type(arguments).__name__}")


class WebMCPDiscoverTool(ToolBase):
    name = "WebMCPDiscover"
    description = (
        "Discover WebMCP tools exposed by the current page (navigator.modelContext). "
        "Returns each tool's name, description, and typed parameters, plus any "
        "declaratively annotated forms. Read-only — safe to call any time."
    )
    aliases = ["DiscoverSiteTools", "ListWebMCPTools"]
    permission_level = "ALWAYS_ALLOW"
    parameters = {
        "url": {
            "type": "string",
            "description": "Optional URL to navigate to before discovering.",
        },
    }

    async def execute(self, url: str = "") -> ToolOutput:
        try:
            page = await _get_page()
            if url:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            result = await page.evaluate(_DISCOVERY_JS)
        except Exception as e:
            return ToolOutput(text=f"Error: WebMCP discovery failed: {e}", error=True)
        n = len(result.get("tools", []))
        summary = (
            f"WebMCP supported: {result.get('supported')}"
            + (f" (shim {result['shim']})" if result.get("shim") else "")
            + f" — {n} tool(s), {len(result.get('declarative_forms', []))} annotated form(s)."
        )
        return ToolOutput(
            text=summary + "\n" + json.dumps(result, indent=2)[:6000],
            title="WebMCP discovery",
            metadata={"result": result},
        )


class WebMCPCallTool(ToolBase):
    name = "WebMCPCall"
    description = (
        "Call a WebMCP tool exposed by the current page by name with structured "
        "arguments. Prefer this over clicking through the UI whenever the page "
        "exposes the action you need — it is faster and doesn't break when the "
        "page design changes. Runs the site's own handler in the page."
    )
    aliases = ["CallSiteTool", "InvokeWebMCPTool"]
    permission_level = "REQUIRES_APPROVAL"
    parameters = {
        "tool": {"type": "string", "description": "Name of the WebMCP tool to call."},
        "arguments": {
            "type": "string",
            "description": "JSON object of arguments matching the tool's parameters schema.",
        },
    }

    async def execute(self, tool: str, arguments: str = "") -> ToolOutput:
        try:
            args = _parse_args(arguments)
        except (json.JSONDecodeError, TypeError) as e:
            return ToolOutput(text=f"Error: invalid arguments: {e}", error=True)
        try:
            page = await _get_page()
            result = await page.evaluate(_CALL_JS, [tool, args])
        except Exception as e:
            return ToolOutput(text=f"Error: WebMCP call failed: {e}", error=True)
        text = json.dumps(result, indent=2)[:8000] if not isinstance(result, str) else result[:8000]
        return ToolOutput(
            text=f"WebMCP tool '{tool}' returned:\n{text}",
            title=f"WebMCP: {tool}",
            metadata={"tool": tool, "result": result},
        )


class WebMCPShimTool(ToolBase):
    name = "WebMCPShim"
    description = (
        "Inject LuckyD's WebMCP polyfill (navigator.modelContext) into the current "
        "page. Use on pages that don't natively support WebMCP yet — it enables "
        "the full discover/call loop and auto-registers annotated HTML forms "
        "(data-webmcp-tool) as callable tools. Harmless on pages with native support."
    )
    aliases = ["EnableWebMCP"]
    permission_level = "NORMAL"
    parameters = {}

    async def execute(self) -> ToolOutput:
        try:
            shim_js = _SHIM_PATH.read_text(encoding="utf-8")
        except OSError as e:
            return ToolOutput(text=f"Error: cannot read WebMCP shim: {e}", error=True)
        try:
            page = await _get_page()
            await page.evaluate(shim_js)
            result = await page.evaluate(_DISCOVERY_JS)
        except Exception as e:
            return ToolOutput(text=f"Error: WebMCP shim injection failed: {e}", error=True)
        n = len(result.get("tools", []))
        return ToolOutput(
            text=f"WebMCP shim active (supported={result.get('supported')}). "
            f"{n} tool(s) available — use WebMCPDiscover for details.",
            title="WebMCP shim",
            metadata={"result": result},
        )


register_tool(WebMCPDiscoverTool())
register_tool(WebMCPCallTool())
register_tool(WebMCPShimTool())
