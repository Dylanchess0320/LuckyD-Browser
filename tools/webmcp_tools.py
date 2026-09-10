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

Tab/origin binding: a page must never discover or invoke tools registered
by another origin. Discovery records each tool's schema under the page's
origin and returns an opaque binding token; a call is dispatched only when
the token matches the current page's origin *and* the tool was discovered
on that origin. Navigating cross-origin purges the stale registration.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .base import ToolBase, ToolOutput
from .browser_tools import _get_page
from .registry import register_tool

_SHIM_PATH = Path(__file__).resolve().parent.parent / "static" / "webmcp-shim.js"

# Bound on how long a page-side tool handler may run before we give up.
# A malicious (or just broken) page must not be able to hang the agent.
_EVALUATE_TIMEOUT_MS = 30000

# Caps on page-supplied discovery data — a hostile page could otherwise hand
# us megabytes of schema to chew on.
_MAX_TOOLS_PER_ORIGIN = 200
_MAX_SCHEMA_KEYS = 200
_MAX_TEXT_LEN = 2000


# ── Tab/origin binding registry ─────────────────────────────────────────
# Keyed by (tab_id, origin). LuckyD's browser currently drives a single tab,
# so current_tab_id() always returns "default" — but every lookup goes
# through it, so multi-tab support only has to start returning the real
# active tab id here: bindings are already isolated per tab, one tab's
# navigation can never purge or steal another tab's registrations, and a
# binding token discovered in tab A is refused in tab B.

_DEFAULT_TAB_ID = "default"


@dataclass
class _OriginBinding:
    """Server-side record of WebMCP tools discovered on one page origin."""

    origin: str
    binding_token: str  # opaque, unguessable; rotated on every discovery
    tools: dict[str, dict] = field(default_factory=dict)  # tool name -> parameters schema


_BINDINGS: dict[tuple[str, str], _OriginBinding] = {}
_last_origin: dict[str, str] = {}
_current_tab_id: str = _DEFAULT_TAB_ID


def current_tab_id() -> str:
    """Identifier of the tab the agent is currently driving.

    Single-tab today: always "default". When the browser gains tabs this
    becomes the hook where the real active-tab id (e.g. from browser_tools'
    page handle) is returned; all binding lookups key on (tab_id, origin)
    already, so no call-site changes will be needed.
    """
    return _current_tab_id


def set_current_tab_id(tab_id: str) -> None:
    """Override the active tab id. Test/support hook; the future multi-tab
    browser core will drive this instead."""
    global _current_tab_id
    _current_tab_id = tab_id or _DEFAULT_TAB_ID


def _tab_key(tab_id: str, origin_key: str) -> tuple[str, str]:
    return (tab_id, origin_key)


def _origin_of_url(url: str | None) -> str:
    """Canonical origin for a page URL, or "" when it has no http(s) origin."""
    try:
        parts = urlsplit(url or "")
    except Exception:
        return ""
    if parts.scheme not in ("http", "https"):
        return ""
    host = (parts.hostname or "").lower()
    if not host:
        return ""
    port = parts.port
    default = 443 if parts.scheme == "https" else 80
    netloc = host if port in (None, default) else f"{host}:{port}"
    return f"{parts.scheme}://{netloc}"


def _binding_key(page_url: str | None) -> str:
    """Binding key for the current page: its origin, or the raw URL for
    origin-less pages (about:blank, data:, ...) which have nothing to confuse."""
    return _origin_of_url(page_url) or f"url:{page_url or ''}"


def reset_webmcp_bindings() -> None:
    """Drop all WebMCP tab/origin bindings. Test/support hook."""
    _BINDINGS.clear()
    _last_origin.clear()
    global _current_tab_id
    _current_tab_id = _DEFAULT_TAB_ID


def _sync_origin_binding(tab_id: str, page_url: str | None) -> None:
    """Purge stale registrations when a tab navigates cross-origin.

    Called before every discover/call so a binding can never outlive the
    page whose origin registered it. Per-tab: one tab's navigation never
    touches another tab's bindings.
    """
    key = _binding_key(page_url)
    prev = _last_origin.get(tab_id)
    if prev is not None and key != prev:
        _BINDINGS.pop(_tab_key(tab_id, prev), None)
    _last_origin[tab_id] = key


def _register_discovery(tab_id: str, key: str, tools: list[dict]) -> str:
    """Record a discovery result; returns a fresh opaque binding token."""
    token = secrets.token_urlsafe(24)
    _BINDINGS[_tab_key(tab_id, key)] = _OriginBinding(
        origin=key,
        binding_token=token,
        tools={t["name"]: t["parameters"] for t in tools},
    )
    return token


