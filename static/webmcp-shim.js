/* LuckyD WebMCP shim — navigator.modelContext polyfill (proposal preview).
 *
 * WebMCP (Web Model Context Protocol, Google I/O 2026) lets sites expose
 * structured tools to browser agents via `navigator.modelContext`. Native
 * support is still in origin trial, so this shim implements the proposal's
 * imperative API surface — registerTool / listTools / callTool — plus
 * declarative form auto-registration, so any page (and LuckyD's agent)
 * can exercise the full WebMCP loop today. If the browser ever ships
 * native support, the shim steps aside automatically.
 *
 * Imperative example (in page JS):
 *   await navigator.modelContext.registerTool({
 *     name: "search_products",
 *     description: "Search the product catalog",
 *     parameters: { type: "object",
 *                   properties: { query: { type: "string" } },
 *                   required: ["query"] },
 *     execute: async (args) => ({ results: [...] }),
 *   });
 *
 * Declarative example (annotated HTML form — no JS needed):
 *   <form data-webmcp-tool="newsletter_signup"
 *         data-webmcp-description="Subscribe to the newsletter">
 *     <input name="email" type="email" required>
 *   </form>
 */
(function () {
  "use strict";
  if (typeof navigator === "undefined") return;
  if (navigator.modelContext) return; // native support wins

  var tools = new Map();

  function describeTool(t) {
    return {
      name: t.name,
      description: t.description || "",
      parameters: t.parameters || { type: "object", properties: {} },
    };
  }

  // Minimal JSON-Schema validation: required fields + primitive types.
  function validate(schema, args) {
    schema = schema || {};
    args = args || {};
    var props = schema.properties || {};
    for (var i = 0; i < (schema.required || []).length; i++) {
      var key = schema.required[i];
      if (args[key] === undefined || args[key] === null || args[key] === "") {
        return "missing required parameter: " + key;
      }
    }
    for (var name in props) {
      if (args[name] === undefined || args[name] === null) continue;
      var want = (props[name] || {}).type;
      var got = Array.isArray(args[name]) ? "array" : typeof args[name];
      if (want === "integer" && !(got === "number" && Number.isInteger(args[name]))) {
        return "parameter '" + name + "' must be an integer";
      }
      if ((want === "number" || want === "string" || want === "boolean" || want === "array") && got !== want) {
        return "parameter '" + name + "' must be " + want;
      }
    }
    return null;
  }

  // Declarative API: auto-register annotated forms as tools.
  function registerDeclarativeForms(root) {
    var scope = root || (typeof document !== "undefined" ? document : null);
    if (!scope || !scope.querySelectorAll) return;
    var forms = scope.querySelectorAll("form[data-webmcp-tool]");
    for (var fi = 0; fi < forms.length; fi++) {
      (function (form) {
        var name = form.getAttribute("data-webmcp-tool");
        if (!name || tools.has(name)) return;
        var props = {};
        var required = [];
        var fields = form.querySelectorAll("input[name], select[name], textarea[name]");
        for (var i = 0; i < fields.length; i++) {
          var el = fields[i];
          var type = "string";
          if (el.type === "number" || el.type === "range") type = "number";
          else if (el.type === "checkbox") type = "boolean";
          props[el.name] = {
            type: type,
            description: el.getAttribute("data-webmcp-description") || el.name,
          };
          if (el.required) required.push(el.name);
        }
        tools.set(name, {
          name: name,
          description:
            form.getAttribute("data-webmcp-description") || "Submit form '" + name + "'",
          parameters: { type: "object", properties: props, required: required },
          execute: async function (args) {
            args = args || {};
            for (var key in args) {
              var input = null;
              try {
                input = form.querySelector('[name="' + key.replace(/"/g, "") + '"]');
              } catch (e) {
                input = null;
              }
              if (input && "value" in input) {
                input.value = args[key];
                input.dispatchEvent(new Event("input", { bubbles: true }));
                input.dispatchEvent(new Event("change", { bubbles: true }));
              }
            }
            var data = {};
            try {
              new FormData(form).forEach(function (v, k) {
                data[k] = v;
              });
            } catch (e) {
              data = args;
            }
            // Report what would be submitted instead of navigating away while
            // an agent is driving the page.
            return {
              submitted: data,
              action: form.getAttribute("action") || "",
              method: (form.getAttribute("method") || "get").toLowerCase(),
            };
          },
        });
      })(forms[fi]);
    }
  }

  var mc = {
    // Imperative API — mirrors the proposal's registerTool().
    registerTool: async function (def) {
      if (!def || typeof def.name !== "string" || !def.name) {
        throw new TypeError("registerTool: def.name (string) is required");
      }
      var handler = def.execute || def.handler;
      if (typeof handler !== "function") {
        throw new TypeError("registerTool: def.execute (function) is required");
      }
      tools.set(def.name, {
        name: def.name,
        description: def.description || "",
        parameters: def.parameters || def.inputSchema || { type: "object", properties: {} },
        execute: handler,
      });
      // Pick up any annotated forms that appeared since boot.
      registerDeclarativeForms();
      return { ok: true, name: def.name };
    },
    unregisterTool: async function (name) {
      return { ok: tools.delete(name) };
    },
    listTools: async function () {
      registerDeclarativeForms();
      return Array.from(tools.values()).map(describeTool);
    },
    callTool: async function (name, args) {
      var t = tools.get(name);
      if (!t) throw new Error("WebMCP: unknown tool '" + name + "'");
      var err = validate(t.parameters, args || {});
      if (err) throw new TypeError("WebMCP: invalid arguments: " + err);
      return await t.execute(args || {});
    },
    // Aliases tolerated for spec-drift between preview builds.
    getTools: async function () {
      return mc.listTools();
    },
    invokeTool: async function (name, args) {
      return mc.callTool(name, args);
    },
    _shim: "luckyd-webmcp/1.0",
    _resetForTests: function () {
      tools.clear();
    },
  };

  try {
    Object.defineProperty(navigator, "modelContext", {
      value: mc,
      configurable: true,
      writable: false,
    });
  } catch (e) {
    navigator.modelContext = mc;
  }

  if (typeof document !== "undefined") {
    var boot = function () {
      try {
        registerDeclarativeForms(document);
      } catch (e) {
        /* never break the page */
      }
    };
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", boot);
    } else {
      boot();
    }
  }
})();
