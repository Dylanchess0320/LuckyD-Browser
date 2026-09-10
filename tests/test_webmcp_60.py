"""Tests for LuckyD 6.0 WebMCP support — tools, permissions, and the shim."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

import core.trust as trust
from core.approval_hook import _TOOL_PERMISSIONS
from core.types import ToolPermissionLevel

REPO = Path(__file__).resolve().parent.parent
SHIM = REPO / "static" / "webmcp-shim.js"


@pytest.fixture(scope="module")
def _webmcp_tools():
    import tools.webmcp_tools  # noqa: F401  (registers tools on import)
    from tools.registry import registry

    return registry


class TestToolRegistration:
    def test_tools_registered(self, _webmcp_tools):
        for name in ("WebMCPDiscover", "WebMCPCall", "WebMCPShim"):
            assert _webmcp_tools.get(name) is not None, name

    def test_aliases(self, _webmcp_tools):
        assert _webmcp_tools.get("DiscoverSiteTools") is not None
        assert _webmcp_tools.get("CallSiteTool") is not None

    def test_permission_levels(self):
        assert _TOOL_PERMISSIONS["WebMCPDiscover"] == ToolPermissionLevel.ALWAYS_ALLOW
        assert _TOOL_PERMISSIONS["WebMCPCall"] == ToolPermissionLevel.REQUIRES_APPROVAL
        assert _TOOL_PERMISSIONS["WebMCPShim"] == ToolPermissionLevel.NORMAL

    def test_browser_scope(self):
        # "Web*" prefix would map to network — WebMCP tools drive live tabs.
        for name in ("WebMCPDiscover", "WebMCPCall", "WebMCPShim"):
            assert trust.scope_of(name) == "browser", name
        assert trust.risk_of("WebMCPCall") == "high"

    def test_describe_decision(self):
        s = trust.describe_decision("WebMCPCall", {"tool": "search_products"})
        assert "WebMCPCall" in s


class TestShimSyntax:
    def test_node_check(self):
        proc = subprocess.run(["node", "--check", str(SHIM)], capture_output=True, timeout=30)
        assert proc.returncode == 0, proc.stderr.decode()


_NODE_PREAMBLE = r"""
// ---- minimal browser stubs ----
global.CSS = { escape: (s) => s };
global.Event = class Event { constructor(t, o) { this.type = t; } };
global.FormData = class FormData {
  constructor(form) { this._form = form; }
  forEach(cb) { for (const f of this._form._fields) cb(f.value, f.name); }
};
function fakeField(name, type, required) {
  return { name, type, required: !!required, value: "",
    getAttribute: () => null, dispatchEvent() {} };
}
function fakeForm() {
  const fields = [fakeField("email", "email", true), fakeField("name", "text", false)];
  return {
    _fields: fields,
    getAttribute: (k) => k === "data-webmcp-tool" ? "newsletter"
      : k === "data-webmcp-description" ? "Subscribe to newsletter" : null,
    querySelectorAll: () => fields,
    querySelector: (sel) => {
      const m = String(sel).match(/\[name="([^"]+)"\]/);
      return fields.find((f) => f.name === (m && m[1])) || null;
    },
    action: "",
  };
}
global.document = {
  readyState: "complete",
  querySelectorAll: (sel) => sel === "form[data-webmcp-tool]" ? [fakeForm()] : [],
  addEventListener: () => {},
};
global.navigator = {};
"""


def _run_shim_scenario(body: str, predefine_native: bool = False) -> dict:
    shim_src = SHIM.read_text(encoding="utf-8")
    native = "global.navigator.modelContext = { native: true };\n" if predefine_native else ""
    script = (
        _NODE_PREAMBLE
        + native
        + shim_src
        + "\n"
        + ";(async () => {\n"
        + "const out = {};\n"
        + "try {\n"
        + body
        + "\n"
        + "} catch (e) { out.fatal = String(e && e.stack || e); }\n"
        + "console.log(JSON.stringify(out));\n"
        + "})();\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        proc = subprocess.run(["node", path], capture_output=True, timeout=30)
    finally:
        Path(path).unlink(missing_ok=True)
    assert proc.returncode == 0, proc.stderr.decode()
    return json.loads(proc.stdout.decode())


class TestShimBehavior:
    def test_register_list_call_roundtrip(self):
        out = _run_shim_scenario(
            """
            const mc = navigator.modelContext;
            out.hasMC = !!mc;
            await mc.registerTool({
              name: "add",
              description: "Add two numbers",
              parameters: { type: "object",
                properties: { a: { type: "number" }, b: { type: "number" } },
                required: ["a", "b"] },
              execute: async (args) => args.a + args.b,
            });
            const tools = await mc.listTools();
            out.listed = tools.map(t => t.name);
            out.desc = tools.find(t => t.name === "add").description;
            out.sum = await mc.callTool("add", { a: 2, b: 3 });
            """
        )
        assert out["hasMC"] is True
        assert "add" in out["listed"]
        assert out["desc"] == "Add two numbers"
        assert out["sum"] == 5

    def test_schema_validation(self):
        out = _run_shim_scenario(
            """
            const mc = navigator.modelContext;
            await mc.registerTool({
              name: "add",
              parameters: { type: "object",
                properties: { a: { type: "number" }, b: { type: "integer" } },
                required: ["a", "b"] },
              execute: async (args) => args.a + args.b,
            });
            try { await mc.callTool("add", { a: 1 }); out.missing = "no-throw"; }
            catch (e) { out.missing = String(e); }
            try { await mc.callTool("add", { a: 1, b: 1.5 }); out.badtype = "no-throw"; }
            catch (e) { out.badtype = String(e); }
            try { await mc.callTool("nope", {}); out.unknown = "no-throw"; }
            catch (e) { out.unknown = String(e); }
            """
        )
        assert "missing required parameter: b" in out["missing"]
        assert "must be an integer" in out["badtype"]
        assert "unknown tool" in out["unknown"]

    def test_register_validation(self):
        out = _run_shim_scenario(
            """
            const mc = navigator.modelContext;
            try { await mc.registerTool({ description: "x" }); out.noname = "no-throw"; }
            catch (e) { out.noname = String(e); }
            try { await mc.registerTool({ name: "x" }); out.noexec = "no-throw"; }
            catch (e) { out.noexec = String(e); }
            """
        )
        assert "def.name" in out["noname"]
        assert "def.execute" in out["noexec"]

    def test_declarative_form_auto_registration(self):
        out = _run_shim_scenario(
            """
            const mc = navigator.modelContext;
            const tools = await mc.listTools();
            const nl = tools.find(t => t.name === "newsletter");
            out.found = !!nl;
            out.description = nl && nl.description;
            out.emailRequired = nl && nl.parameters.required.includes("email");
            const res = await mc.callTool("newsletter", { email: "a@b.c", name: "Al" });
            out.submitted = res.submitted;
            """
        )
        assert out["found"] is True
        assert out["description"] == "Subscribe to newsletter"
        assert out["emailRequired"] is True
        assert out["submitted"]["email"] == "a@b.c"
        assert out["submitted"]["name"] == "Al"

    def test_native_support_wins(self):
        out = _run_shim_scenario(
            "out.native = !!navigator.modelContext.native;", predefine_native=True
        )
        assert out.get("native") is True

    def test_double_load_safe(self):
        out = _run_shim_scenario(
            """
            const first = navigator.modelContext;
            out.stillThere = !!first;
            """
        )
        assert out["stillThere"] is True