def _check_binding(key: str, tool: str, binding_token: str) -> tuple[_OriginBinding | None, str]:
    """Verify a call is bound to the tab+origin that registered the tool.

    The tab is the agent's currently-driven tab (current_tab_id()); in the
    single-tab browser this is always "default", so the signature stays
    origin-keyed for callers.

    Returns (binding, "") on success, (None, reason) on refusal.
    """
    binding = _BINDINGS.get(_tab_key(current_tab_id(), key))
    if binding is None:
        return None, (
            "No WebMCP tools have been discovered on this page's origin. "
            "Run WebMCPDiscover first — tools are bound to the origin that "
            "registered them and cannot be called across origins."
        )
    if not binding_token or not secrets.compare_digest(binding_token, binding.binding_token):
        return None, (
            "Invalid or missing binding token for this page's origin. A WebMCP "
            "tool call must carry the binding token returned by WebMCPDiscover "
            "on this exact page/origin; cross-origin and replayed calls are refused."
        )
    if tool not in binding.tools:
        return None, (
            f"Unknown WebMCP tool '{tool}' on this page's origin. Only tools "
            "reported by WebMCPDiscover for the current origin can be called."
        )
    return binding, ""


# ── Defensive page-data handling ────────────────────────────────────────


def _coerce_discovery(raw: Any) -> dict:
    """Coerce whatever the page returned into a safe discovery shape.

    A hostile page may return non-objects, giant blobs, or malformed tool
    entries; none of that may crash the agent or poison the binding registry.
    """
    if not isinstance(raw, dict):
        return {
            "supported": False,
            "tools": [],
            "declarative_forms": [],
            "error": "page returned a non-object discovery result",
        }
    tools: list[dict] = []
    raw_tools = raw.get("tools")
    if isinstance(raw_tools, list):
        for t in raw_tools[:_MAX_TOOLS_PER_ORIGIN]:
            if not isinstance(t, dict):
                continue
            name = t.get("name")
            if not isinstance(name, str) or not name:
                continue
            params = t.get("parameters") or t.get("inputSchema") or {}
            params = {} if not isinstance(params, dict) else _cap_schema(params)
            tools.append(
                {
                    "name": name[:128],
                    "description": str(t.get("description") or "")[:_MAX_TEXT_LEN],
                    "parameters": params,
                }
            )
    forms: list[dict] = []
    raw_forms = raw.get("declarative_forms")
    if isinstance(raw_forms, list):
        for f in raw_forms[:_MAX_TOOLS_PER_ORIGIN]:
            if isinstance(f, dict) and isinstance(f.get("tool"), str) and f["tool"]:
                forms.append(
                    {
                        "tool": f["tool"][:128],
                        "description": str(f.get("description") or "")[:_MAX_TEXT_LEN],
                        "fields": f.get("fields") if isinstance(f.get("fields"), list) else [],
                    }
                )
    out = {
        "supported": bool(raw.get("supported")),
        "shim": raw.get("shim") if isinstance(raw.get("shim"), str) else None,
        "tools": tools,
        "declarative_forms": forms,
    }
    if raw.get("error") is not None:
        out["error"] = str(raw.get("error"))[:_MAX_TEXT_LEN]
    return out


def _cap_schema(schema: dict) -> dict:
    """Trim a page-supplied parameter schema to sane bounds."""
    props = schema.get("properties")
    if isinstance(props, dict) and len(props) > _MAX_SCHEMA_KEYS:
        schema = dict(schema)
        schema["properties"] = dict(list(props.items())[:_MAX_SCHEMA_KEYS])
    return schema


def _validate_args(schema: Any, args: Any) -> str | None:
    """Validate tool arguments against the schema captured at discovery.

    Mirrors the shim's in-page validation so a native (non-shim) page that
    skips validation can't receive malformed input. Returns an error message
    or None when the arguments are acceptable.
    """
    if not isinstance(schema, dict) or not isinstance(args, dict):
        return None
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    required = schema.get("required")
    if not isinstance(required, list):
        required = []
    for key in required:
        if not isinstance(key, str):
            continue
        value = args.get(key)
        if value is None or value == "":
            return f"missing required parameter: {key}"
    for name, spec in properties.items():
        if not isinstance(spec, dict):
            continue
        if name not in args or args[name] is None:
            continue
        want = spec.get("type")
        value = args[name]
        ok = True
        if want == "string":
            ok = isinstance(value, str)
        elif want == "boolean":
            ok = isinstance(value, bool)
        elif want == "integer":
            ok = isinstance(value, int) and not isinstance(value, bool)
        elif want == "number":
            ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        elif want == "array":
            ok = isinstance(value, list)
        elif want == "object":
            ok = isinstance(value, dict)
        if not ok:
            return f"parameter '{name}' must be {want}"
    return None


def _safe_json(value: Any, limit: int) -> str:
    """Serialize page-supplied data defensively; never raise, always truncate."""
    try:
        return json.dumps(value, indent=2, default=str)[:limit]
    except Exception:
        try:
            return str(value)[:limit]
        except Exception:
            return "<unserializable page result>"


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


async def _discover_on_page(page) -> tuple[dict, str, str]:
    """Run discovery on a page, bind the result to the (tab, origin).

    Returns (sanitized_result, binding_token, binding_key).
    """
    tab_id = current_tab_id()
    page_url = getattr(page, "url", "") or ""
    _sync_origin_binding(tab_id, page_url)
    key = _binding_key(page_url)
    raw = await page.evaluate(_DISCOVERY_JS, timeout=_EVALUATE_TIMEOUT_MS)
    result = _coerce_discovery(raw)
    token = _register_discovery(tab_id, key, result["tools"])
    return result, token, key


class WebMCPDiscoverTool(ToolBase):
    name = "WebMCPDiscover"
    description = (
        "Discover WebMCP tools exposed by the current page (navigator.modelContext). "
        "Returns each tool's name, description, and typed parameters, plus any "
        "declaratively annotated forms, plus a binding token. The binding token "
        "is bound to this page's origin and must be passed to WebMCPCall — "
        "tools cannot be discovered on one origin and called on another. "
        "Read-only — safe to call any time."
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
            result, token, key = await _discover_on_page(page)
        except Exception as e:
            return ToolOutput(text=f"Error: WebMCP discovery failed: {e}", error=True)
        n = len(result["tools"])
        summary = (
            f"WebMCP supported: {result.get('supported')}"
            + (f" (shim {result['shim']})" if result.get("shim") else "")
            + f" — {n} tool(s), {len(result['declarative_forms'])} annotated form(s)."
            + f"\nOrigin binding: {key}"
            + f"\nBinding token (pass to WebMCPCall): {token}"
        )
        return ToolOutput(
            text=summary + "\n" + _safe_json(result, 6000),
            title="WebMCP discovery",
            metadata={"result": result, "binding_token": token, "origin": key},
        )


class WebMCPCallTool(ToolBase):
    name = "WebMCPCall"
    description = (
        "Call a WebMCP tool exposed by the current page by name with structured "
        "arguments. Prefer this over clicking through the UI whenever the page "
        "exposes the action you need — it is faster and doesn't break when the "
        "page design changes. Runs the site's own handler in the page. The "
        "binding_token from WebMCPDiscover is required: it proves the tool was "
        "discovered on this page's origin, and the call is refused otherwise."
    )
    aliases = ["CallSiteTool", "InvokeWebMCPTool"]
    permission_level = "REQUIRES_APPROVAL"
    parameters = {
        "tool": {"type": "string", "description": "Name of the WebMCP tool to call."},
        "arguments": {
            "type": "string",
            "description": "JSON object of arguments matching the tool's parameters schema.",
        },
        "binding_token": {
            "type": "string",
            "description": (
                "Opaque token returned by WebMCPDiscover for this page's origin. "
                "Calls without a token matching the current origin are refused."
            ),
        },
    }

    async def execute(self, tool: str, arguments: str = "", binding_token: str = "") -> ToolOutput:
        try:
            args = _parse_args(arguments)
        except (json.JSONDecodeError, TypeError) as e:
            return ToolOutput(text=f"Error: invalid arguments: {e}", error=True)
        try:
            page = await _get_page()
            page_url = getattr(page, "url", "") or ""
            tab_id = current_tab_id()
            _sync_origin_binding(tab_id, page_url)
            key = _binding_key(page_url)
            binding, refusal = _check_binding(key, tool, binding_token or "")
            if binding is None:
                return ToolOutput(text=f"Error: WebMCP call refused: {refusal}", error=True)
            schema_error = _validate_args(binding.tools.get(tool), args)
            if schema_error:
                return ToolOutput(
                    text=f"Error: WebMCP call refused: invalid arguments: {schema_error}",
                    error=True,
                )
            result = await page.evaluate(_CALL_JS, [tool, args], timeout=_EVALUATE_TIMEOUT_MS)
        except Exception as e:
            return ToolOutput(text=f"Error: WebMCP call failed: {e}", error=True)
        text = _safe_json(result, 8000)
        return ToolOutput(
            text=f"WebMCP tool '{tool}' returned:\n{text}",
            title=f"WebMCP: {tool}",
            metadata={"tool": tool, "result": result, "origin": key},
        )


class WebMCPShimTool(ToolBase):
    name = "WebMCPShim"
    description = (
        "Inject LuckyD's WebMCP polyfill (navigator.modelContext) into the current "
        "page. Use on pages that don't natively support WebMCP yet — it enables "
        "the full discover/call loop and auto-registers annotated HTML forms "
        "(data-webmcp-tool) as callable tools. Harmless on pages with native support. "
        "Also returns a binding token for WebMCPCall, like WebMCPDiscover."
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
            await page.evaluate(shim_js, timeout=_EVALUATE_TIMEOUT_MS)
            result, token, key = await _discover_on_page(page)
        except Exception as e:
            return ToolOutput(text=f"Error: WebMCP shim injection failed: {e}", error=True)
        n = len(result["tools"])
        return ToolOutput(
            text=f"WebMCP shim active (supported={result.get('supported')}). "
            f"{n} tool(s) available — use WebMCPDiscover for details."
            f"\nOrigin binding: {key}"
            f"\nBinding token (pass to WebMCPCall): {token}",
            title="WebMCP shim",
            metadata={"result": result, "binding_token": token, "origin": key},
        )


register_tool(WebMCPDiscoverTool())
register_tool(WebMCPCallTool())
register_tool(WebMCPShimTool())
